"""T0 entity resolution over the alias registry (D17): exact match or mint.

T0 is the cheapest cascade tier: an exact match on the normalized
`llm_canonical` lemma. A miss mints a new canonical entity (the novelty path)
with its alias, and every resolution — hit or mint — writes the immutable
mention and an append-only resolution verdict. Higher tiers (T1-T4 blocking
and adjudication) arrive with the truth-machinery phase.
"""

from typing import Final
import unicodedata
from uuid import UUID
from uuid import uuid4

from sqlalchemy import bindparam
from sqlalchemy import JSON
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.model import ClaimForNormalization
from rememberstack.model import EntityRef
from rememberstack.model import ResolvedEntity

T0_RESOLVER_VERSION: Final = "e3-resolver-t0-2026.07"
"""The T0-only resolver generation (walking skeleton; cascade tiers follow)."""


def normalized_lemma(*, surface: str) -> str:
    """The registry match key: accent-stripped, lowercased, whitespace-folded."""
    decomposed = unicodedata.normalize("NFKD", surface)
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(stripped.lower().split())


class EntityRegistry:
    """Alias-registry resolution and mention/verdict writes for one deployment."""

    def __init__(self, *, engine: Engine) -> None:
        """Bind the registry to the spine database."""
        self._engine = engine

    def resolve_t0(
        self, *, deployment_id: UUID, reference: EntityRef, claim: ClaimForNormalization
    ) -> ResolvedEntity:
        """Resolve one reference by exact lemma match, minting on a miss.

        One transaction writes the mention, the entity + alias when minted,
        and the append-only T0 verdict (D17: verdicts supersede, never edit).
        """
        lemma = normalized_lemma(surface=reference.name)
        with self._engine.begin() as connection:
            # serialize concurrent mints of one lemma (Codex review: without
            # this, two workers each mint an entity and facts never collapse):
            connection.execute(_LOCK_LEMMA, {"key": f"{deployment_id}:lemma:{lemma}"})
            existing = (
                connection.execute(
                    _SELECT_BY_LEMMA, {"deployment_id": deployment_id, "lemma": lemma}
                )
                .mappings()
                .one_or_none()
            )
            created = existing is None
            entity_id = existing["entity_id"] if existing else uuid4()
            if created:
                connection.execute(
                    _INSERT_ENTITY,
                    {
                        "entity_id": entity_id,
                        "deployment_id": deployment_id,
                        "canonical_name": reference.name,
                        "normalized_name": lemma,
                    },
                )
                connection.execute(
                    _INSERT_ALIAS,
                    {
                        "alias_id": uuid4(),
                        "deployment_id": deployment_id,
                        "entity_id": entity_id,
                        "alias_text": reference.name,
                        "lemma": lemma,
                    },
                )
            mention_id = uuid4()
            connection.execute(
                _INSERT_MENTION,
                {
                    "mention_id": mention_id,
                    "deployment_id": deployment_id,
                    "surface_form": reference.name,
                    "lemma": lemma,
                    "canonical_name_form": reference.name,
                    "claim_id": claim.claim_id,
                    "chunk_id": claim.chunk_id,
                    "doc_id": claim.doc_id,
                },
            )
            connection.execute(
                _INSERT_DECISION,
                {
                    "decision_id": uuid4(),
                    "deployment_id": deployment_id,
                    "mention_id": mention_id,
                    "entity_id": entity_id,
                    "is_new_entity": created,
                    "features": {"lemma": lemma},
                    "resolver_version": T0_RESOLVER_VERSION,
                },
            )
        return ResolvedEntity(entity_id=entity_id, created=created)

    def claim_already_normalized(self, *, claim_id: UUID) -> bool:
        """Whether the claim's facts already landed (evidence-backed replay).

        The marker is fact evidence, not mentions — a crash between subject
        resolution and the fact write must not strand the claim (Codex
        review). A claim that yielded no facts leaves no marker; its re-run on
        a crash-retry is bounded by the attempt limit and lands idempotently
        (mentions/verdicts are append-only transcript rows either way).
        """
        return claim_id in self.normalized_claim_ids(claim_ids=(claim_id,))

    def normalized_claim_ids(self, *, claim_ids: tuple[UUID, ...]) -> frozenset[UUID]:
        """Return every evidence-backed replay marker in one bounded read."""
        if not claim_ids:
            return frozenset()
        with self._engine.connect() as connection:
            rows = connection.execute(
                _SELECT_NORMALIZED_CLAIMS, {"claim_ids": list(claim_ids)}
            ).scalars()
            return frozenset(rows)


_LOCK_LEMMA = text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))")

_SELECT_BY_LEMMA = text(
    """
    SELECT aliases.entity_id FROM aliases
    JOIN entities ON entities.deployment_id = aliases.deployment_id
                 AND entities.entity_id = aliases.entity_id
    WHERE aliases.deployment_id = :deployment_id
      AND aliases.normalized_lemma = :lemma
      AND entities.status = 'active'  -- merged/retired never become endpoints;
                                      -- redirect-following arrives with merges
    ORDER BY aliases.first_seen
    LIMIT 1
    """
)

_INSERT_ENTITY = text(
    """
    INSERT INTO entities (
        entity_id, deployment_id, canonical_name, normalized_name
    ) VALUES (
        :entity_id, :deployment_id, :canonical_name, :normalized_name
    )
    """
)

_INSERT_ALIAS = text(
    """
    INSERT INTO aliases (
        alias_id, deployment_id, entity_id, alias_text, normalized_lemma, provenance
    ) VALUES (
        :alias_id, :deployment_id, :entity_id, :alias_text, :lemma, 'llm_canonical'
    )
    """
)

_INSERT_MENTION = text(
    """
    INSERT INTO mentions (
        mention_id, deployment_id, surface_form, normalized_lemma,
        canonical_name_form, claim_id, chunk_id, doc_id
    ) VALUES (
        :mention_id, :deployment_id, :surface_form, :lemma,
        :canonical_name_form, :claim_id, :chunk_id, :doc_id
    )
    """
)

_INSERT_DECISION = text(
    """
    INSERT INTO resolution_decisions (
        decision_id, deployment_id, mention_id, entity_id, method,
        confidence, is_new_entity, features, resolver_version
    ) VALUES (
        :decision_id, :deployment_id, :mention_id, :entity_id, 'T0',
        1.0, :is_new_entity, :features, :resolver_version
    )
    """
).bindparams(bindparam("features", type_=JSON))

_SELECT_NORMALIZED_CLAIMS = text(
    """
    SELECT claim_id FROM relation_evidence WHERE claim_id = ANY(:claim_ids)
    UNION
    SELECT claim_id FROM observation_evidence WHERE claim_id = ANY(:claim_ids)
    """
)
