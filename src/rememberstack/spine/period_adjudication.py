"""D140 §8.2: in-force periods as an adjudication input, and when to look again.

A claim that occurs in versions with declared effective periods is shown to
fact adjudication with its **in-force intervals**: the union of the in-force
intervals of the versions it occurs in (in their current reading, as currently
believed). Adjudication may use them like a claim's own dates; D118's single
fact window stays the only fact-time authority.

When those intervals change after a fact was adjudicated (a correction, a
retraction, a clear, a new version that ends its predecessor, a deletion), the
fact's window is reviewed. The **period basis** is the fingerprint that makes
this cheap and repeatable: a hash of the fact's evidence claims and their
in-force intervals. It is stored on the fact whenever its window is adjudicated;
a fact whose current basis differs from the stored one needs review, and a fact
whose basis did not change never costs a model call.

Lineages that never declared a period contribute nothing here: their claims
carry no intervals, their facts have no basis, and nothing is ever queued for
them, so an undated corpus adjudicates exactly as before.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from datetime import UTC
from typing import Any
from typing import Literal
from uuid import UUID
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import Connection

FACT_WINDOW_REVIEW_VERSION = "fact-window-review-2026.10a"
"""Component version of the ``review_fact_windows`` stage (fan-out and review)."""

FactKind = Literal["relation", "observation"]

_FACT_TABLES: dict[str, tuple[str, str, str]] = {
    "relation": ("relations", "relation_id", "relation_evidence"),
    "observation": ("observations", "observation_id", "observation_evidence"),
}

# The occurrences of a set of claims in live versions' current readings, with
# their version's in-force intervals. document_version_scope holds exactly the
# non-deleted versions of live lineages, as currently believed.
_CLAIM_OCCURRENCES = """
SELECT cc.claim_id, s.periodised, s.in_force
FROM chunk_claims cc
JOIN chunks ch
  ON ch.deployment_id = cc.deployment_id AND ch.chunk_id = cc.chunk_id
JOIN document_versions v
  ON v.deployment_id = ch.deployment_id AND v.version_id = ch.version_id
 AND v.current_representation_id = ch.representation_id
JOIN document_version_scope s
  ON s.deployment_id = v.deployment_id AND s.version_id = v.version_id
WHERE cc.deployment_id = :deployment_id
  AND cc.claim_id = ANY(CAST(:claim_ids AS uuid[]))
"""

_CLAIM_INTERVALS = text(
    f"""
WITH occurrence AS ({_CLAIM_OCCURRENCES}),
claim_union AS (
  SELECT o.claim_id,
         bool_or(o.periodised) AS periodised,
         bool_or(NOT o.periodised) AS also_undated,
         range_agg(r) FILTER (WHERE o.periodised AND r IS NOT NULL) AS in_force
  FROM occurrence o
  LEFT JOIN LATERAL unnest(o.in_force) AS r ON true
  GROUP BY o.claim_id
)
SELECT u.claim_id,
       u.also_undated,
       (SELECT coalesce(
                 jsonb_agg(
                   jsonb_build_array(
                     to_char(lower(x) AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US'),
                     to_char(upper(x) AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US'))
                   ORDER BY lower(x)),
                 '[]'::jsonb)
        FROM unnest(coalesce(u.in_force, '{{}}'::tstzmultirange)) AS x) AS in_force
FROM claim_union u
WHERE u.periodised
"""
)


def _instant(raw: str | None) -> str | None:
    """Render a UTC wall-clock string from SQL as an ISO instant (None = open)."""
    if raw is None:
        return None
    return datetime.fromisoformat(raw).replace(tzinfo=UTC).isoformat()


def claim_in_force_on(
    *, connection: Connection, deployment_id: UUID, claim_ids: Iterable[UUID]
) -> dict[str, dict[str, Any]]:
    """In-force intervals of each claim that occurs in a periodised lineage.

    Keys are claim ids as strings. Each value is ``{"in_force": [{"from": ISO,
    "until": ISO or None}], "also_undated": bool}``: half-open UTC intervals
    (``until`` exclusive; None = still in force), and whether the same text also
    occurs in a lineage without declared periods. A claim that occurs only in
    undated lineages is absent, so callers add nothing for it. An empty list
    means the claim occurs only in versions never in force.
    """
    ids = sorted({str(claim_id) for claim_id in claim_ids})
    if not ids or not deployment_ever_periodised_on(
        connection=connection, deployment_id=deployment_id
    ):
        return {}
    rows = connection.execute(
        _CLAIM_INTERVALS, {"deployment_id": deployment_id, "claim_ids": ids}
    ).mappings()
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        intervals = row["in_force"] or []
        result[str(row["claim_id"])] = {
            "in_force": [
                {"from": _instant(start), "until": _instant(end)}
                for start, end in intervals
            ],
            "also_undated": bool(row["also_undated"]),
        }
    return result


# A fact's period basis, computed set-wise in the database so a fact with many
# evidence claims never materialises them in the worker: per evidence claim
# (both stances) that occurs in a periodised lineage, its stance, the union of
# its in-force intervals rendered in UTC, and whether the same claim also occurs
# undated; hashed in claim order. NULL for a fact with no periodised evidence.
_FACT_BASIS = """
WITH evidence AS (
  SELECT e.{id_column} AS fact_id, e.claim_id, e.stance::text AS stance
  FROM {evidence} e
  WHERE e.deployment_id = :deployment_id
    AND e.{id_column} = ANY(CAST(:fact_ids AS uuid[]))
),
occurrence AS (
  SELECT ev.fact_id, ev.claim_id, ev.stance, s.periodised, s.in_force
  FROM evidence ev
  JOIN chunk_claims cc
    ON cc.deployment_id = :deployment_id AND cc.claim_id = ev.claim_id
  JOIN chunks ch
    ON ch.deployment_id = cc.deployment_id AND ch.chunk_id = cc.chunk_id
  JOIN document_versions v
    ON v.deployment_id = ch.deployment_id AND v.version_id = ch.version_id
   AND v.current_representation_id = ch.representation_id
  JOIN document_version_scope s
    ON s.deployment_id = v.deployment_id AND s.version_id = v.version_id
),
per_claim AS (
  SELECT o.fact_id, o.claim_id, o.stance,
         bool_or(o.periodised) AS periodised,
         bool_or(NOT o.periodised) AS also_undated,
         range_agg(r) FILTER (WHERE o.periodised AND r IS NOT NULL) AS in_force
  FROM occurrence o
  LEFT JOIN LATERAL unnest(o.in_force) AS r ON true
  GROUP BY o.fact_id, o.claim_id, o.stance
),
rendered AS (
  SELECT p.fact_id, p.claim_id, p.stance,
         p.claim_id::text || '|' || p.stance || '|' || p.also_undated::text || '|' ||
         coalesce((
           SELECT string_agg(
                    to_char(lower(x) AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US')
                    || '/' ||
                    coalesce(to_char(upper(x) AT TIME ZONE 'UTC',
                                     'YYYY-MM-DD"T"HH24:MI:SS.US'), ''),
                    ',' ORDER BY lower(x))
           FROM unnest(coalesce(p.in_force, '{{}}'::tstzmultirange)) AS x
         ), '') AS item
  FROM per_claim p
  WHERE p.periodised
)
SELECT f.fact_id,
       (SELECT encode(sha256(convert_to(
                 string_agg(r.item, E'\n' ORDER BY r.claim_id, r.stance), 'UTF8')), 'hex')
        FROM rendered r WHERE r.fact_id = f.fact_id
        HAVING count(*) > 0) AS basis
FROM unnest(CAST(:fact_ids AS uuid[])) AS f(fact_id)
"""


def fact_period_basis_on(
    *,
    connection: Connection,
    deployment_id: UUID,
    kind: FactKind,
    fact_ids: Iterable[UUID],
) -> dict[UUID, str | None]:
    """The current period basis of each fact (None when no evidence is periodised)."""
    ids = sorted({str(fact_id) for fact_id in fact_ids})
    if not ids:
        return {}
    _, id_column, evidence = _FACT_TABLES[kind]
    return {
        UUID(str(row["fact_id"])): row["basis"]
        for row in connection.execute(
            text(_FACT_BASIS.format(id_column=id_column, evidence=evidence)),
            {"deployment_id": deployment_id, "fact_ids": ids},
        ).mappings()
    }


def record_period_basis_on(
    *,
    connection: Connection,
    deployment_id: UUID,
    kind: FactKind,
    fact_ids: Iterable[UUID],
) -> None:
    """Store each fact's current basis: its window was just adjudicated against it.

    An ordinary application passes only facts its :class:`PeriodWatch` found
    current and then checks :func:`periods_unchanged_on`; a review passes the
    fact whose basis it validated under the fence. A deployment that never declared a period has no basis anywhere, so this is
    one indexed existence probe there.
    """
    if not deployment_ever_periodised_on(
        connection=connection, deployment_id=deployment_id
    ):
        return
    table, id_column, _ = _FACT_TABLES[kind]
    bases = fact_period_basis_on(
        connection=connection, deployment_id=deployment_id, kind=kind, fact_ids=fact_ids
    )
    if not bases:
        return
    connection.execute(
        text(
            f"UPDATE {table} f SET period_basis = b.basis"
            " FROM unnest(CAST(:fact_ids AS uuid[]), CAST(:bases AS text[]))"
            " AS b(fact_id, basis)"
            f" WHERE f.deployment_id = :deployment_id AND f.{id_column} = b.fact_id"
            " AND f.period_basis IS DISTINCT FROM b.basis"
        ),
        {
            "deployment_id": deployment_id,
            "fact_ids": [str(fact_id) for fact_id in bases],
            "bases": list(bases.values()),
        },
    )


# One statement, so every part is read from the same database snapshot: the
# stale candidates (stored basis differs from current), one digest over the
# in-force periods of every watched claim (all evidence of the facts, both
# stances, plus the shown claims), and the shown claims' own periods. Only the
# digest, the stale ids (at most the candidate count) and the shown claims'
# periods (at most the shown count) leave the database, however much evidence
# the facts have.
_PERIOD_WATCH = """
WITH watched AS (
  SELECT e.claim_id FROM {evidence} e
  WHERE e.deployment_id = :deployment_id
    AND e.{id_column} = ANY(CAST(:fact_ids AS uuid[]))
  UNION
  SELECT shown.claim_id FROM unnest(CAST(:shown_ids AS uuid[])) AS shown(claim_id)
),
occurrence AS (
  SELECT cc.claim_id, s.periodised, s.in_force
  FROM watched w
  JOIN chunk_claims cc
    ON cc.deployment_id = :deployment_id AND cc.claim_id = w.claim_id
  JOIN chunks ch
    ON ch.deployment_id = cc.deployment_id AND ch.chunk_id = cc.chunk_id
  JOIN document_versions v
    ON v.deployment_id = ch.deployment_id AND v.version_id = ch.version_id
   AND v.current_representation_id = ch.representation_id
  JOIN document_version_scope s
    ON s.deployment_id = v.deployment_id AND s.version_id = v.version_id
),
claim_union AS (
  SELECT o.claim_id,
         bool_or(o.periodised) AS periodised,
         bool_or(NOT o.periodised) AS also_undated,
         range_agg(r) FILTER (WHERE o.periodised AND r IS NOT NULL) AS in_force
  FROM occurrence o
  LEFT JOIN LATERAL unnest(o.in_force) AS r ON true
  GROUP BY o.claim_id
),
rendered AS (
  SELECT u.claim_id, u.also_undated,
         (SELECT coalesce(
                   jsonb_agg(
                     jsonb_build_array(
                       to_char(lower(x) AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US'),
                       to_char(upper(x) AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US'))
                     ORDER BY lower(x)),
                   '[]'::jsonb)
          FROM unnest(coalesce(u.in_force, '{{}}'::tstzmultirange)) AS x) AS in_force
  FROM claim_union u
  WHERE u.periodised
),
fact_basis AS ({fact_basis}),
stale AS (
  SELECT f.{id_column} AS fact_id
  FROM {table} f JOIN fact_basis b ON b.fact_id = f.{id_column}
  WHERE f.deployment_id = :deployment_id
    AND f.period_basis IS DISTINCT FROM b.basis
)
SELECT
  (SELECT encode(sha256(convert_to(coalesce(string_agg(
            r.claim_id::text || '|' || r.also_undated::text || '|' || r.in_force::text,
            E'\\n' ORDER BY r.claim_id), ''), 'UTF8')), 'hex')
   FROM rendered r) AS digest,
  (SELECT coalesce(jsonb_object_agg(r.claim_id::text,
            jsonb_build_object('in_force', r.in_force, 'also_undated', r.also_undated)),
          '{{}}'::jsonb)
   FROM rendered r
   WHERE r.claim_id = ANY(CAST(:shown_ids AS uuid[]))) AS shown,
  (SELECT coalesce(array_agg(fact_id), '{{}}'::uuid[]) FROM stale) AS stale
"""


@dataclass(frozen=True)
class PeriodWatch:
    """What an application's period bases may certify (D140 §8.2).

    Read in one statement before the application's final input validation.
    ``stale`` holds the candidate facts whose stored basis already differed
    from their current one: a change the decision did not adjudicate is
    pending review for them, so their basis must not be certified now.
    ``digest`` covers the periods of every claim the recorded bases can cover;
    ``shown`` holds the shown claims' own periods, in the shape adjudication
    is shown them, so the caller can check them against its validated inputs.
    """

    kind: FactKind
    fact_ids: tuple[str, ...]
    shown_ids: tuple[str, ...]
    stale: frozenset[UUID]
    digest: str
    shown: dict[str, dict[str, Any]]


def _read_watch(
    *,
    connection: Connection,
    deployment_id: UUID,
    kind: FactKind,
    fact_ids: tuple[str, ...],
    shown_ids: tuple[str, ...],
) -> tuple[str, dict[str, dict[str, Any]], frozenset[UUID]]:
    table, id_column, evidence = _FACT_TABLES[kind]
    row = connection.execute(
        text(
            _PERIOD_WATCH.format(
                evidence=evidence,
                id_column=id_column,
                table=table,
                fact_basis=_FACT_BASIS.format(id_column=id_column, evidence=evidence),
            )
        ),
        {
            "deployment_id": deployment_id,
            "fact_ids": list(fact_ids),
            "shown_ids": list(shown_ids),
        },
    ).one()
    shown = {
        claim_id: {
            "in_force": [
                {"from": _instant(start), "until": _instant(end)}
                for start, end in value["in_force"]
            ],
            "also_undated": bool(value["also_undated"]),
        }
        for claim_id, value in dict(row.shown).items()
    }
    return (
        str(row.digest),
        shown,
        frozenset(UUID(str(fact_id)) for fact_id in row.stale),
    )


def watch_periods_on(
    *,
    connection: Connection,
    deployment_id: UUID,
    kind: FactKind,
    fact_ids: Iterable[UUID],
    claim_ids: Iterable[UUID],
) -> PeriodWatch | None:
    """Capture what an application's bases may certify, before its final validation.

    ``fact_ids`` and ``claim_ids`` are the prepared inputs' candidate facts and
    shown claims; the final validation proves they are still the inputs.
    Period writers do not take the period fence (it only keeps fan-outs out),
    so a declaration, deletion or new occurrence may commit at any point; the
    caller checks ``shown`` against its validated inputs, records bases only
    for facts not in ``stale``, and requires :func:`periods_unchanged_on`
    after recording. None for a deployment that never declared a period:
    nothing is recorded there.
    """
    if not deployment_ever_periodised_on(
        connection=connection, deployment_id=deployment_id
    ):
        return None
    facts = tuple(sorted({str(fact_id) for fact_id in fact_ids}))
    shown_ids = tuple(sorted({str(claim_id) for claim_id in claim_ids}))
    digest, shown, stale = _read_watch(
        connection=connection,
        deployment_id=deployment_id,
        kind=kind,
        fact_ids=facts,
        shown_ids=shown_ids,
    )
    return PeriodWatch(
        kind=kind,
        fact_ids=facts,
        shown_ids=shown_ids,
        stale=stale,
        digest=digest,
        shown=shown,
    )


def periods_unchanged_on(
    *,
    connection: Connection,
    deployment_id: UUID,
    watch: PeriodWatch,
    changed_fact_ids: Iterable[UUID],
) -> bool:
    """Whether every watched claim still has the periods the watch read.

    Called after the bases are recorded, in the same transaction. The watched
    claims are the same set: an application only adds or removes evidence of
    shown claims, and every fact it changed is a candidate or holds only
    shown-claim evidence. A period writer that committed before this read is
    detected (the application rolls back and retries); one that commits
    after it leaves the old basis stored, which its own fan-out then sees as
    moved and reviews.
    """
    digest, _, _ = _read_watch(
        connection=connection,
        deployment_id=deployment_id,
        kind=watch.kind,
        fact_ids=tuple(
            sorted({*watch.fact_ids, *(str(fact_id) for fact_id in changed_fact_ids)})
        ),
        shown_ids=watch.shown_ids,
    )
    return digest == watch.digest


def share_period_fence_on(*, connection: Connection, deployment_id: UUID) -> None:
    """Hold the deployment's period fence shared until the transaction ends.

    Taken by every writer of a fact window or ``period_basis`` (ordinary
    adjudication apply, window review apply) after acquiring the entity
    adjudication lock and before its final input validation; it is held through
    commit. Lock order: hard-forget fence, identity epoch, entity lock, period
    fence.
    """
    connection.execute(
        text("SELECT pg_advisory_xact_lock_shared(hashtextextended(:key, 0))"),
        {"key": f"period-fence:{deployment_id}"},
    )


def period_fence_barrier_on(*, connection: Connection, deployment_id: UUID) -> None:
    """Wait for every in-flight window decision of the deployment to commit.

    A lineage fan-out takes the fence exclusively, holding no other lock, before
    it reads stored bases. Every input change it reacts to committed before the
    fan-out was queued, so a decision validated on older inputs has either
    committed by now (and its stale basis is seen and reviewed) or validates
    after the change (and sees it). The barrier is released when the
    transaction ends; later pages need no barrier.
    """
    connection.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"period-fence:{deployment_id}"},
    )


def deployment_ever_periodised_on(
    *, connection: Connection, deployment_id: UUID
) -> bool:
    """Whether any lineage of the deployment ever recorded an effective-time event."""
    return bool(
        connection.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM document_effective_time_events"
                " WHERE deployment_id = :deployment_id)"
            ),
            {"deployment_id": deployment_id},
        ).scalar_one()
    )


def lineage_ever_periodised_on(
    *, connection: Connection, deployment_id: UUID, doc_id: UUID
) -> bool:
    """Whether the lineage ever recorded an effective-time event (declared or cleared).

    An undated lineage has none, so nothing about it is ever queued here.
    """
    return bool(
        connection.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM document_effective_time_events"
                " WHERE deployment_id = :deployment_id AND doc_id = :doc_id)"
            ),
            {"deployment_id": deployment_id, "doc_id": doc_id},
        ).scalar_one()
    )


def review_request(
    *,
    deployment_id: UUID,
    doc_id: UUID,
    reason: str,
    request_id: UUID | None = None,
    after: tuple[str, str] | None = None,
) -> Any:
    """The lineage fan-out job: find this lineage's facts whose basis moved.

    A work row is unique per target, so every request is its own target: a
    fresh id, or ``request_id`` from a caller that may retry (a reconcile run
    derives it from its own id). The lineage is in the payload. The fan-out is
    a cheap query, and the reviews it queues are no-ops for facts whose basis
    did not change.
    """
    from rememberstack.model import EnqueueWork
    from rememberstack.model import PipelineStage
    from rememberstack.model import ProcessingLane
    from rememberstack.model import ProcessingTarget

    target = request_id or uuid4()
    return EnqueueWork(
        deployment_id=deployment_id,
        target_kind=ProcessingTarget.DOCUMENT,
        target_id=target,
        stage=PipelineStage.REVIEW_FACT_WINDOWS,
        component_version=FACT_WINDOW_REVIEW_VERSION,
        content_hash=target.hex,
        lane=ProcessingLane.STEADY,
        payload={
            "doc_id": str(doc_id),
            "reason": reason,
            **({} if after is None else {"after_kind": after[0], "after_id": after[1]}),
        },
    )


def request_window_review_on(
    *,
    connection: Connection,
    deployment_id: UUID,
    doc_id: UUID,
    reason: str,
    request_id: UUID | None = None,
) -> bool:
    """Queue a lineage fan-out in the caller's transaction, if it ever had periods.

    Returns whether a job was queued. Nothing is queued while a hard forget is
    honored: it scrubs the touched facts' windows and bases itself, and a job
    naming the forgotten lineage would lose its payload to the same scrub.
    """
    from rememberstack.spine.admission import active_forget_id_on

    if not lineage_ever_periodised_on(
        connection=connection, deployment_id=deployment_id, doc_id=doc_id
    ):
        return False
    if active_forget_id_on(connection=connection, deployment_id=deployment_id):
        return False
    from rememberstack.spine.work_ledger import enqueue_on

    enqueue_on(
        connection=connection,
        work=review_request(
            deployment_id=deployment_id,
            doc_id=doc_id,
            reason=reason,
            request_id=request_id,
        ),
    )
    return True


REVIEW_PAGE_SIZE = 500
"""Facts examined per lineage fan-out job; a fuller page continues in a new job."""

_LINEAGE_FACTS = """
SELECT f.{id_column} AS fact_id, f.period_basis AS stored
FROM {table} f
WHERE f.deployment_id = :deployment_id
  AND f.invalidated_at IS NULL
  AND f.{id_column} > CAST(:after AS uuid)
  AND EXISTS (
    SELECT 1 FROM {evidence} e
    JOIN chunk_claims cc
      ON cc.deployment_id = e.deployment_id AND cc.claim_id = e.claim_id
    JOIN chunks ch
      ON ch.deployment_id = cc.deployment_id AND ch.chunk_id = cc.chunk_id
    WHERE e.deployment_id = f.deployment_id
      AND e.{id_column} = f.{id_column}
      AND ch.doc_id = :doc_id
  )
ORDER BY f.{id_column}
LIMIT :limit
"""

_ORDER: tuple[FactKind, ...] = ("relation", "observation")
_FIRST_ID = "00000000-0000-0000-0000-000000000000"


def facts_needing_review_on(
    *,
    connection: Connection,
    deployment_id: UUID,
    doc_id: UUID,
    after: tuple[str, str] | None = None,
    limit: int = REVIEW_PAGE_SIZE,
) -> tuple[list[tuple[FactKind, UUID, str | None]], tuple[str, str] | None]:
    """One page of live facts with evidence in the lineage whose basis moved.

    Pages walk relations then observations in id order from ``after``; the
    second value is the cursor of the next page, or None when the lineage is
    done. Deleted versions count: deleting a version is one way a fact's
    periodised evidence changes. A fact left with neither periodised nor live
    evidence (its only source was deleted) is skipped: the lifecycle cascade
    already settled it, and it has nothing to review against.
    """
    kind_index = 0 if after is None else _ORDER.index(after[0])  # type: ignore[arg-type]
    cursor = _FIRST_ID if after is None else after[1]
    found: list[tuple[FactKind, UUID, str | None]] = []
    remaining = limit
    for kind in _ORDER[kind_index:]:
        table, id_column, evidence = _FACT_TABLES[kind]
        rows = [
            dict(row)
            for row in connection.execute(
                text(
                    _LINEAGE_FACTS.format(
                        id_column=id_column, evidence=evidence, table=table
                    )
                ),
                {
                    "deployment_id": deployment_id,
                    "doc_id": doc_id,
                    "after": cursor,
                    "limit": remaining,
                },
            ).mappings()
        ]
        stored = {UUID(str(row["fact_id"])): row["stored"] for row in rows}
        current = fact_period_basis_on(
            connection=connection,
            deployment_id=deployment_id,
            kind=kind,
            fact_ids=stored,
        )
        moved = [fact for fact in stored if current[fact] != stored[fact]]
        live = _with_live_evidence(
            connection=connection,
            deployment_id=deployment_id,
            kind=kind,
            fact_ids=[fact for fact in moved if current[fact] is None],
        )
        for fact_id in sorted(moved, key=str):
            if current[fact_id] is None and fact_id not in live:
                continue
            found.append((kind, fact_id, current[fact_id]))
        if len(rows) == remaining:
            return found, (kind, str(UUID(str(rows[-1]["fact_id"]))))
        remaining -= len(rows)
        cursor = _FIRST_ID
    return found, None


def _with_live_evidence(
    *, connection: Connection, deployment_id: UUID, kind: FactKind, fact_ids: list[UUID]
) -> set[UUID]:
    """The facts with an evidence claim occurring in some live version's current reading."""
    if not fact_ids:
        return set()
    _, id_column, evidence = _FACT_TABLES[kind]
    return {
        UUID(str(value))
        for value in connection.execute(
            text(
                f"SELECT DISTINCT e.{id_column} FROM {evidence} e"
                " JOIN chunk_claims cc ON cc.deployment_id = e.deployment_id"
                " AND cc.claim_id = e.claim_id"
                " JOIN chunks ch ON ch.deployment_id = cc.deployment_id"
                " AND ch.chunk_id = cc.chunk_id"
                " JOIN document_versions v ON v.deployment_id = ch.deployment_id"
                " AND v.version_id = ch.version_id"
                " AND v.current_representation_id = ch.representation_id"
                " JOIN document_version_scope s ON s.deployment_id = v.deployment_id"
                " AND s.version_id = v.version_id"
                " WHERE e.deployment_id = :deployment_id"
                f" AND e.{id_column} = ANY(CAST(:fact_ids AS uuid[]))"
            ),
            {"deployment_id": deployment_id, "fact_ids": [str(f) for f in fact_ids]},
        ).scalars()
    }
