"""The walking-skeleton eval pack (WP-1.7, D22): the S-subset as canaries.

The scenario battery is the retrieval golden set's skeleton (retrieval §11),
so these cases live in the `retrieval` suite: S1 (current fact via resolve +
lookup), S2 (semantic observation), S5 (the hydration chain), S39 (typed
negatives), plus the grain contract (claims answers are evidence grain, never
current-fact). Seeding writes the canaries; the evaluator replays each
scenario against a composed QueryEngine and judges the envelope.
"""

from typing import Final
from uuid import UUID
from uuid import uuid5

from sqlalchemy import bindparam
from sqlalchemy import JSON
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.model import CanaryCase
from rememberstack.model import Envelope
from rememberstack.model import Grain
from rememberstack.model import NegativeKind
from rememberstack.surfaces.query_engine import QueryEngine

_CANARY_NAMESPACE: Final = UUID("5ce1e701-0000-4000-8000-000000000000")

SKELETON_CANARIES: Final[tuple[dict[str, object], ...]] = (
    {
        "description": "S1: current employer via resolve + live works_for lookup",
        "input": {"scenario": "s1", "name": "Alice Novak", "predicate": "works_for"},
        "expected": {"label": "Alice Novak works for Acme", "min_evidence": 1},
    },
    {
        "description": "S2: semantic observation lookup (headcount)",
        "input": {"scenario": "s2", "name": "Acme", "property_query": "headcount"},
        "expected": {"label_contains": "600"},
    },
    {
        "description": "S5: hydration chain down to spans and sources",
        "input": {"scenario": "s5", "name": "Alice Novak", "predicate": "works_for"},
        "expected": {"min_evidence": 2, "min_sources": 1},
    },
    {
        "description": "S39: unknown entity vs known-empty are typed differently",
        "input": {"scenario": "s39", "unknown_name": "Contoso", "known_name": "Acme"},
        "expected": {},
    },
    {
        "description": "grain contract: claims answers are evidence grain",
        "input": {"scenario": "grain_contract", "query": "Alice Novak employer"},
        "expected": {"min_evidence": 1},
    },
)


def seed_skeleton_canaries(*, engine: Engine, deployment_id: UUID) -> None:
    """Insert or refresh the skeleton pack (stable per-deployment ids).

    This function is the canonical definition: re-seeding updates an existing
    canary in place, so a changed case can never silently keep evaluating its
    stale stored form (Codex review).
    """
    with engine.begin() as connection:
        for canary in SKELETON_CANARIES:
            connection.execute(
                _INSERT_CANARY,
                {
                    "canary_id": uuid5(
                        _CANARY_NAMESPACE, f"{deployment_id}:{canary['description']}"
                    ),
                    "deployment_id": deployment_id,
                    "description": canary["description"],
                    "input": canary["input"],
                    "expected": canary["expected"],
                },
            )


def make_skeleton_evaluator(*, query_engine: QueryEngine, deployment_id: UUID):
    """Build the retrieval-suite evaluator over one composed QueryEngine."""

    def evaluate(case: CanaryCase) -> bool:
        """Replay one scenario and judge its envelope against expectations."""
        scenario = case.input.get("scenario")
        if scenario == "s1":
            return _s1(query_engine, deployment_id, case)
        if scenario == "s2":
            return _s2(query_engine, deployment_id, case)
        if scenario == "s5":
            return _s5(query_engine, deployment_id, case)
        if scenario == "s39":
            return _s39(query_engine, deployment_id, case)
        if scenario == "grain_contract":
            return _grain_contract(query_engine, deployment_id, case)
        return False  # an unknown scenario never silently passes

    return evaluate


def _s1(engine: QueryEngine, deployment_id: UUID, case: CanaryCase) -> bool:
    """Resolve the person; the live relation answers with its label."""
    entity = _resolve_one(engine, deployment_id, str(case.input["name"]))
    if entity is None:
        return False
    answer = engine.lookup_relations(
        deployment_id=deployment_id,
        subject_entity_id=entity,
        predicate=str(case.input["predicate"]),
    )
    return (
        answer.grain is Grain.FACT
        and len(answer.facts) == 1
        and answer.facts[0].label == case.expected["label"]
        and answer.facts[0].evidence_count >= int(case.expected["min_evidence"])  # type: ignore[call-overload]
        and answer.facts[0].validity.invalidated_at is None
    )


def _s2(engine: QueryEngine, deployment_id: UUID, case: CanaryCase) -> bool:
    """Semantic property match over the entity's observation statements."""
    entity = _resolve_one(engine, deployment_id, str(case.input["name"]))
    if entity is None:
        return False
    answer = engine.lookup_observations(
        deployment_id=deployment_id,
        entity_id=entity,
        property_query=str(case.input["property_query"]),
    )
    # discrimination is bounded by the corpus: with richer golden corpora
    # (WP-0.6 tooling) this tightens to "only the matching property returns";
    # here the match must exist, rank in the top results, and drop nothing:
    return (
        answer.grain is Grain.FACT
        and answer.dropped_by_hydration == 0
        and any(
            str(case.expected["label_contains"]) in fact.label
            for fact in answer.facts[:3]
        )
    )


def _s5(engine: QueryEngine, deployment_id: UUID, case: CanaryCase) -> bool:
    """The full chain: relation → evidence spans → source handles."""
    entity = _resolve_one(engine, deployment_id, str(case.input["name"]))
    if entity is None:
        return False
    relations = engine.lookup_relations(
        deployment_id=deployment_id,
        subject_entity_id=entity,
        predicate=str(case.input["predicate"]),
    )
    if not relations.facts:
        return False
    hydrated = engine.hydrate_relation(
        deployment_id=deployment_id, relation_id=relations.facts[0].fact_id
    )
    source_docs = {source.doc_id for source in hydrated.sources}
    return (
        hydrated.grain is Grain.COMPOSITE
        and len(hydrated.evidence) >= int(case.expected["min_evidence"])  # type: ignore[call-overload]
        and len(hydrated.sources) >= int(case.expected["min_sources"])  # type: ignore[call-overload]
        # provenance linkage (Codex review): every claim's offsets are
        # span-coherent and its document is among the returned sources:
        and all(
            claim.char_end - claim.char_start == len(claim.source_span)
            and claim.doc_id in source_docs
            for claim in hydrated.evidence
        )
    )


def _s39(engine: QueryEngine, deployment_id: UUID, case: CanaryCase) -> bool:
    """Unknown entity and known-empty carry their distinct typed negatives."""
    unknown = engine.resolve(
        deployment_id=deployment_id, name=str(case.input["unknown_name"])
    )
    if unknown.negative is None or unknown.negative.kind is not (
        NegativeKind.UNKNOWN_ENTITY
    ):
        return False
    known = _resolve_one(engine, deployment_id, str(case.input["known_name"]))
    if known is None:
        return False
    empty = engine.lookup_relations(
        deployment_id=deployment_id, subject_entity_id=known, predicate="reports_to"
    )
    return (
        empty.negative is not None and empty.negative.kind is NegativeKind.KNOWN_EMPTY
    )


def _grain_contract(engine: QueryEngine, deployment_id: UUID, case: CanaryCase) -> bool:
    """Claims answers are evidence grain — never a current-fact answer.

    Substance is required (Codex review): an implementation returning empty
    evidence for every query must fail, and every returned claim must be
    current testimony (the default channel's contract).
    """
    answer: Envelope = engine.search_claims(
        deployment_id=deployment_id, query=str(case.input["query"])
    )
    return (
        answer.grain is Grain.EVIDENCE
        and not answer.facts
        and len(answer.evidence) >= int(case.expected["min_evidence"])  # type: ignore[call-overload]
        and all(claim.is_current_testimony for claim in answer.evidence)
    )


def _resolve_one(engine: QueryEngine, deployment_id: UUID, name: str) -> UUID | None:
    """Resolve to exactly one current entity, or None."""
    resolved = engine.resolve(deployment_id=deployment_id, name=name)
    if len(resolved.entities) != 1:
        return None
    return resolved.entities[0].entity_id


_INSERT_CANARY = text(
    """
    INSERT INTO canary_cases (
        canary_id, deployment_id, suite, description, input, expected
    ) VALUES (
        :canary_id, :deployment_id, 'retrieval', :description, :input, :expected
    )
    ON CONFLICT (canary_id) DO UPDATE
        SET description = EXCLUDED.description,
            input = EXCLUDED.input,
            expected = EXCLUDED.expected
    """
).bindparams(bindparam("input", type_=JSON), bindparam("expected", type_=JSON))
