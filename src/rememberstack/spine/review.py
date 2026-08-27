"""The review queue (D24, registries §8, lifecycle §4): reversible verdicts.

Clusters, not pairs; only the expected-impact middle band reaches humans;
every action appends a reversible, provenance-stamped record — a merge
verdict lands in `merge_events` (decided_by=human), a support_withdrawn
verdict lands as a currency event (restore) or an invalidation with its
adjudication (invalidate). `uncertain` is the only non-terminal outcome and
deliberately leaves the marker standing.
"""

from typing import Final
from uuid import UUID
from uuid import uuid4

from sqlalchemy import bindparam
from sqlalchemy import JSON
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine

from rememberstack.model import ReviewDecisionError
from rememberstack.model import ReviewItem
from rememberstack.ports.profile_refresher import ProfileRefresherPort
from rememberstack.spine.clustering import apply_merge

REVIEW_RECONCILIATION_NAMESPACE: Final = UUID("5e51e77e-0000-4000-8000-000000000000")


class ReviewQueue:
    """List, flag, and decide review items over an explicitly composed engine."""

    def __init__(
        self, *, engine: Engine, profile_refresher: ProfileRefresherPort
    ) -> None:
        """Bind review mutations and their evidence-derived projection."""
        self._engine = engine
        self._profile_refresher = profile_refresher

    def pending(
        self, *, deployment_id: UUID, limit: int = 20
    ) -> tuple[ReviewItem, ...]:
        """Open items ranked by expected impact (D24's routing score)."""
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    _SELECT_PENDING, {"deployment_id": deployment_id, "limit": limit}
                )
                .mappings()
                .all()
            )
        return tuple(ReviewItem.model_validate(dict(row)) for row in rows)

    def has_open_support_withdrawn(
        self, *, deployment_id: UUID, fact_kind: str, fact_id: UUID
    ) -> bool:
        """Whether an undecided support_withdrawn flag already marks the fact.

        The reconcile stage's retry guard: a replayed run must not stack a
        second flag on a fact the first attempt already queued.
        """
        with self._engine.connect() as connection:
            return (
                connection.execute(
                    _SELECT_OPEN_FLAG,
                    {
                        "deployment_id": deployment_id,
                        "fact_kind": fact_kind,
                        "fact_id": str(fact_id),
                    },
                ).scalar_one_or_none()
                is not None
            )

    def flag_support_withdrawn(
        self,
        *,
        deployment_id: UUID,
        fact_kind: str,
        fact_id: UUID,
        claim_id: UUID,
        diff: dict[str, object],
    ) -> UUID:
        """Queue the only support_withdrawn trigger (lifecycle §4): a new
        toolchain generation re-read the unchanged file and did not re-derive
        the claim — no mechanical verdict is derivable."""
        review_id = uuid4()
        with self._engine.begin() as connection:
            self._require_bound(
                connection=connection,
                deployment_id=deployment_id,
                fact_kind=fact_kind,
                fact_id=fact_id,
                claim_id=claim_id,
            )
            connection.execute(
                _INSERT_REVIEW,
                {
                    "review_id": review_id,
                    "deployment_id": deployment_id,
                    "item_kind": "support_withdrawn",
                    "candidate": {
                        "fact_kind": fact_kind,
                        "fact_id": str(fact_id),
                        "claim_id": str(claim_id),
                        "diff": diff,
                    },
                    "blast_radius": 1,
                    "confidence": 0.5,
                    "expected_impact": 0.5,
                },
            )
        return review_id

    def decide_merge(
        self,
        *,
        deployment_id: UUID,
        review_id: UUID,
        verdict: str,
        reviewer: str,
        note: str | None = None,
    ) -> tuple[UUID, ...]:
        """Apply a merge-cluster verdict: merge performs it, not_merge closes.

        Returns the merge-event ids a `merge` verdict produced (empty for
        `not_merge`). Every merge is decided_by=human and fully reversible.
        """
        if verdict not in ("merge", "not_merge"):
            raise ReviewDecisionError(
                f"verdict {verdict!r} is not valid for a merge_cluster item"
            )
        with self._engine.begin() as connection:
            item = self._claim_item(
                connection=connection,
                deployment_id=deployment_id,
                review_id=review_id,
                expected_kind="merge_cluster",
                verdict=verdict,
            )
            if item is None:
                return ()  # idempotent retry of the same verdict
            events: tuple[UUID, ...] = ()
            if verdict == "merge":
                candidate = _candidate(item=item)
                survivor = UUID(str(candidate["survivor_id"]))
                events = tuple(
                    apply_merge(
                        connection=connection,
                        deployment_id=deployment_id,
                        survivor_id=survivor,
                        absorbed_id=UUID(str(absorbed)),
                        trigger_lemmas=[str(candidate.get("trigger_lemma", ""))],
                        evidence={"review_id": str(review_id), "note": note},
                        blast_radius=int(str(item["blast_radius"])),
                        decided_by="human",
                    )
                    for absorbed in list(candidate["absorbed_ids"])  # type: ignore[arg-type]
                )
                events = tuple(event for event in events if event is not None)
            self._close(
                connection=connection,
                review_id=review_id,
                status="accepted" if verdict == "merge" else "rejected",
                verdict=verdict,
                note=note,
                reviewer=reviewer,
                result_decision_id=events[0] if events else None,
            )
        return events

    def decide_support_withdrawn(
        self,
        *,
        deployment_id: UUID,
        review_id: UUID,
        verdict: str,
        reviewer: str,
        note: str | None = None,
    ) -> None:
        """Apply a support_withdrawn triage verdict (lifecycle §4).

        restore_support: the old claim was right (the extractor regressed) —
        a currency event reinstates it and the fact's support recounts.
        invalidate_fact: the old claim was an extraction artifact — the fact
        leaves the current layer with a recorded adjudication.
        uncertain: non-terminal; the marker deliberately stands (deferred).
        """
        if verdict not in ("restore_support", "invalidate_fact", "uncertain"):
            raise ReviewDecisionError(
                f"verdict {verdict!r} is not valid for a support_withdrawn item"
            )
        fact_kind: str
        fact_id: UUID
        with self._engine.begin() as connection:
            item = self._claim_item(
                connection=connection,
                deployment_id=deployment_id,
                review_id=review_id,
                expected_kind="support_withdrawn",
                verdict=verdict,
            )
            if item is None:
                # The database verdict is already durable, but a prior process
                # may have failed after commit and before the derived profile
                # refresh. Re-read its fact coordinates so the identical retry
                # repairs that projection without duplicating ledger rows.
                item = dict(
                    connection.execute(
                        _SELECT_ITEM_LOCKED,
                        {"deployment_id": deployment_id, "review_id": review_id},
                    )
                    .mappings()
                    .one()
                )
                candidate = _candidate(item=item)
                fact_kind = str(candidate["fact_kind"])
                fact_id = UUID(str(candidate["fact_id"]))
            else:
                candidate = _candidate(item=item)
                fact_kind = str(candidate["fact_kind"])
                fact_id = UUID(str(candidate["fact_id"]))
                claim_id = UUID(str(candidate["claim_id"]))
                if verdict == "restore_support":
                    self._restore_support(
                        connection=connection,
                        deployment_id=deployment_id,
                        fact_kind=fact_kind,
                        fact_id=fact_id,
                        claim_id=claim_id,
                        review_id=review_id,
                    )
                elif verdict == "invalidate_fact":
                    self._invalidate_fact(
                        connection=connection,
                        deployment_id=deployment_id,
                        fact_kind=fact_kind,
                        fact_id=fact_id,
                        claim_id=claim_id,
                        review_id=review_id,
                    )
                self._close(
                    connection=connection,
                    review_id=review_id,
                    status="deferred" if verdict == "uncertain" else "accepted",
                    verdict=verdict,
                    note=note,
                    reviewer=reviewer,
                    result_decision_id=None,
                )
        if verdict != "uncertain":
            self._profile_refresher.refresh_for_facts(
                deployment_id=deployment_id,
                relation_ids=(fact_id,) if fact_kind == "relation" else (),
                observation_ids=(fact_id,) if fact_kind == "observation" else (),
                call_key=f"profile:review:{review_id}",
            )

    def _restore_support(
        self,
        *,
        connection: Connection,
        deployment_id: UUID,
        fact_kind: str,
        fact_id: UUID,
        claim_id: UUID,
        review_id: UUID,
    ) -> None:
        """The restore path: currency event + claim flag + support recount."""
        self._require_bound(
            connection=connection,
            deployment_id=deployment_id,
            fact_kind=fact_kind,
            fact_id=fact_id,
            claim_id=claim_id,
        )
        doc_id = connection.execute(
            _CLAIM_DOC, {"claim_id": claim_id, "deployment_id": deployment_id}
        ).scalar_one()
        already = connection.execute(
            _CURRENCY_EVENT_EXISTS,
            {"claim_id": claim_id, "reconciliation_id": review_id},
        ).scalar_one()
        if not already:  # review-keyed reconciliation: retries re-emit nothing
            connection.execute(
                _INSERT_CURRENCY_EVENT,
                {
                    "event_id": uuid4(),
                    "deployment_id": deployment_id,
                    "claim_id": claim_id,
                    "doc_id": doc_id,
                    "reconciliation_id": review_id,
                },
            )
        connection.execute(
            _RESTORE_CLAIM_CURRENCY,
            {"claim_id": claim_id, "deployment_id": deployment_id},
        )
        self._recount(connection=connection, fact_kind=fact_kind, fact_id=fact_id)
        # plant the D35 canary (lifecycle §4): no future extractor ships
        # while silently missing this claim again — the lifecycle eval
        # pack re-checks it per version. Idempotent per review.
        connection.execute(
            _PLANT_LIFECYCLE_CANARY,
            {
                "canary_id": uuid4(),
                "deployment_id": deployment_id,
                "description": (
                    f"restore_support review {review_id}: the claim must"
                    " remain current testimony"
                ),
                "input": {
                    "review_id": str(review_id),
                    "fact_kind": fact_kind,
                    "fact_id": str(fact_id),
                },
                "expected": {
                    "fact_kind": fact_kind,
                    "fact_id": str(fact_id),
                    "restored_claim_id": str(claim_id),
                },
                "review_id": str(review_id),
            },
        )

    def _require_bound(
        self,
        *,
        connection: Connection,
        deployment_id: UUID,
        fact_kind: str,
        fact_id: UUID,
        claim_id: UUID,
    ) -> None:
        """The claim and the fact must both belong to the deployment (D50):
        a candidate carrying foreign ids writes nothing (Codex review)."""
        claim_ok = connection.execute(
            _CLAIM_BOUND, {"claim_id": claim_id, "deployment_id": deployment_id}
        ).scalar_one()
        fact_statement = (
            _RELATION_BOUND if fact_kind == "relation" else _OBSERVATION_BOUND
        )
        fact_ok = connection.execute(
            fact_statement, {"fact_id": fact_id, "deployment_id": deployment_id}
        ).scalar_one()
        if not (claim_ok and fact_ok):
            raise ReviewDecisionError(
                "the claim or fact does not belong to this deployment"
            )

    def _invalidate_fact(
        self,
        *,
        connection: Connection,
        deployment_id: UUID,
        fact_kind: str,
        fact_id: UUID,
        claim_id: UUID,
        review_id: UUID,
    ) -> None:
        """The invalidate path: learned-wrong, recorded, out of the current layer."""
        if fact_kind == "relation":
            connection.execute(
                _INVALIDATE_RELATION,
                {"deployment_id": deployment_id, "relation_id": fact_id},
            )
            connection.execute(
                _INSERT_RELATION_ADJUDICATION,
                {
                    "adjudication_id": uuid4(),
                    "deployment_id": deployment_id,
                    "relation_id": fact_id,
                    "features": {
                        "action": "invalidate_fact",
                        "review_id": str(review_id),
                        "claim_id": str(claim_id),
                    },
                },
            )
        else:
            connection.execute(
                _INVALIDATE_OBSERVATION,
                {"deployment_id": deployment_id, "observation_id": fact_id},
            )
            connection.execute(
                _INSERT_OBSERVATION_ADJUDICATION,
                {
                    "adjudication_id": uuid4(),
                    "deployment_id": deployment_id,
                    "observation_id": fact_id,
                    "triggering_claim_id": claim_id,
                    "features": {
                        "action": "invalidate_fact",
                        "review_id": str(review_id),
                    },
                },
            )

    def _recount(
        self, *, connection: Connection, fact_kind: str, fact_id: UUID
    ) -> None:
        """The D54 lineage-distinct recount after a currency change."""
        statement = (
            _RECOUNT_RELATION if fact_kind == "relation" else _RECOUNT_OBSERVATION
        )
        connection.execute(statement, {"fact_id": fact_id})

    def _claim_item(
        self,
        *,
        connection: Connection,
        deployment_id: UUID,
        review_id: UUID,
        expected_kind: str,
        verdict: str,
    ) -> dict[str, object] | None:
        """Lock one open item of the expected kind, or raise a typed error.

        A retried IDENTICAL verdict on a closed item is an idempotent no-op
        (returns None — a lost CLI response can safely re-send); a DIFFERENT
        verdict on a closed item is refused.
        """
        row = (
            connection.execute(
                _SELECT_ITEM_LOCKED,
                {"deployment_id": deployment_id, "review_id": review_id},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise ReviewDecisionError(f"review item {review_id} does not exist")
        if row["item_kind"] != expected_kind:
            raise ReviewDecisionError(
                f"review item {review_id} is a {row['item_kind']}, "
                f"not a {expected_kind}"
            )
        if row["status"] not in ("pending", "deferred"):
            if row["verdict"] == verdict:
                return None  # idempotent retry
            raise ReviewDecisionError(
                f"review item {review_id} is already {row['status']} "
                f"with verdict {row['verdict']!r}"
            )
        return dict(row)

    def _close(
        self,
        *,
        connection: Connection,
        review_id: UUID,
        status: str,
        verdict: str,
        note: str | None,
        reviewer: str,
        result_decision_id: UUID | None,
    ) -> None:
        """Close (or defer) the item, APPENDING to the verdict history.

        The history lives in the candidate payload, so a later terminal
        verdict never erases an earlier deferral's rationale (append-only
        provenance, D24).
        """
        connection.execute(
            _CLOSE_REVIEW,
            {
                "review_id": review_id,
                "status": status,
                "verdict": verdict,
                "verdict_note": note,
                "assigned_to": reviewer,
                "result_decision_id": result_decision_id,
                "history_entry": {
                    "verdict": verdict,
                    "reviewer": reviewer,
                    "note": note,
                },
            },
        )


_SELECT_PENDING = text(
    """
    SELECT review_id, item_kind::text AS item_kind, candidate, blast_radius,
           confidence, expected_impact, status::text AS status, created_at
    FROM review_queue
    WHERE deployment_id = :deployment_id AND status IN ('pending', 'deferred')
    ORDER BY expected_impact DESC, created_at
    LIMIT :limit
    """
)

_SELECT_ITEM_LOCKED = text(
    """
    SELECT review_id, item_kind::text AS item_kind, candidate, blast_radius,
           status::text AS status, verdict::text AS verdict
    FROM review_queue
    WHERE deployment_id = :deployment_id AND review_id = :review_id
    FOR UPDATE
    """
)

_SELECT_OPEN_FLAG = text(
    """
    SELECT review_id FROM review_queue
    WHERE deployment_id = :deployment_id
      AND item_kind = 'support_withdrawn'
      AND status IN ('pending', 'deferred')
      AND candidate ->> 'fact_kind' = :fact_kind
      AND candidate ->> 'fact_id' = :fact_id
    LIMIT 1
    """
)

_PLANT_LIFECYCLE_CANARY = text(
    """
    INSERT INTO canary_cases (
        canary_id, deployment_id, suite, description, input, expected
    )
    SELECT :canary_id, :deployment_id, 'lifecycle', :description,
           :input, :expected
    WHERE NOT EXISTS (
        SELECT 1 FROM canary_cases c
        WHERE c.suite = 'lifecycle'
          AND c.input ->> 'review_id' = :review_id
    )
    """
).bindparams(bindparam("input", type_=JSON), bindparam("expected", type_=JSON))

_INSERT_REVIEW = text(
    """
    INSERT INTO review_queue (
        review_id, deployment_id, item_kind, candidate, blast_radius,
        confidence, expected_impact
    ) VALUES (
        :review_id, :deployment_id, :item_kind, :candidate, :blast_radius,
        :confidence, :expected_impact
    )
    """
).bindparams(bindparam("candidate", type_=JSON))

_CLOSE_REVIEW = text(
    """
    UPDATE review_queue
    SET status = :status, verdict = :verdict, verdict_note = :verdict_note,
        assigned_to = :assigned_to, result_decision_id = :result_decision_id,
        resolved_at = now(),
        candidate = jsonb_set(
            candidate,
            '{verdict_history}',
            coalesce(candidate->'verdict_history', '[]'::jsonb)
                || CAST(:history_entry AS jsonb),
            true
        )
    WHERE review_id = :review_id
    """
).bindparams(bindparam("history_entry", type_=JSON))

_CLAIM_DOC = text(
    """
    SELECT doc_id FROM claims
    WHERE claim_id = :claim_id AND deployment_id = :deployment_id
    """
)

_CLAIM_BOUND = text(
    """
    SELECT count(*) > 0 FROM claims
    WHERE claim_id = :claim_id AND deployment_id = :deployment_id
    """
)

_RELATION_BOUND = text(
    """
    SELECT count(*) > 0 FROM relations
    WHERE relation_id = :fact_id AND deployment_id = :deployment_id
    """
)

_OBSERVATION_BOUND = text(
    """
    SELECT count(*) > 0 FROM observations
    WHERE observation_id = :fact_id AND deployment_id = :deployment_id
    """
)

_CURRENCY_EVENT_EXISTS = text(
    """
    SELECT count(*) > 0 FROM testimony_currency_events
    WHERE claim_id = :claim_id AND reconciliation_id = :reconciliation_id
    """
)

_INSERT_CURRENCY_EVENT = text(
    """
    INSERT INTO testimony_currency_events (
        event_id, deployment_id, claim_id, doc_id, reconciliation_id,
        became_current, reason
    ) VALUES (
        :event_id, :deployment_id, :claim_id, :doc_id, :reconciliation_id,
        true, 'review_restored'
    )
    """
)

_RESTORE_CLAIM_CURRENCY = text(
    """
    UPDATE claims SET is_current_testimony = true
    WHERE claim_id = :claim_id AND deployment_id = :deployment_id
    """
)

_INVALIDATE_RELATION = text(
    """
    UPDATE relations SET invalidated_at = now(), updated_at = now()
    WHERE deployment_id = :deployment_id AND relation_id = :relation_id
      AND invalidated_at IS NULL
    """
)

_INVALIDATE_OBSERVATION = text(
    """
    UPDATE observations SET invalidated_at = now(), updated_at = now()
    WHERE deployment_id = :deployment_id AND observation_id = :observation_id
      AND invalidated_at IS NULL
    """
)

_INSERT_RELATION_ADJUDICATION = text(
    """
    INSERT INTO relation_adjudications (
        adjudication_id, deployment_id, relation_id, outcome, method,
        confidence, features, adjudicator_version, decided_by
    ) VALUES (
        :adjudication_id, :deployment_id, :relation_id, 'invalidated', 'exact',
        1.0, :features, 'review-2026.07', 'human'
    )
    """
).bindparams(bindparam("features", type_=JSON))

_INSERT_OBSERVATION_ADJUDICATION = text(
    """
    INSERT INTO observation_adjudications (
        adjudication_id, deployment_id, observation_id, outcome, method,
        confidence, triggering_claim_id, features, adjudicator_version,
        decided_by
    ) VALUES (
        :adjudication_id, :deployment_id, :observation_id, 'invalidated', 'exact',
        1.0, :triggering_claim_id, :features, 'review-2026.07', 'human'
    )
    """
).bindparams(bindparam("features", type_=JSON))

_RECOUNT_RELATION = text(
    """
    UPDATE relations SET evidence_count = (
        SELECT count(DISTINCT evidence.doc_id)
        FROM relation_evidence evidence
        JOIN claims ON claims.claim_id = evidence.claim_id
        WHERE evidence.relation_id = :fact_id
          AND evidence.stance = 'supports'
          AND claims.is_current_testimony
    ), updated_at = now()
    WHERE relation_id = :fact_id
    """
)

_RECOUNT_OBSERVATION = text(
    """
    UPDATE observations SET evidence_count = (
        SELECT count(DISTINCT evidence.doc_id)
        FROM observation_evidence evidence
        JOIN claims ON claims.claim_id = evidence.claim_id
        WHERE evidence.observation_id = :fact_id
          AND evidence.stance = 'supports'
          AND claims.is_current_testimony
    ), updated_at = now()
    WHERE observation_id = :fact_id
    """
)


def _candidate(*, item: dict[str, object]) -> dict[str, object]:
    """The item's candidate payload as a typed mapping."""
    candidate = item["candidate"]
    if not isinstance(candidate, dict):
        raise ReviewDecisionError("review item carries a malformed candidate")
    return candidate
