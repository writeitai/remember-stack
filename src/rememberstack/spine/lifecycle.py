"""The lifecycle catalog (D54/D55): currency, recount, closure, deletion.

Spine-owned SQL for the reconciliation flow (lifecycle §5) and the deletion
grains (§8). The `testimony_currency_events` ledger is truth and
`claims.is_current_testimony` is cache; every write here is idempotent under
a stable `reconciliation_id` (a retried run re-emits its rows as no-ops) so
reconciliation can ride the ordinary work ledger.
"""

from uuid import UUID
from uuid import uuid4

from sqlalchemy import bindparam
from sqlalchemy import JSON
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine

from rememberstack.model import CurrencyTransition
from rememberstack.model import ReconciliationDelta

CURRENCY_CACHE_MISMATCH_SQL = """
    SELECT cl.claim_id,
           cl.is_current_testimony AS cached_current,
           coalesce(last.became_current, true) AS ledger_current
    FROM claims cl
    LEFT JOIN LATERAL (
        SELECT e.became_current
        FROM testimony_currency_events e
        WHERE e.claim_id = cl.claim_id
        ORDER BY e.occurred_at DESC, e.event_id DESC
        LIMIT 1
    ) last ON true
    WHERE cl.deployment_id = :deployment_id
      AND cl.is_current_testimony <> coalesce(last.became_current, true)
"""
"""Single source for the D33 currency-cache versus append-only-ledger invariant."""


class LifecycleCatalog:
    """Currency transitions, the D54 recount, per-shape closure, deletion."""

    def __init__(self, *, engine: Engine) -> None:
        """Bind the catalog to the spine database."""
        self._engine = engine

    def reconciliation_context(self, *, version_id: UUID) -> dict[str, object]:
        """What reconciling one completed version needs to know."""
        with self._engine.connect() as connection:
            row = (
                connection.execute(_SELECT_CONTEXT, {"version_id": version_id})
                .mappings()
                .one()
            )
        return dict(row)

    def stale_for_supersession(
        self, *, deployment_id: UUID, doc_id: UUID, current_version_id: UUID
    ) -> tuple[CurrencyTransition, ...]:
        """Living-mode §3 rule: current claims the current version left behind.

        A claim stays current iff SOME chunk of the current version carries
        it — by origin or by a reuse occurrence link. Everything else of the
        lineage flips `version_superseded`.
        """
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    _SELECT_STALE_SUPERSEDED,
                    {
                        "deployment_id": deployment_id,
                        "doc_id": doc_id,
                        "current_version_id": current_version_id,
                    },
                )
                .mappings()
                .all()
            )
        return tuple(
            CurrencyTransition(
                claim_id=row["claim_id"],
                doc_id=doc_id,
                became_current=False,
                reason="version_superseded",
                from_version_id=row["from_version_id"],
            )
            for row in rows
        )

    def stale_for_reextraction(
        self,
        *,
        version_id: UUID,
        representation_id: UUID,
        chunker_version: str,
        extractor_version: str,
    ) -> tuple[CurrencyTransition, ...]:
        """Re-derivation §3 rule: older-BASIS claims on a re-read version.

        The reason covers ANY basis-coordinate change (§3): an extractor
        bump, a converter bump (a different representation), or a
        blockizer/chunker bump (a different packing generation) — a claim
        of this version whose (representation, chunker generation,
        extractor generation) differs from the completing basis flips
        `reextracted`, wholesale, by coordinates, no content matching.
        """
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    _SELECT_STALE_REEXTRACTED,
                    {
                        "version_id": version_id,
                        "representation_id": representation_id,
                        "chunker_version": chunker_version,
                        "extractor_version": extractor_version,
                    },
                )
                .mappings()
                .all()
            )
        return tuple(
            CurrencyTransition(
                claim_id=row["claim_id"],
                doc_id=row["doc_id"],
                became_current=False,
                reason="reextracted",
                from_extractor_version=row["from_extractor_version"],
            )
            for row in rows
        )

    def stale_for_deletion(
        self, *, deployment_id: UUID, doc_id: UUID
    ) -> tuple[CurrencyTransition, ...]:
        """Deletion §8 rule: every current claim of the lineage ends."""
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    _SELECT_STALE_DELETED,
                    {"deployment_id": deployment_id, "doc_id": doc_id},
                )
                .mappings()
                .all()
            )
        return tuple(
            CurrencyTransition(
                claim_id=row["claim_id"],
                doc_id=doc_id,
                became_current=False,
                reason="version_deleted",
                from_version_id=row["from_version_id"],
            )
            for row in rows
        )

    def stale_for_version_deletion(
        self, *, deployment_id: UUID, version_id: UUID
    ) -> tuple[CurrencyTransition, ...]:
        """§8 version grain: ONLY the deleted version's exclusive testimony ends.

        A claim flips `version_deleted` iff it is carried by the deleted
        version and by no other live version of the lineage — snapshot
        lineages keep every other version's testimony current.
        """
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    _SELECT_STALE_VERSION_DELETED,
                    {"deployment_id": deployment_id, "version_id": version_id},
                )
                .mappings()
                .all()
            )
        return tuple(
            CurrencyTransition(
                claim_id=row["claim_id"],
                doc_id=row["doc_id"],
                became_current=False,
                reason="version_deleted",
                from_version_id=version_id,
            )
            for row in rows
        )

    def regained_by_current_version(
        self, *, deployment_id: UUID, doc_id: UUID, current_version_id: UUID
    ) -> tuple[CurrencyTransition, ...]:
        """Non-current claims the (new) current version carries — regained.

        Deleting the newest version repoints the lineage at its predecessor
        (§8: "the lineage continues"); the predecessor's testimony IS the
        current basis again, so its claims regain currency — an append-only
        `became_current = true` event, never a rewrite.
        """
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    _SELECT_REGAINED,
                    {
                        "deployment_id": deployment_id,
                        "doc_id": doc_id,
                        "current_version_id": current_version_id,
                    },
                )
                .mappings()
                .all()
            )
        return tuple(
            CurrencyTransition(
                claim_id=row["claim_id"],
                doc_id=doc_id,
                became_current=True,
                reason="version_deleted",
                from_version_id=row["from_version_id"],
            )
            for row in rows
        )

    def recorded_transitions(
        self, *, reconciliation_id: UUID
    ) -> tuple[CurrencyTransition, ...]:
        """This run's already-ledgered transitions (the retry recovery read).

        A crash between the currency transaction and the downstream steps
        must not orphan the run: a retry unions what the ledger already
        holds under this reconciliation_id with whatever it recomputes, so
        recount/closure/flags/emission always see the full set.
        """
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    _SELECT_RUN_EVENTS, {"reconciliation_id": reconciliation_id}
                )
                .mappings()
                .all()
            )
        return tuple(
            CurrencyTransition(
                claim_id=row["claim_id"],
                doc_id=row["doc_id"],
                became_current=row["became_current"],
                reason=row["reason"],
                from_extractor_version=row["from_extractor_version"],
                from_version_id=row["from_version_id"],
            )
            for row in rows
        )

    def apply_transitions(
        self,
        *,
        deployment_id: UUID,
        reconciliation_id: UUID,
        transitions: tuple[CurrencyTransition, ...],
    ) -> int:
        """Append ledger rows and update the cache flags (D54, F11-idempotent).

        The guarded insert makes a retried run's re-emission a no-op: an
        event with the same (claim, reconciliation, reason, direction)
        already in the ledger is never duplicated. The cache follows the
        ledger in the same transaction. Returns how many events were new.
        """
        if not transitions:
            return 0
        # Collapse exact repeats before the set write. The former row loop
        # made the first repeat win through its NOT EXISTS guard.
        unique: dict[tuple[UUID, str, bool], CurrencyTransition] = {}
        for transition in transitions:
            unique.setdefault(
                (transition.claim_id, transition.reason, transition.became_current),
                transition,
            )
        event_rows = [
            {
                "event_id": str(uuid4()),
                "claim_id": str(transition.claim_id),
                "doc_id": str(transition.doc_id),
                "became_current": transition.became_current,
                "reason": transition.reason,
                "from_extractor_version": transition.from_extractor_version,
                "from_version_id": (
                    str(transition.from_version_id)
                    if transition.from_version_id is not None
                    else None
                ),
            }
            for transition in unique.values()
        ]
        # A claim can legitimately flip more than once in a composed run.
        # Match the former loop: the final transition owns the cache value.
        cache_rows = [
            {"claim_id": str(claim_id), "is_current": is_current}
            for claim_id, is_current in {
                transition.claim_id: transition.became_current
                for transition in transitions
            }.items()
        ]
        with self._engine.begin() as connection:
            applied = connection.execute(
                _INSERT_CURRENCY_EVENTS,
                {
                    "deployment_id": deployment_id,
                    "reconciliation_id": reconciliation_id,
                    "events": event_rows,
                },
            ).scalar_one()
            connection.execute(_UPDATE_CURRENCY_CACHES, {"claims": cache_rows})
        return int(applied)

    def affected_relation_ids(self, *, claim_ids: tuple[UUID, ...]) -> tuple[UUID, ...]:
        """Relations evidenced by any of these claims (the recount scope)."""
        if not claim_ids:
            return ()
        with self._engine.connect() as connection:
            return tuple(
                connection.execute(
                    _SELECT_AFFECTED_RELATIONS, {"claim_ids": list(claim_ids)}
                ).scalars()
            )

    def affected_observation_ids(
        self, *, claim_ids: tuple[UUID, ...]
    ) -> tuple[UUID, ...]:
        """Observations evidenced by any of these claims (the recount scope)."""
        if not claim_ids:
            return ()
        with self._engine.connect() as connection:
            return tuple(
                connection.execute(
                    _SELECT_AFFECTED_OBSERVATIONS, {"claim_ids": list(claim_ids)}
                ).scalars()
            )

    def recount(
        self, *, relation_ids: tuple[UUID, ...], observation_ids: tuple[UUID, ...]
    ) -> tuple[tuple[UUID, ...], tuple[UUID, ...]]:
        """Recompute the D54 counts for the touched facts (bounded, indexed).

        Returns the facts whose counts actually CHANGED — the stale-storm
        guard's input: a re-extraction that changes no fact state must
        stale nothing, so only changed facts belong in the emitted delta.
        """
        with self._engine.begin() as connection:
            changed_relation_ids = (
                frozenset(
                    connection.execute(
                        _RECOUNT_RELATIONS, {"relation_ids": list(relation_ids)}
                    ).scalars()
                )
                if relation_ids
                else frozenset()
            )
            changed_observation_ids = (
                frozenset(
                    connection.execute(
                        _RECOUNT_OBSERVATIONS,
                        {"observation_ids": list(observation_ids)},
                    ).scalars()
                )
                if observation_ids
                else frozenset()
            )
        # RETURNING order is not a SQL contract; retain the prior row-loop's
        # caller-visible input order while keeping the write set-based.
        return (
            tuple(item for item in relation_ids if item in changed_relation_ids),
            tuple(item for item in observation_ids if item in changed_observation_ids),
        )

    def open_zero_support_relations(
        self, *, deployment_id: UUID, relation_ids: tuple[UUID, ...]
    ) -> tuple[UUID, ...]:
        """Still-open, still-believed relations whose support hit zero."""
        if not relation_ids:
            return ()
        with self._engine.connect() as connection:
            return tuple(
                connection.execute(
                    _SELECT_ZERO_RELATIONS,
                    {
                        "deployment_id": deployment_id,
                        "relation_ids": list(relation_ids),
                    },
                ).scalars()
            )

    def open_zero_support_observations(
        self, *, deployment_id: UUID, observation_ids: tuple[UUID, ...]
    ) -> tuple[UUID, ...]:
        """Still-believed observations whose support hit zero."""
        if not observation_ids:
            return ()
        with self._engine.connect() as connection:
            return tuple(
                connection.execute(
                    _SELECT_ZERO_OBSERVATIONS,
                    {
                        "deployment_id": deployment_id,
                        "observation_ids": list(observation_ids),
                    },
                ).scalars()
            )

    def close_relations(
        self,
        *,
        deployment_id: UUID,
        relation_ids: tuple[UUID, ...],
        boundary: object,
        reconciliation_id: UUID,
    ) -> tuple[UUID, ...]:
        """Close solely-supported relations per shape (§4 source-acted rule).

        A relation is a stated world-time window: `valid_until` caps at the
        boundary (the withdrawing version's source-modified time) and an
        append-only `retracted_source_removal` adjudication records why —
        loud, attributed, reversible; `invalidated_at` stays NULL (the fact
        was believed while supported; retraction is not "learned wrong").
        """
        closed: list[UUID] = []
        with self._engine.begin() as connection:
            for relation_id in relation_ids:
                capped = connection.execute(
                    _CAP_RELATION, {"relation_id": relation_id, "boundary": boundary}
                ).scalar_one_or_none()
                if capped is None:
                    continue  # already closed by an earlier attempt
                closed.append(relation_id)
                _record_adjudication(
                    connection=connection,
                    sql=_INSERT_RELATION_RETRACTION,
                    deployment_id=deployment_id,
                    fact_id=relation_id,
                    reconciliation_id=reconciliation_id,
                )
        return tuple(closed)

    def close_observations(
        self,
        *,
        deployment_id: UUID,
        observation_ids: tuple[UUID, ...],
        reconciliation_id: UUID,
    ) -> tuple[UUID, ...]:
        """Close solely-supported observations per shape (§4, D43 no-cap).

        Observations are untyped (state vs measurement is semantic), so the
        mechanical exit that is safe for BOTH shapes is `invalidated_at` —
        belief ends without asserting a world-time end (capping a
        measurement's window is forbidden by the no-cap rule).
        """
        closed: list[UUID] = []
        with self._engine.begin() as connection:
            for observation_id in observation_ids:
                marked = connection.execute(
                    _INVALIDATE_OBSERVATION, {"observation_id": observation_id}
                ).scalar_one_or_none()
                if marked is None:
                    continue
                closed.append(observation_id)
                _record_adjudication(
                    connection=connection,
                    sql=_INSERT_OBSERVATION_RETRACTION,
                    deployment_id=deployment_id,
                    fact_id=observation_id,
                    reconciliation_id=reconciliation_id,
                )
        return tuple(closed)

    def emit_evidence_changed(
        self, *, deployment_id: UUID, delta: ReconciliationDelta
    ) -> bool:
        """Queue one fact-level `evidence_changed` trigger (D45).

        Guarded by the reconciliation id inside the payload, so a retried
        run never double-fires the K compile driver. Nothing is emitted for
        an empty delta ("a new claim row for the same testimony is not an
        evidence change").
        """
        if not (
            delta.recounted_relations
            or delta.recounted_observations
            or delta.relations_closed
            or delta.observations_closed
            or delta.flags_raised
        ):
            return False
        with self._engine.begin() as connection:
            inserted = connection.execute(
                _INSERT_EVIDENCE_CHANGED,
                {
                    "refresh_id": uuid4(),
                    "deployment_id": deployment_id,
                    "reconciliation_id": str(delta.reconciliation_id),
                    "payload": delta.model_dump(mode="json"),
                },
            ).scalar_one_or_none()
        return inserted is not None

    def delete_version(self, *, version_id: UUID) -> dict[str, object]:
        """Mark one version deleted; repoint currency if it was current (§8).

        The lineage continues: if the deleted version held the current
        pointer, the latest remaining live version takes it (NULL when none
        remain). Returns the lineage context for the caller's cascade.
        """
        with self._engine.begin() as connection:
            row = (
                connection.execute(_TOMBSTONE_VERSION, {"version_id": version_id})
                .mappings()
                .one()
            )
            connection.execute(
                _REPOINT_AFTER_VERSION_DELETE,
                {"doc_id": row["doc_id"], "version_id": version_id},
            )
        return dict(row)

    def delete_lineage(self, *, doc_id: UUID) -> None:
        """Tombstone a lineage by operator decision (§8; audit-visible).

        Claims are retained as history — normal deletion never scrubs
        content (forgotten ≠ deleted); the caller runs the currency cascade.
        """
        with self._engine.begin() as connection:
            connection.execute(_TOMBSTONE_LINEAGE_BY_ID, {"doc_id": doc_id})

    def cycles_ready_to_finalize(
        self, *, deployment_id: UUID
    ) -> tuple[tuple[UUID, int], ...]:
        """Completed, unfinalized cycles whose observed versions are done.

        A cycle is ready when no version it stamped still has pending,
        running, or retrying (failed) chain work — lineages still
        extracting defer the cycle to the next finalization pass (the
        recorded grace). Each entry carries the cycle's `failed_items`
        count: a LOSSY cycle's observation set is incomplete and must not
        drive absence-based closure.
        """
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    _SELECT_READY_CYCLES, {"deployment_id": deployment_id}
                )
                .mappings()
                .all()
            )
        return tuple((row["cycle_id"], row["failed_items"]) for row in rows)

    def cycle_lineages(self, *, cycle_id: UUID) -> tuple[UUID, ...]:
        """Lineages the cycle observed via ingested versions."""
        with self._engine.connect() as connection:
            return tuple(
                connection.execute(
                    _SELECT_CYCLE_LINEAGES, {"cycle_id": cycle_id}
                ).scalars()
            )

    def tombstoned_lineages_needing_cascade(
        self, *, deployment_id: UUID
    ) -> tuple[UUID, ...]:
        """Source-tombstoned lineages that still hold current testimony.

        Deployment-wide, not per cycle: the cascade re-derives from current
        state, so a finalizer crash between claiming a cycle and finishing
        its cascades self-heals on the next pass instead of orphaning the
        tombstone.
        """
        with self._engine.connect() as connection:
            return tuple(
                connection.execute(
                    _SELECT_TOMBSTONES_NEEDING_CASCADE, {"deployment_id": deployment_id}
                ).scalars()
            )

    def lineage_claim_ids(
        self, *, deployment_id: UUID, doc_id: UUID
    ) -> tuple[UUID, ...]:
        """Every claim the lineage ever produced (the closure-scan scope)."""
        with self._engine.connect() as connection:
            return tuple(
                connection.execute(
                    _SELECT_LINEAGE_CLAIMS,
                    {"deployment_id": deployment_id, "doc_id": doc_id},
                ).scalars()
            )

    def closure_boundary(self, *, doc_id: UUID) -> object:
        """The §4 cap boundary: the current version's source-modified time."""
        with self._engine.connect() as connection:
            return connection.execute(
                _SELECT_CLOSURE_BOUNDARY, {"doc_id": doc_id}
            ).scalar_one_or_none()

    def claim_finalization(self, *, cycle_id: UUID) -> bool:
        """Atomically claim one cycle's finalization (single winner).

        Two finalizer instances can never both run a cycle's retraction
        evaluation: the first UPDATE wins, the loser sees False. A crash
        after claiming leaves any unfinished closure to the next
        observation of the same lineages (a brief, visible, self-healing
        gap — the cascade re-derives from current state, never from
        per-cycle deltas) and tombstone cascades to the deployment-wide
        sweep.
        """
        with self._engine.begin() as connection:
            claimed = connection.execute(
                _CLAIM_FINALIZATION, {"cycle_id": cycle_id}
            ).scalar_one_or_none()
        return claimed is not None


def _record_adjudication(
    *,
    connection: Connection,
    sql: object,
    deployment_id: UUID,
    fact_id: UUID,
    reconciliation_id: UUID,
) -> None:
    """Append one retraction adjudication, once per (fact, reconciliation)."""
    connection.execute(
        sql,  # type: ignore[arg-type]
        {
            "adjudication_id": uuid4(),
            "deployment_id": deployment_id,
            "fact_id": fact_id,
            "features": {"reconciliation_id": str(reconciliation_id)},
            "reconciliation_id": str(reconciliation_id),
        },
    )


_SELECT_CONTEXT = text(
    """
    SELECT v.deployment_id, v.doc_id, v.version_id, v.version_no,
           v.sync_cycle_id, d.versioning_mode::text AS versioning_mode,
           d.current_version_id,
           (SELECT cv.source_modified_at FROM document_versions cv
            WHERE cv.version_id = d.current_version_id) AS current_source_modified_at
    FROM document_versions v
    JOIN documents d ON d.doc_id = v.doc_id
    WHERE v.version_id = :version_id
    """
)

_SELECT_STALE_SUPERSEDED = text(
    """
    SELECT cl.claim_id, c.version_id AS from_version_id
    FROM claims cl
    JOIN chunks c ON c.chunk_id = cl.chunk_id
    WHERE cl.deployment_id = :deployment_id
      AND cl.doc_id = :doc_id
      AND cl.is_current_testimony
      AND c.version_id <> :current_version_id
      AND NOT EXISTS (SELECT 1 FROM chunk_claims cc
                      JOIN chunks cur ON cur.chunk_id = cc.chunk_id
                      WHERE cc.claim_id = cl.claim_id
                        AND cur.version_id = :current_version_id)
    """
)

_SELECT_STALE_REEXTRACTED = text(
    """
    SELECT cl.claim_id, cl.doc_id, cl.extractor_version AS from_extractor_version
    FROM claims cl
    JOIN chunks c ON c.chunk_id = cl.chunk_id
    WHERE c.version_id = :version_id
      AND cl.is_current_testimony
      AND (c.representation_id <> :representation_id
           OR c.chunker_version <> :chunker_version
           OR cl.extractor_version <> :extractor_version)
      AND NOT (
          -- carried into the completing basis by a same-generation reuse
          -- link: the claim IS part of the current transcription. An OLD
          -- generation's own links never count — reuse keys embed the
          -- extractor version, so re-attachment is same-generation only.
          cl.extractor_version = :extractor_version
          AND EXISTS (
              SELECT 1 FROM chunk_claims cc
              JOIN chunks cur ON cur.chunk_id = cc.chunk_id
              WHERE cc.claim_id = cl.claim_id
                AND cur.representation_id = :representation_id
                AND cur.chunker_version = :chunker_version
          )
      )
    """
)

_SELECT_STALE_DELETED = text(
    """
    SELECT cl.claim_id, c.version_id AS from_version_id
    FROM claims cl
    JOIN chunks c ON c.chunk_id = cl.chunk_id
    WHERE cl.deployment_id = :deployment_id
      AND cl.doc_id = :doc_id
      AND cl.is_current_testimony
    """
)

_SELECT_STALE_VERSION_DELETED = text(
    """
    SELECT cl.claim_id, cl.doc_id
    FROM claims cl
    WHERE cl.deployment_id = :deployment_id
      AND cl.is_current_testimony
      AND EXISTS (SELECT 1 FROM chunks c
                  WHERE c.chunk_id = cl.chunk_id
                    AND c.version_id = :version_id
                  UNION ALL
                  SELECT 1 FROM chunk_claims cc
                  JOIN chunks oc ON oc.chunk_id = cc.chunk_id
                  WHERE cc.claim_id = cl.claim_id
                    AND oc.version_id = :version_id)
      AND NOT EXISTS (
          SELECT 1 FROM chunk_claims cc2
          JOIN chunks other ON other.chunk_id = cc2.chunk_id
          JOIN document_versions ov ON ov.version_id = other.version_id
          WHERE cc2.claim_id = cl.claim_id
            AND other.version_id <> :version_id
            AND ov.deleted_at IS NULL
          UNION ALL
          SELECT 1 FROM chunks origin
          JOIN document_versions ov2 ON ov2.version_id = origin.version_id
          WHERE origin.chunk_id = cl.chunk_id
            AND origin.version_id <> :version_id
            AND ov2.deleted_at IS NULL
      )
    """
)

_SELECT_REGAINED = text(
    """
    SELECT cl.claim_id, c.version_id AS from_version_id
    FROM claims cl
    JOIN chunks c ON c.chunk_id = cl.chunk_id
    WHERE cl.deployment_id = :deployment_id
      AND cl.doc_id = :doc_id
      AND NOT cl.is_current_testimony
      AND (c.version_id = :current_version_id
           OR EXISTS (SELECT 1 FROM chunk_claims cc
                      JOIN chunks cur ON cur.chunk_id = cc.chunk_id
                      WHERE cc.claim_id = cl.claim_id
                        AND cur.version_id = :current_version_id))
    """
)

_SELECT_RUN_EVENTS = text(
    """
    SELECT claim_id, doc_id, became_current, reason::text AS reason,
           from_extractor_version, from_version_id
    FROM testimony_currency_events
    WHERE reconciliation_id = :reconciliation_id
    """
)

_INSERT_CURRENCY_EVENTS = text(
    """
    WITH input AS (
        SELECT *
        FROM jsonb_to_recordset(CAST(:events AS jsonb)) AS event (
            event_id uuid,
            claim_id uuid,
            doc_id uuid,
            became_current boolean,
            reason text,
            from_extractor_version text,
            from_version_id uuid
        )
    ), inserted AS (
        INSERT INTO testimony_currency_events (
            event_id, deployment_id, claim_id, doc_id, reconciliation_id,
            became_current, reason, from_extractor_version, from_version_id
        )
        SELECT input.event_id, :deployment_id, input.claim_id, input.doc_id,
               :reconciliation_id, input.became_current,
               CAST(input.reason AS currency_reason),
               input.from_extractor_version, input.from_version_id
        FROM input
        WHERE NOT EXISTS (
            SELECT 1 FROM testimony_currency_events existing
            WHERE existing.claim_id = input.claim_id
              AND existing.reconciliation_id = :reconciliation_id
              AND existing.reason = CAST(input.reason AS currency_reason)
              AND existing.became_current = input.became_current
        )
        RETURNING event_id
    )
    SELECT count(*) FROM inserted
    """
).bindparams(bindparam("events", type_=JSON))

_UPDATE_CURRENCY_CACHES = text(
    """
    WITH input AS (
        SELECT *
        FROM jsonb_to_recordset(CAST(:claims AS jsonb)) AS claim (
            claim_id uuid,
            is_current boolean
        )
    )
    UPDATE claims
    SET is_current_testimony = input.is_current
    FROM input
    WHERE claims.claim_id = input.claim_id
      AND claims.is_current_testimony <> input.is_current
    """
).bindparams(bindparam("claims", type_=JSON))

_SELECT_AFFECTED_RELATIONS = text(
    """
    SELECT DISTINCT relation_id FROM relation_evidence
    WHERE claim_id = ANY(:claim_ids)
    """
)

_SELECT_AFFECTED_OBSERVATIONS = text(
    """
    SELECT DISTINCT observation_id FROM observation_evidence
    WHERE claim_id = ANY(:claim_ids)
    """
)

_RECOUNT_RELATIONS = text(
    """
    WITH fresh AS (
        SELECT requested.relation_id,
               count(DISTINCT evidence.doc_id) FILTER (
                   WHERE evidence.stance = 'supports'
                     AND claims.is_current_testimony
               ) AS supports,
               count(DISTINCT evidence.doc_id) FILTER (
                   WHERE evidence.stance = 'contradicts'
                     AND claims.is_current_testimony
               ) AS contradicts
        FROM unnest(CAST(:relation_ids AS uuid[])) AS requested(relation_id)
        LEFT JOIN relation_evidence evidence
          ON evidence.relation_id = requested.relation_id
        LEFT JOIN claims ON claims.claim_id = evidence.claim_id
        GROUP BY requested.relation_id
    )
    UPDATE relations r
    SET evidence_count = fresh.supports,
        contradict_count = fresh.contradicts,
        updated_at = now()
    FROM fresh
    WHERE r.relation_id = fresh.relation_id
      AND (r.evidence_count IS DISTINCT FROM fresh.supports
           OR r.contradict_count IS DISTINCT FROM fresh.contradicts)
    RETURNING r.relation_id
    """
)

_RECOUNT_OBSERVATIONS = text(
    """
    WITH fresh AS (
        SELECT requested.observation_id,
               count(DISTINCT evidence.doc_id) FILTER (
                   WHERE evidence.stance = 'supports'
                     AND claims.is_current_testimony
               ) AS supports,
               count(DISTINCT evidence.doc_id) FILTER (
                   WHERE evidence.stance = 'contradicts'
                     AND claims.is_current_testimony
               ) AS contradicts
        FROM unnest(CAST(:observation_ids AS uuid[]))
             AS requested(observation_id)
        LEFT JOIN observation_evidence evidence
          ON evidence.observation_id = requested.observation_id
        LEFT JOIN claims ON claims.claim_id = evidence.claim_id
        GROUP BY requested.observation_id
    )
    UPDATE observations o
    SET evidence_count = fresh.supports,
        contradict_count = fresh.contradicts,
        updated_at = now()
    FROM fresh
    WHERE o.observation_id = fresh.observation_id
      AND (o.evidence_count IS DISTINCT FROM fresh.supports
           OR o.contradict_count IS DISTINCT FROM fresh.contradicts)
    RETURNING o.observation_id
    """
)

_SELECT_ZERO_RELATIONS = text(
    """
    SELECT relation_id FROM relations
    WHERE deployment_id = :deployment_id
      AND relation_id = ANY(:relation_ids)
      AND evidence_count = 0
      AND invalidated_at IS NULL
      AND valid_until IS NULL
      AND NOT EXISTS (
          -- a fact under an open support_withdrawn review is the
          -- transcription-only branch: a reviewer decides, never mechanics
          SELECT 1 FROM review_queue q
          WHERE q.deployment_id = relations.deployment_id
            AND q.item_kind = 'support_withdrawn'
            AND q.status IN ('pending', 'deferred')
            AND q.candidate ->> 'fact_kind' = 'relation'
            AND q.candidate ->> 'fact_id' = relations.relation_id::text
      )
    """
)

_SELECT_ZERO_OBSERVATIONS = text(
    """
    SELECT observation_id FROM observations
    WHERE deployment_id = :deployment_id
      AND observation_id = ANY(:observation_ids)
      AND evidence_count = 0
      AND invalidated_at IS NULL
      AND NOT EXISTS (
          SELECT 1 FROM review_queue q
          WHERE q.deployment_id = observations.deployment_id
            AND q.item_kind = 'support_withdrawn'
            AND q.status IN ('pending', 'deferred')
            AND q.candidate ->> 'fact_kind' = 'observation'
            AND q.candidate ->> 'fact_id' = observations.observation_id::text
      )
    """
)

_CAP_RELATION = text(
    """
    UPDATE relations
    SET valid_until = coalesce(CAST(:boundary AS timestamptz), now())
    WHERE relation_id = :relation_id
      AND valid_until IS NULL
      AND invalidated_at IS NULL
    RETURNING relation_id
    """
)

_INVALIDATE_OBSERVATION = text(
    """
    UPDATE observations
    SET invalidated_at = now()
    WHERE observation_id = :observation_id AND invalidated_at IS NULL
    RETURNING observation_id
    """
)

_INSERT_RELATION_RETRACTION = text(
    """
    INSERT INTO relation_adjudications (
        adjudication_id, deployment_id, relation_id, outcome, method,
        features, adjudicator_version
    )
    SELECT :adjudication_id, :deployment_id, :fact_id,
           'retracted_source_removal', 'exact', :features, 'reconcile-2026.07'
    WHERE NOT EXISTS (
        SELECT 1 FROM relation_adjudications a
        WHERE a.relation_id = :fact_id
          AND a.outcome = 'retracted_source_removal'
          AND a.features ->> 'reconciliation_id' = :reconciliation_id
    )
    """
).bindparams(bindparam("features", type_=JSON))

_INSERT_OBSERVATION_RETRACTION = text(
    """
    INSERT INTO observation_adjudications (
        adjudication_id, deployment_id, observation_id, outcome, method,
        features, adjudicator_version
    )
    SELECT :adjudication_id, :deployment_id, :fact_id,
           'retracted_source_removal', 'exact', :features, 'reconcile-2026.07'
    WHERE NOT EXISTS (
        SELECT 1 FROM observation_adjudications a
        WHERE a.observation_id = :fact_id
          AND a.outcome = 'retracted_source_removal'
          AND a.features ->> 'reconciliation_id' = :reconciliation_id
    )
    """
).bindparams(bindparam("features", type_=JSON))

_INSERT_EVIDENCE_CHANGED = text(
    """
    INSERT INTO knowledge_refresh_queue (
        refresh_id, deployment_id, trigger, payload
    )
    SELECT :refresh_id, :deployment_id, 'evidence_changed', :payload
    WHERE NOT EXISTS (
        SELECT 1 FROM knowledge_refresh_queue q
        WHERE q.deployment_id = :deployment_id
          AND q.trigger = 'evidence_changed'
          AND q.payload ->> 'reconciliation_id' = :reconciliation_id
    )
    RETURNING refresh_id
    """
).bindparams(bindparam("payload", type_=JSON))

_TOMBSTONE_VERSION = text(
    """
    UPDATE document_versions SET deleted_at = coalesce(deleted_at, now())
    WHERE version_id = :version_id
    RETURNING deployment_id, doc_id, version_id
    """
)

_REPOINT_AFTER_VERSION_DELETE = text(
    """
    UPDATE documents d
    SET current_version_id = (
        SELECT v.version_id FROM document_versions v
        WHERE v.doc_id = d.doc_id
          AND v.deleted_at IS NULL
          AND v.status = 'ready'
        ORDER BY v.version_no DESC
        LIMIT 1
    )
    WHERE d.doc_id = :doc_id AND d.current_version_id = :version_id
    """
)

_TOMBSTONE_LINEAGE_BY_ID = text(
    """
    UPDATE documents SET deleted_at = now()
    WHERE doc_id = :doc_id AND deleted_at IS NULL
    """
)

_SELECT_READY_CYCLES = text(
    """
    SELECT y.cycle_id, y.failed_items
    FROM connector_sync_cycles y
    WHERE y.deployment_id = :deployment_id
      AND y.completed_at IS NOT NULL
      AND y.finalized_at IS NULL
      AND NOT EXISTS (
          SELECT 1
          FROM document_versions v
          JOIN processing_state w
            ON w.target_id = v.version_id
           AND w.target_kind = 'document_version'
          WHERE v.sync_cycle_id = y.cycle_id
            AND w.status IN ('pending', 'running', 'failed')
      )
      AND NOT EXISTS (
          SELECT 1
          FROM document_versions v
          JOIN chunks c ON c.version_id = v.version_id
          JOIN processing_state w
            ON w.deployment_id = y.deployment_id
           AND w.target_kind = 'chunk'
           AND w.target_id = c.chunk_id
           AND w.stage = 'extract_claims'
          WHERE v.sync_cycle_id = y.cycle_id
            AND w.status IN ('pending', 'running', 'failed', 'dead_letter')
      )
      AND NOT EXISTS (
          -- D88: when claim-grain normalize rows exist, they must finish
          -- (incl. dead_letter) before cycle finalization. Missing rows are
          -- intentional for pre-fanout serial normalize (legacy drain) — do
          -- not treat absence as incomplete.
          SELECT 1
          FROM document_versions v
          JOIN chunks c ON c.version_id = v.version_id
          JOIN claims cl ON cl.chunk_id = c.chunk_id
          JOIN processing_state w
            ON w.deployment_id = y.deployment_id
           AND w.target_kind = 'claim'
           AND w.target_id = cl.claim_id
           AND w.stage = 'normalize_relations'
          WHERE v.sync_cycle_id = y.cycle_id
            AND w.status IN ('pending', 'running', 'failed', 'dead_letter')
      )
    ORDER BY y.started_at
    """
)

_SELECT_CYCLE_LINEAGES = text(
    """
    SELECT DISTINCT doc_id FROM document_versions
    WHERE sync_cycle_id = :cycle_id
    """
)

_SELECT_TOMBSTONES_NEEDING_CASCADE = text(
    """
    SELECT d.doc_id FROM documents d
    WHERE d.deployment_id = :deployment_id
      AND d.deleted_at IS NOT NULL
      AND d.deleted_sync_cycle_id IS NOT NULL
      AND EXISTS (SELECT 1 FROM claims cl
                  WHERE cl.doc_id = d.doc_id AND cl.is_current_testimony)
    """
)

_SELECT_LINEAGE_CLAIMS = text(
    """
    SELECT claim_id FROM claims
    WHERE deployment_id = :deployment_id AND doc_id = :doc_id
    """
)

_SELECT_CLOSURE_BOUNDARY = text(
    """
    SELECT v.source_modified_at
    FROM documents d
    JOIN document_versions v ON v.version_id = d.current_version_id
    WHERE d.doc_id = :doc_id
    """
)

_CLAIM_FINALIZATION = text(
    """
    UPDATE connector_sync_cycles SET finalized_at = now()
    WHERE cycle_id = :cycle_id AND finalized_at IS NULL
    RETURNING cycle_id
    """
)
