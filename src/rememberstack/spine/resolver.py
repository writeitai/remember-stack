"""The full ER cascade (D17/D20): T0 exact → T1/T2 blocking → T3 → T4 → mint.

Block-loose / decide-tight: T1 (trigram) and T2 (Daitch-Mokotoff phonetic)
only GENERATE candidates; decisions are T0 (exact), T3 (embedding band), and
T4 (LLM adjudication, small → frontier escalation). A near-miss is escalated,
never auto-rejected. Every verdict lands append-only in
`resolution_decisions` with its tier, scores, and the resolver version whose
thresholds were in force. Registry-self-contained: no external authority
tier (D20).
"""

from typing import Final
from uuid import UUID
from uuid import uuid4

from sqlalchemy import bindparam
from sqlalchemy import JSON
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine

from rememberstack.core.embedding_input_policy import embedding_text_hash
from rememberstack.model import AdjudicationVerdict
from rememberstack.model import ClaimForNormalization
from rememberstack.model import EmbeddingRequest
from rememberstack.model import EntityRef
from rememberstack.model import ModelRequest
from rememberstack.model import ResolutionCandidate
from rememberstack.model import ResolvedEntity
from rememberstack.model import ResolverConfig
from rememberstack.ports.cost_meter import CostMeterPort
from rememberstack.ports.model_provider import ModelProviderPort
from rememberstack.ports.p1_index import ENTITY_INPUT_POLICY
from rememberstack.ports.p1_index import P1_VECTOR_DIMENSIONS
from rememberstack.spine.entity_eligibility import surface_appears_in_claim
from rememberstack.spine.entity_registry import normalized_lemma


class ResolverVersionConflictError(Exception):
    """A resolver version re-registered with a different definition (D22)."""


class UnregisteredEntityTypeError(Exception):
    """Mint refused: emitted type is not in the deployment entity_types registry (D86)."""


RESOLVER_VERSION: Final = "resolver-2026.07b"
"""The cascade generation whose thresholds stamp every decision (D17/D22).
07b pins T4 temperature=0.0 — generation parameters are part of provenance."""

_T4_PROMPT: Final = """You adjudicate entity identity for a memory system.
Are these the same real-world entity? Answer strictly from the evidence given.

MENTION: {mention!r}
CLAIM CONTEXT: {context}

CANDIDATE: {candidate!r}

Same entity?"""


class CascadeResolver:
    """T0-T4 resolution over one deployment's registry, minting on no-match."""

    def __init__(
        self,
        *,
        engine: Engine,
        model_provider: ModelProviderPort,
        config: ResolverConfig,
        embedding_model: str,
        small_model: str,
        frontier_model: str,
    ) -> None:
        """Bind the cascade to the registry and its T3/T4 model seats.

        Model seats follow the port-default principle (D70's pattern): the
        adjudicator ladder is deployment configuration, measured per phase.
        """
        self._engine = engine
        self._model_provider = model_provider
        self._config = config
        self._embedding_model = embedding_model
        self._small_model = small_model
        self._frontier_model = frontier_model
        self._registered = False
        self._last_rejection: tuple[str, float, dict[str, object]] | None = None

    def resolve(
        self,
        *,
        deployment_id: UUID,
        reference: EntityRef,
        claim: ClaimForNormalization,
        meter: CostMeterPort | None = None,
        call_key: str = "resolve",
    ) -> ResolvedEntity:
        """Run the cascade for one reference; mint when nothing matches.

        Stops at the first confident decision. The mention and the
        append-only verdict (tier, scores, config version) are written in the
        same transaction as any mint. Cross-SURFACE duplicate mints (two
        distinct variants racing on an empty registry) are deliberately not
        serialized here — that is the clustering/merge machinery's job
        (registries §6, WP-2.2); the lemma lock prevents the same-lemma race.
        """
        self._ensure_registered(deployment_id=deployment_id)
        lemma = normalized_lemma(surface=reference.name)
        with self._engine.begin() as connection:
            connection.execute(_LOCK_LEMMA, {"key": f"{deployment_id}:lemma:{lemma}"})
            exact = (
                connection.execute(
                    _T0_EXACT, {"deployment_id": deployment_id, "lemma": lemma}
                )
                .mappings()
                .one_or_none()
            )
            if exact is not None:
                return self._record(
                    connection=connection,
                    deployment_id=deployment_id,
                    reference=reference,
                    claim=claim,
                    lemma=lemma,
                    entity_id=exact["entity_id"],
                    method="T0",
                    confidence=1.0,
                    features={"lemma": lemma},
                    created=False,
                )
            candidates = self._blocked_candidates(
                connection=connection, deployment_id=deployment_id, lemma=lemma
            )
            decision = self._decide(
                connection=connection,
                deployment_id=deployment_id,
                reference=reference,
                claim=claim,
                candidates=candidates,
                meter=meter,
                call_key=call_key,
            )
            if decision is not None:
                candidate, method, confidence, features = decision
                return self._record(
                    connection=connection,
                    deployment_id=deployment_id,
                    reference=reference,
                    claim=claim,
                    lemma=lemma,
                    entity_id=candidate.entity_id,
                    method=method,
                    confidence=confidence,
                    features=features,
                    created=False,
                )
            return self._mint(
                connection=connection,
                deployment_id=deployment_id,
                reference=reference,
                claim=claim,
                lemma=lemma,
                considered=candidates,
                meter=meter,
                call_key=call_key,
            )

    def _ensure_registered(self, *, deployment_id: UUID) -> None:
        """Verify the in-force config IS the registered resolver version.

        Registers on first use; a version whose stored definition differs
        from this config is a hard error — thresholds are immutable per
        version (D22): change the numbers, mint a new version string.
        """
        if self._registered:
            return
        seed_resolver_version(
            engine=self._engine, deployment_id=deployment_id, config=self._config
        )
        self._registered = True

    def judge_pair(
        self,
        *,
        surface_a: str,
        surface_b: str,
        entity_type: str,
        context_a: str | None,
        context_b: str | None,
    ) -> tuple[bool, str]:
        """The cascade's decision function over one golden pair (D22).

        Registry-free: measures whether the tiers would identify the two
        surfaces — lemma equality (T0), blocking reachability (T1/T2; a pair
        blocking cannot reach is a no_match by the recall ceiling), the T3
        band over the two surface embeddings, then T4 with both contexts.
        Returns (match, deciding_tier).
        """
        lemma_a = normalized_lemma(surface=surface_a)
        lemma_b = normalized_lemma(surface=surface_b)
        if lemma_a == lemma_b:
            return True, "T0"
        with self._engine.connect() as connection:
            reachable = connection.execute(
                _PAIR_REACHABLE,
                {"a": lemma_a, "b": lemma_b, "floor": self._config.trigram_floor},
            ).scalar_one()
        if not reachable:
            return False, "blocking"
        thresholds = self._config.default_thresholds
        vectors = self._model_provider.embed(
            request=EmbeddingRequest(
                model=self._embedding_model,
                texts=(surface_a, surface_b),
                dimensions=P1_VECTOR_DIMENSIONS,
            )
        ).vectors
        score = _cosine(vectors[0], vectors[1])
        if score >= thresholds.t3_accept:
            return True, "T3"
        if score <= thresholds.t3_reject:
            return False, "T3"
        prompt = _T4_PROMPT.format(
            mention=surface_b, context=context_b or "(none)", candidate=surface_a
        )
        if context_a:
            prompt += f"\nCANDIDATE CONTEXT: {context_a}"
        verdict = self._model_provider.generate(
            request=ModelRequest(
                model=self._small_model, prompt=prompt, temperature=0.0
            ),
            response_type=AdjudicationVerdict,
        )
        if verdict.output.confidence >= thresholds.t4_small_confidence_floor:
            return verdict.output.match, "T4_small"
        frontier = self._model_provider.generate(
            request=ModelRequest(
                model=self._frontier_model, prompt=prompt, temperature=0.0
            ),
            response_type=AdjudicationVerdict,
        )
        return frontier.output.match, "T4_frontier"

    def _blocked_candidates(
        self, *, connection: Connection, deployment_id: UUID, lemma: str
    ) -> tuple[ResolutionCandidate, ...]:
        """T1 trigram + T2 phonetic candidate generation (never a decision)."""
        rows = (
            connection.execute(
                _T1_T2_BLOCK,
                {
                    "deployment_id": deployment_id,
                    "lemma": lemma,
                    "floor": self._config.trigram_floor,
                    "limit": self._config.blocking_limit,
                },
            )
            .mappings()
            .all()
        )
        return tuple(
            ResolutionCandidate(
                entity_id=row["entity_id"],
                canonical_name=row["canonical_name"],
                blocking_tier=row["blocking_tier"],
                trigram_score=row["trigram_score"],
            )
            for row in rows
        )

    def _decide(
        self,
        *,
        connection: Connection,
        deployment_id: UUID,
        reference: EntityRef,
        claim: ClaimForNormalization,
        candidates: tuple[ResolutionCandidate, ...],
        meter: CostMeterPort | None,
        call_key: str,
    ) -> tuple[ResolutionCandidate, str, float, dict[str, object]] | None:
        """T3 embedding bands, then T4 adjudication for the ambiguous band."""
        if not candidates:
            return None
        thresholds = self._config.default_thresholds
        scored = self._t3_scores(
            connection=connection,
            deployment_id=deployment_id,
            reference=reference,
            candidates=candidates,
            meter=meter,
            call_key=f"{call_key}:t3",
        )
        ordered = sorted(
            scored,
            key=lambda item: item[1] if item[1] is not None else 0.0,
            reverse=True,
        )
        adjudicated = 0
        for candidate, score in ordered:
            if score is not None and score >= thresholds.t3_accept:
                return (
                    candidate,
                    "T3",
                    score,
                    {
                        "blocking_tier": candidate.blocking_tier,
                        "embedding_score": score,
                    },
                )
            if score is not None and score <= thresholds.t3_reject:
                self._last_rejection = ("T3", score, {"embedding_score": score})
                continue  # confidently not THIS candidate; others get a look
            # ambiguous band — or no stored profile vector, which must
            # ESCALATE, never count as a confident non-match (Codex review):
            if adjudicated >= self._config.t4_max_candidates:
                break
            adjudicated += 1
            verdict, seat, model = self._t4(
                reference=reference,
                claim=claim,
                candidate=candidate,
                meter=meter,
                call_key=f"{call_key}:t4:{candidate.entity_id}",
            )
            if verdict.match:
                return (
                    candidate,
                    seat,
                    verdict.confidence,
                    {
                        "blocking_tier": candidate.blocking_tier,
                        "embedding_score": score,
                        "model": model,
                        "rationale": verdict.rationale,
                    },
                )
            self._last_rejection = (
                seat,
                verdict.confidence,
                {"model": model, "rationale": verdict.rationale},
            )
        return None

    def _t3_scores(
        self,
        *,
        connection: Connection,
        deployment_id: UUID,
        reference: EntityRef,
        candidates: tuple[ResolutionCandidate, ...],
        meter: CostMeterPort | None,
        call_key: str,
    ) -> tuple[tuple[ResolutionCandidate, float | None], ...]:
        """Cosine similarity against candidate profiles; None = no profile.

        A missing/stale profile vector is AMBIGUITY (route to T4), never a
        confident non-match (Codex review).
        """
        query_vector = self._embed(
            surface=reference.name, meter=meter, call_key=call_key
        )
        by_id = {
            row["entity_id"]: float(row["score"])
            for row in connection.execute(
                _ENTITY_VECTOR_SCORES,
                {
                    "deployment_id": deployment_id,
                    "entity_ids": [candidate.entity_id for candidate in candidates],
                    "embedding_model": self._embedding_model,
                    "input_policy": ENTITY_INPUT_POLICY,
                    "query_vector": _vector_literal(query_vector),
                },
            ).mappings()
        }
        return tuple(
            (candidate, by_id.get(candidate.entity_id)) for candidate in candidates
        )

    def _t4(
        self,
        *,
        reference: EntityRef,
        claim: ClaimForNormalization,
        candidate: ResolutionCandidate,
        meter: CostMeterPort | None,
        call_key: str,
    ) -> tuple[AdjudicationVerdict, str, str]:
        """T4 small-model adjudication, escalating to frontier below the floor."""
        prompt = _T4_PROMPT.format(
            mention=reference.name,
            context=claim.claim_text,
            candidate=candidate.canonical_name,
        )
        verdict_call = self._model_provider.generate(
            request=ModelRequest(
                model=self._small_model, prompt=prompt, temperature=0.0
            ),
            response_type=AdjudicationVerdict,
        )
        if meter is not None:
            meter.record(
                call_key=f"{call_key}:small", tier="T4_small", usage=verdict_call.usage
            )
        verdict = verdict_call.output
        thresholds = self._config.default_thresholds
        if verdict.confidence >= thresholds.t4_small_confidence_floor:
            return verdict, "T4_small", self._small_model
        frontier_call = self._model_provider.generate(
            request=ModelRequest(
                model=self._frontier_model, prompt=prompt, temperature=0.0
            ),
            response_type=AdjudicationVerdict,
        )
        if meter is not None:
            meter.record(
                call_key=f"{call_key}:frontier",
                tier="T4_frontier",
                usage=frontier_call.usage,
            )
        return frontier_call.output, "T4_frontier", self._frontier_model

    def _mint(
        self,
        *,
        connection: Connection,
        deployment_id: UUID,
        reference: EntityRef,
        claim: ClaimForNormalization,
        lemma: str,
        considered: tuple[ResolutionCandidate, ...],
        meter: CostMeterPort | None,
        call_key: str,
    ) -> ResolvedEntity:
        """Create the canonical entity + alias and index its T3 profile."""
        entity_id = uuid4()
        # the mint verdict records the tier that DECIDED novelty: T0 when
        # nothing blocked, else the rejecting tier's method and confidence
        # (Codex review — the audit trail keeps the actual path):
        rejection = self._last_rejection if considered else None
        self._last_rejection = None
        method, confidence, extra = rejection or ("T0", 1.0, {})
        connection.execute(
            _INSERT_ENTITY,
            {
                "entity_id": entity_id,
                "deployment_id": deployment_id,
                "canonical_name": reference.name,
                "normalized_name": lemma,
            },
        )
        vector = self._embed(
            surface=reference.name, meter=meter, call_key=f"{call_key}:mint"
        )
        connection.execute(
            _UPDATE_ENTITY_EMBEDDING,
            {
                "entity_id": entity_id,
                "deployment_id": deployment_id,
                "embedding": _vector_literal(vector),
                "embedding_model": self._embedding_model,
                "input_policy": ENTITY_INPUT_POLICY,
                "text_hash": embedding_text_hash(reference.name),
            },
        )
        return self._record(
            connection=connection,
            deployment_id=deployment_id,
            reference=reference,
            claim=claim,
            lemma=lemma,
            entity_id=entity_id,
            method=method,
            confidence=confidence,
            features={
                "lemma": lemma,
                "novelty": True,
                "considered": [str(c.entity_id) for c in considered],
                **extra,
            },
            created=True,
        )

    def _record(
        self,
        *,
        connection: Connection,
        deployment_id: UUID,
        reference: EntityRef,
        claim: ClaimForNormalization,
        lemma: str,
        entity_id: UUID,
        method: str,
        confidence: float,
        features: dict[str, object],
        created: bool,
    ) -> ResolvedEntity:
        """Write the mention + append-only verdict; return the resolution."""
        mention_id = uuid4()
        emitted = reference.mention_surface()
        if surface_appears_in_claim(surface=emitted, claim_text=claim.claim_text):
            source_text = emitted
        elif surface_appears_in_claim(
            surface=reference.name, claim_text=claim.claim_text
        ):
            source_text = reference.name
        else:
            source_text = None
        surface = source_text if source_text is not None else reference.name
        surface_lemma = normalized_lemma(surface=surface)
        connection.execute(
            _INSERT_MENTION,
            {
                "mention_id": mention_id,
                "deployment_id": deployment_id,
                "surface_form": surface,
                "lemma": surface_lemma,
                "canonical_name_form": reference.name,
                "claim_id": claim.claim_id,
                "chunk_id": claim.chunk_id,
                "doc_id": claim.doc_id,
            },
        )
        self._upsert_alias(
            connection=connection,
            deployment_id=deployment_id,
            entity_id=entity_id,
            alias_text=reference.name,
            lemma=lemma,
            provenance="llm_canonical",
        )
        if source_text is not None:
            self._upsert_alias(
                connection=connection,
                deployment_id=deployment_id,
                entity_id=entity_id,
                alias_text=source_text,
                lemma=normalized_lemma(surface=source_text),
                provenance="source",
            )
        self.refresh_generic_identifier_guard(
            connection=connection, deployment_id=deployment_id, lemma=lemma
        )
        if source_text is not None:
            source_lemma = normalized_lemma(surface=source_text)
            if source_lemma != lemma:
                self.refresh_generic_identifier_guard(
                    connection=connection,
                    deployment_id=deployment_id,
                    lemma=source_lemma,
                )
        connection.execute(
            _INSERT_DECISION,
            {
                "decision_id": uuid4(),
                "deployment_id": deployment_id,
                "mention_id": mention_id,
                "entity_id": entity_id,
                "method": method,
                "confidence": confidence,
                "is_new_entity": created,
                "features": features,
                "resolver_version": self._config.resolver_version,
            },
        )
        connection.execute(  # keep the blast-radius input warm (registries §6)
            _BUMP_MENTION_COUNT,
            {"deployment_id": deployment_id, "entity_id": entity_id},
        )
        return ResolvedEntity(entity_id=entity_id, created=created)

    def _upsert_alias(
        self,
        *,
        connection: Connection,
        deployment_id: UUID,
        entity_id: UUID,
        alias_text: str,
        lemma: str,
        provenance: str,
    ) -> None:
        """Insert or refresh one alias row (source or llm_canonical)."""
        connection.execute(
            _UPSERT_ALIAS,
            {
                "alias_id": uuid4(),
                "deployment_id": deployment_id,
                "entity_id": entity_id,
                "alias_text": alias_text,
                "lemma": lemma,
                "provenance": provenance,
            },
        )

    def refresh_generic_identifier_guard(
        self,
        *,
        connection: Connection,
        deployment_id: UUID,
        lemma: str,
        distinct_floor: int = 2,
    ) -> None:
        """Recount how many entities share ``lemma`` and upsert the guard row.

        Down-weighted when the lemma points at ``distinct_floor`` or more
        ids. Called from resolve so I.5 can trust the table for T1/T2.
        """
        connection.execute(
            _UPSERT_GENERIC_GUARD,
            {"deployment_id": deployment_id, "lemma": lemma, "floor": distinct_floor},
        )

    def _embed(
        self, *, surface: str, meter: CostMeterPort | None, call_key: str
    ) -> tuple[float, ...]:
        """One profile/query embedding through the configured port (D63)."""
        response = self._model_provider.embed(
            request=EmbeddingRequest(
                model=self._embedding_model,
                texts=(surface,),
                dimensions=P1_VECTOR_DIMENSIONS,
            )
        )
        if meter is not None:
            meter.record(call_key=call_key, tier="T3", usage=response.usage)
        return response.vectors[0]


def seed_resolver_version(
    *, engine: Engine, deployment_id: UUID, config: ResolverConfig
) -> None:
    """Register the cascade configuration once (immutable per version, D22).

    Re-seeding an identical definition is a no-op; a DIFFERENT definition
    under the same version string is a hard error — change the numbers, mint
    a new version. Thresholds are starting points until curves exist.
    """
    definition = _config_definition(config=config)
    stored = _stored_config(
        engine=engine,
        deployment_id=deployment_id,
        resolver_version=config.resolver_version,
    )
    if stored is not None:
        if stored != definition:
            raise ResolverVersionConflictError(
                f"resolver version {config.resolver_version!r} already registered "
                "with a different definition; mint a new version string"
            )
        return
    with engine.begin() as connection:
        connection.execute(
            _SEED_RESOLVER_VERSION,
            {
                "deployment_id": deployment_id,
                "resolver_version": config.resolver_version,
                **definition,
            },
        )


def _stored_config(
    *, engine: Engine, deployment_id: UUID, resolver_version: str
) -> dict[str, object] | None:
    """The registered definition for a version, or None if unregistered."""
    with engine.connect() as connection:
        row = (
            connection.execute(
                _SELECT_RESOLVER_VERSION,
                {"deployment_id": deployment_id, "resolver_version": resolver_version},
            )
            .mappings()
            .one_or_none()
        )
    if row is None:
        return None
    return {
        "tier_config": row["tier_config"],
        "thresholds_by_type": row["thresholds_by_type"],
    }


def _config_definition(*, config: ResolverConfig) -> dict[str, object]:
    """The comparable stored form of one in-memory config."""
    return {
        "tier_config": {
            "order": ["T0", "T1", "T2", "T3", "T4_small", "T4_frontier"],
            "trigram_floor": config.trigram_floor,
            "blocking_limit": config.blocking_limit,
            "t4_max_candidates": config.t4_max_candidates,
        },
        "thresholds_by_type": {
            "default": config.default_thresholds.model_dump(),
            **{
                entity_type: thresholds.model_dump()
                for entity_type, thresholds in config.thresholds_by_type.items()
            },
        },
    }


def _cosine(a: tuple[float, ...], b: tuple[float, ...] | None) -> float:
    """Cosine similarity; a candidate without a profile vector scores 0."""
    if b is None or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _vector_literal(vector: tuple[float, ...]) -> str:
    """Serialize the fixed D94 vector for PostgreSQL's vector input type."""
    if len(vector) != P1_VECTOR_DIMENSIONS:
        raise ValueError(
            f"P1 vector has {len(vector)} dimensions; expected {P1_VECTOR_DIMENSIONS}"
        )
    return "[" + ",".join(repr(value) for value in vector) + "]"


_LOCK_LEMMA = text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))")

_PAIR_REACHABLE = text(
    """
    SELECT similarity(:a, :b) >= :floor
        OR daitch_mokotoff(:a) && daitch_mokotoff(:b)
    """
)

_T0_EXACT = text(
    """
    SELECT aliases.entity_id FROM aliases
    JOIN entities ON entities.deployment_id = aliases.deployment_id
                 AND entities.entity_id = aliases.entity_id
    WHERE aliases.deployment_id = :deployment_id
      AND aliases.normalized_lemma = :lemma
      AND entities.status = 'active'
    ORDER BY aliases.first_seen
    LIMIT 1
    """
)

_T1_T2_BLOCK = text(
    """
    WITH t1 AS (
        SELECT DISTINCT ON (aliases.entity_id)
               aliases.entity_id, similarity(aliases.normalized_lemma, :lemma) AS score
        FROM aliases
        WHERE aliases.deployment_id = :deployment_id
          AND similarity(aliases.normalized_lemma, :lemma) >= :floor
        ORDER BY aliases.entity_id, score DESC
    ),
    t2 AS (
        SELECT DISTINCT aliases.entity_id
        FROM aliases
        WHERE aliases.deployment_id = :deployment_id
          AND daitch_mokotoff(aliases.normalized_lemma)
              && daitch_mokotoff(:lemma)
    )
    SELECT entities.entity_id, entities.canonical_name,
           coalesce(t1.score, 0.0) AS trigram_score,
           CASE WHEN t1.entity_id IS NOT NULL THEN 'T1' ELSE 'T2' END
               AS blocking_tier
    FROM entities
    LEFT JOIN t1 ON t1.entity_id = entities.entity_id
    LEFT JOIN t2 ON t2.entity_id = entities.entity_id
    WHERE entities.deployment_id = :deployment_id
      AND entities.status = 'active'
      AND (t1.entity_id IS NOT NULL OR t2.entity_id IS NOT NULL)
    ORDER BY coalesce(t1.score, 0.0) DESC
    LIMIT :limit
    """
)

_ENTITY_VECTOR_SCORES = text(
    """
    SELECT entity_id,
           1.0 - (embedding <=> CAST(:query_vector AS vector)) AS score
    FROM entities
    WHERE deployment_id = :deployment_id
      AND entity_id = ANY(CAST(:entity_ids AS uuid[]))
      AND embedding IS NOT NULL
      AND embedding_model = :embedding_model
      AND embedding_input_policy_version = :input_policy
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

_UPSERT_ALIAS = text(
    """
    INSERT INTO aliases (
        alias_id, deployment_id, entity_id, alias_text, normalized_lemma, provenance
    ) VALUES (
        :alias_id, :deployment_id, :entity_id, :alias_text, :lemma, :provenance
    )
    ON CONFLICT (deployment_id, entity_id, normalized_lemma, provenance)
    DO UPDATE SET last_seen = now(), alias_text = EXCLUDED.alias_text
    """
)

_UPSERT_GENERIC_GUARD = text(
    """
    INSERT INTO generic_identifier_guard (
        deployment_id, normalized_lemma, distinct_entity_count,
        is_downweighted, reason, evaluated_at
    )
    SELECT :deployment_id, :lemma, COUNT(DISTINCT entity_id),
           COUNT(DISTINCT entity_id) >= :floor, 'promiscuous-lemma', now()
    FROM aliases
    WHERE deployment_id = :deployment_id AND normalized_lemma = :lemma
    ON CONFLICT (deployment_id, normalized_lemma)
    DO UPDATE SET
        distinct_entity_count = EXCLUDED.distinct_entity_count,
        is_downweighted = EXCLUDED.is_downweighted,
        reason = EXCLUDED.reason,
        evaluated_at = EXCLUDED.evaluated_at
    """
)

_UPDATE_ENTITY_EMBEDDING = text(
    """
    UPDATE entities SET
      embedding = CAST(:embedding AS vector),
      embedding_model = :embedding_model,
      embedding_input_policy_version = :input_policy,
      embedding_text_hash = :text_hash,
      updated_at = now()
    WHERE deployment_id = :deployment_id AND entity_id = :entity_id
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
        :decision_id, :deployment_id, :mention_id, :entity_id, :method,
        :confidence, :is_new_entity, :features, :resolver_version
    )
    """
).bindparams(bindparam("features", type_=JSON))

_SEED_RESOLVER_VERSION = text(
    """
    INSERT INTO resolver_versions (
        deployment_id, resolver_version, tier_config, thresholds_by_type
    ) VALUES (
        :deployment_id, :resolver_version, :tier_config, :thresholds_by_type
    )
    ON CONFLICT (deployment_id, resolver_version) DO NOTHING
    """
).bindparams(
    bindparam("tier_config", type_=JSON), bindparam("thresholds_by_type", type_=JSON)
)

_SELECT_RESOLVER_VERSION = text(
    """
    SELECT tier_config, thresholds_by_type FROM resolver_versions
    WHERE deployment_id = :deployment_id
      AND resolver_version = :resolver_version
    """
)

_BUMP_MENTION_COUNT = text(
    """
    UPDATE entities SET mention_count = mention_count + 1, updated_at = now()
    WHERE deployment_id = :deployment_id AND entity_id = :entity_id
    """
)
