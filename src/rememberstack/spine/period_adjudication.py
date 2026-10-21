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
from collections.abc import Mapping
from datetime import datetime
from datetime import UTC
from hashlib import sha256
import json
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


def _evidence_sql(kind: str) -> str:
    _, id_column, evidence = _FACT_TABLES[kind]
    return f"""
SELECT e.{id_column} AS fact_id, e.claim_id, e.stance::text AS stance
FROM {evidence} e
WHERE e.deployment_id = :deployment_id
  AND e.{id_column} = ANY(CAST(:fact_ids AS uuid[]))
"""


def basis_of(evidence: Iterable[Mapping[str, Any]]) -> str | None:
    """Fingerprint one fact's periodised evidence; None when it has none.

    ``evidence`` rows carry ``claim_id``, ``stance`` and the claim's
    ``in_force``/``also_undated`` (absent for undated-only claims).
    """
    items = sorted(
        (
            str(row["claim_id"]),
            str(row["stance"]),
            json.dumps(row["in_force"], sort_keys=True),
            bool(row["also_undated"]),
        )
        for row in evidence
        if row.get("in_force") is not None
    )
    if not items:
        return None
    return sha256(json.dumps(items).encode()).hexdigest()


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
    evidence = [
        dict(row)
        for row in connection.execute(
            text(_evidence_sql(kind)), {"deployment_id": deployment_id, "fact_ids": ids}
        ).mappings()
    ]
    intervals = claim_in_force_on(
        connection=connection,
        deployment_id=deployment_id,
        claim_ids=(row["claim_id"] for row in evidence),
    )
    grouped: dict[UUID, list[dict[str, Any]]] = {UUID(fact): [] for fact in ids}
    for row in evidence:
        found = intervals.get(str(row["claim_id"]))
        grouped[UUID(str(row["fact_id"]))].append(
            {
                "claim_id": row["claim_id"],
                "stance": row["stance"],
                "in_force": found["in_force"] if found else None,
                "also_undated": found["also_undated"] if found else False,
            }
        )
    return {fact_id: basis_of(rows) for fact_id, rows in grouped.items()}


def record_period_basis_on(
    *,
    connection: Connection,
    deployment_id: UUID,
    kind: FactKind,
    fact_ids: Iterable[UUID],
) -> None:
    """Store each fact's current basis: its window was just adjudicated against it.

    A deployment that never declared a period has no basis anywhere, so this is
    one indexed existence probe there.
    """
    if not deployment_ever_periodised_on(
        connection=connection, deployment_id=deployment_id
    ):
        return
    table, id_column, _ = _FACT_TABLES[kind]
    for fact_id, basis in fact_period_basis_on(
        connection=connection, deployment_id=deployment_id, kind=kind, fact_ids=fact_ids
    ).items():
        connection.execute(
            text(
                f"UPDATE {table} SET period_basis = :basis"
                f" WHERE deployment_id = :deployment_id AND {id_column} = :fact_id"
                " AND period_basis IS DISTINCT FROM :basis"
            ),
            {"deployment_id": deployment_id, "fact_id": fact_id, "basis": basis},
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
    *, deployment_id: UUID, doc_id: UUID, reason: str, request_id: UUID | None = None
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
        payload={"doc_id": str(doc_id), "reason": reason},
    )


def request_window_review_on(
    *, connection: Connection, deployment_id: UUID, doc_id: UUID, reason: str
) -> bool:
    """Queue a lineage fan-out in the caller's transaction, if it ever had periods.

    Returns whether a job was queued.
    """
    if not lineage_ever_periodised_on(
        connection=connection, deployment_id=deployment_id, doc_id=doc_id
    ):
        return False
    from rememberstack.spine.work_ledger import enqueue_on

    enqueue_on(
        connection=connection,
        work=review_request(deployment_id=deployment_id, doc_id=doc_id, reason=reason),
    )
    return True


_LINEAGE_FACTS = """
SELECT DISTINCT e.{id_column} AS fact_id, f.period_basis AS stored
FROM {evidence} e
JOIN {table} f
  ON f.deployment_id = e.deployment_id AND f.{id_column} = e.{id_column}
JOIN chunk_claims cc
  ON cc.deployment_id = e.deployment_id AND cc.claim_id = e.claim_id
JOIN chunks ch
  ON ch.deployment_id = cc.deployment_id AND ch.chunk_id = cc.chunk_id
JOIN document_versions v
  ON v.deployment_id = ch.deployment_id AND v.version_id = ch.version_id
WHERE e.deployment_id = :deployment_id
  AND v.doc_id = :doc_id
  AND f.invalidated_at IS NULL
"""


def facts_needing_review_on(
    *, connection: Connection, deployment_id: UUID, doc_id: UUID
) -> list[tuple[FactKind, UUID, str | None]]:
    """Live facts with evidence in any version of the lineage whose basis moved.

    Deleted versions count: deleting a version is one way a fact's periodised
    evidence changes. Returns ``(kind, fact_id, current_basis)``.
    """
    found: list[tuple[FactKind, UUID, str | None]] = []
    for kind in ("relation", "observation"):
        table, id_column, evidence = _FACT_TABLES[kind]
        rows = connection.execute(
            text(
                _LINEAGE_FACTS.format(
                    id_column=id_column, evidence=evidence, table=table
                )
            ),
            {"deployment_id": deployment_id, "doc_id": doc_id},
        ).mappings()
        stored = {UUID(str(row["fact_id"])): row["stored"] for row in rows}
        current = fact_period_basis_on(
            connection=connection,
            deployment_id=deployment_id,
            kind=kind,  # type: ignore[arg-type]
            fact_ids=stored,
        )
        for fact_id in sorted(stored, key=str):
            if current[fact_id] != stored[fact_id]:
                found.append((kind, fact_id, current[fact_id]))  # type: ignore[arg-type]
    return found
