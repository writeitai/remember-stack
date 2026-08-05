"""The zero-LLM query engine (retrieval §2-§3): resolve, lookup, search, hydrate.

The one correctness rule is D48: projections (P1 Lance) may NOMINATE
candidates, but every returned record has passed by-ID hydration against the
live Postgres spine — a superseded fact can never be served as current, and
nominations hydration rejects are counted in `dropped_by_hydration` so ranked
results are honest about their denominator. No primitive calls an LLM; reads
never trigger anything.
"""

import base64
import binascii
from collections import Counter
from collections.abc import Iterator
from collections.abc import Sequence
from datetime import datetime
from datetime import UTC
from itertools import batched
import math
from typing import cast
from typing import Final
from typing import Literal
from typing import TYPE_CHECKING
import unicodedata
from uuid import UUID

from sqlalchemy import text
from sqlalchemy import TextClause
from sqlalchemy.engine import Engine
from sqlalchemy.engine import RowMapping

from rememberstack.core.embedding_input_policy import EMBEDDING_INPUT_POLICY_VERSION
from rememberstack.core.ranking import DEFAULT_RRF_K
from rememberstack.core.ranking import reciprocal_rank_fusion
from rememberstack.core.ranking import rerank_by_signal
from rememberstack.core.ranking import rerank_by_weighted_signals
from rememberstack.model import AggregateBucket
from rememberstack.model import AggregateReport
from rememberstack.model import ChangeRecord
from rememberstack.model import ChunkEvidenceResult
from rememberstack.model import CoMember
from rememberstack.model import Contradiction
from rememberstack.model import EmbeddingRequest
from rememberstack.model import EntityCandidate
from rememberstack.model import Envelope
from rememberstack.model import EvidenceResult
from rememberstack.model import EvidenceTotal
from rememberstack.model import FactEvidence
from rememberstack.model import FactResult
from rememberstack.model import FactSupport
from rememberstack.model import Freshness
from rememberstack.model import Grain
from rememberstack.model import GraphEdge
from rememberstack.model import GraphNode
from rememberstack.model import GraphPath
from rememberstack.model import Negative
from rememberstack.model import NegativeKind
from rememberstack.model import PageRef
from rememberstack.model import RankedItem
from rememberstack.model import ScanRow
from rememberstack.model import SourceRecord
from rememberstack.model import TranscriptEntry
from rememberstack.model import Truncation
from rememberstack.model import Validity
from rememberstack.ports.model_provider import ModelProviderPort
from rememberstack.ports.p1_index import ClaimVectorLookupPort
from rememberstack.ports.p1_index import P1Nomination
from rememberstack.ports.p1_index import P1SearchPort
from rememberstack.spine.entity_registry import normalized_lemma

if TYPE_CHECKING:
    from rememberstack.surfaces.graph_queries import GraphQueries

DEFAULT_DELTA_LIMIT = 500
"""How many change-feed rows one `delta` page returns before truncating —
a starting point to measure, not a committed constant (retrieval §13)."""

DEFAULT_TRANSCRIPT_LIMIT = 40
"""How many decision-history rows one `transcript` returns before truncating
— recent-first. A starting point to measure, not a committed constant: long
entity resolution logs (hundreds of mention rows) must not blow a reader
context, and S18 forbids silent caps, so the envelope always signals when
this bound applies."""

DEFAULT_SCAN_BATCH = 1_000
"""How many rows the batch `scan` cursor fetches per round-trip."""

CONTRADICTION_COMEMBER_CAP = 25
"""How many co-members a contradiction block returns inline before it pages
(S23). Typical groups are 2–3 sides, so the cap is rarely reached — but when
it is, the block still carries group_id/returned/total/continuation, never a
one-sided answer. WP-5.6 measured this starting cap below its explicit 16 KiB
inline-envelope budget; that budget is an operating target, not a protocol
limit."""

RESOLVE_CONTEXT_LIMIT: Final = 8
"""Maximum focal entities in WP-5.6's bounded S51 context tie-break."""

INTERACTIVE_HYDRATION_BATCH_SIZE: Final = 256
"""Maximum ids in one WP-5.6-measured Postgres confirmation hop."""

BOUNDED_SEMANTIC_CANDIDATES: Final = 400
"""Maximum Postgres-filtered claim ids read from P1 for a semantic rerank.

This is candidate work, not the returned evidence budget: every new Batch B
recipe returns at most 50 evidence records. Four hundred matches the existing
interactive nomination ceiling and keeps hub-entity/time-window vector reads
bounded without ever nominating globally and filtering afterward.
"""

CURRENT_CONTEXT_EVIDENCE_BUDGET: Final = 60
"""Hard maximum evidence associations in one current-context envelope."""

MULTI_HOP_CONTEXT_EVIDENCE_BUDGET: Final = 60
"""Hard maximum associations and returned claim/chunk content records."""

MULTI_HOP_QUESTION_CONTEXT_K: Final = 50
"""The existing question-context recipe's per-grain result cap."""

MULTI_HOP_QUESTION_CONTEXT_CANDIDATE_K: Final = 200
"""The existing question-context recipe's per-channel nomination cap."""

QUESTION_CONTEXT_ENTITY_CAP: Final = 20
"""Maximum confirmed entity candidates in `question_context` v4."""

QUESTION_CONTEXT_FACT_CAP: Final = 30
"""Maximum current facts in the opt-in v4 fact channel."""

QUESTION_CONTEXT_EVIDENCE_PER_FACT: Final = 3
"""The v4 fact channel reuses `current_context`'s fixed default depth."""

_EVIDENCE_STANCES: Final = ("supports", "contradicts")
"""Stable two-stance order for selection and exact-total disclosure."""

_RERANK_SIGNALS = {"graph_distance": True, "evidence_count": False}
"""The inspectable rerank signals and whether each sorts ascending: nearer
the focal entity wins (ascending), more corroboration wins (descending)."""

_BOUNDED_AGGREGATE_FORMS = frozenset(
    {"group_by_predicate", "group_by_object", "delta_top_entities", "typed_absence"}
)
"""The aggregate forms that take a `limit` and so must disclose truncation.
`count` and `timeline` are naturally bounded (one row / one row per year)."""


def _encode_feed_cursor(*, at: datetime, item_id: UUID) -> str:
    """Pack a delta feed position into one opaque, resumable token."""
    raw = f"{at.isoformat()}|{item_id}".encode()
    return base64.urlsafe_b64encode(raw).decode()


def _decode_feed_cursor(token: str | None) -> tuple[datetime, UUID] | None:
    """Unpack a feed cursor into (at, id), or None when there is no cursor."""
    if token is None:
        return None
    try:
        raw = base64.urlsafe_b64decode(token.encode()).decode()
        at_text, id_text = raw.rsplit("|", 1)
        return (datetime.fromisoformat(at_text), UUID(id_text))
    except (ValueError, binascii.Error) as error:
        raise ValueError(f"invalid delta continuation: {token!r}") from error


class QueryEngine:
    """The typed read path over one deployment's spine and P1 indexes."""

    def __init__(
        self,
        *,
        engine: Engine,
        search_index: P1SearchPort,
        model_provider: ModelProviderPort,
        embedding_model: str,
        batch_engine: Engine | None = None,
    ) -> None:
        """Bind the engine to the spine, the P1 indexes, and the embedder.

        Embedding a query string is not an LLM call (retrieval §3): the
        provider's embed endpoint is the semantic channel's entry.

        `batch_engine` is the SEPARATE resource pool the batch surface uses
        (retrieval §9): `scan`'s streaming exports run against it so a large
        export can never starve the interactive connection pool. It defaults
        to the interactive engine — correct for a single-pool deployment —
        but a deployment that wants isolation passes a second engine bound
        to its own connection pool.
        """
        self._engine = engine
        self._search_index = search_index
        self._claim_vector_index = (
            search_index if isinstance(search_index, ClaimVectorLookupPort) else None
        )
        self._model_provider = model_provider
        self._embedding_model = embedding_model
        # Active D80 generation pointer for this query surface (library single-
        # tenant / deployment-scoped binding). Search and hydration prefer
        # rows under this pair so dual-generation cutover stays coherent.
        self._policy_generation = EMBEDDING_INPUT_POLICY_VERSION
        self._embedder_generation = embedding_model
        self._batch_engine = batch_engine or engine

    def resolve(
        self,
        *,
        deployment_id: UUID,
        name: str,
        entity_type: str | None = None,
        context_entity_ids: tuple[UUID, ...] = (),
    ) -> Envelope:
        """Resolve a name to ranked current entities (T0 in the skeleton).

        Nothing resolving is the `unknown_entity` negative (S39) — the agent
        widens resolution or searches; it never gets a silent guess (S51).
        Optional focal entities only reorder exact-name candidates by current
        relation adjacency; every candidate remains visible, so context can
        narrow ambiguity without becoming a silent identity verdict.
        """
        context_entity_ids = tuple(dict.fromkeys(context_entity_ids))
        if len(context_entity_ids) > RESOLVE_CONTEXT_LIMIT:
            raise ValueError(
                f"resolve context accepts at most {RESOLVE_CONTEXT_LIMIT} entities"
            )
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    _RESOLVE_T0,
                    {
                        "deployment_id": deployment_id,
                        "lemma": normalized_lemma(surface=name),
                        "entity_type": entity_type,
                    },
                )
                .mappings()
                .all()
            )
            candidate_ids = tuple(row["entity_id"] for row in rows)
            context_hits = (
                {
                    row["candidate_id"]: int(row["context_hits"])
                    for row in connection.execute(
                        _RESOLVE_CONTEXT_HITS,
                        {
                            "deployment_id": deployment_id,
                            "candidate_ids": list(candidate_ids),
                            "context_entity_ids": list(context_entity_ids),
                        },
                    ).mappings()
                }
                if candidate_ids and context_entity_ids
                else {}
            )
        candidates = tuple(
            EntityCandidate(
                entity_id=row["entity_id"],
                canonical_name=row["canonical_name"],
                type=row["type"],
                tier="T0",
                context_hits=context_hits.get(row["entity_id"], 0),
            )
            for row in sorted(
                rows,
                key=lambda row: (
                    -context_hits.get(row["entity_id"], 0),
                    str(row["canonical_name"]),
                    row["entity_id"].bytes,
                ),
            )
        )
        return Envelope(
            grain=Grain.FACT,
            entities=candidates,
            freshness=_freshness(),
            negative=None
            if candidates
            else Negative(
                kind=NegativeKind.UNKNOWN_ENTITY,
                explanation=f"nothing resolves for {name!r}",
                workaround="check spelling, try search over claims or chunks",
            ),
        )

    def documents_about(
        self, *, deployment_id: UUID, entity: str, k: int = 20
    ) -> Envelope:
        """List live ingested documents carrying a resolved mention of an entity."""
        _validate_batch_b_k(k=k)
        entity_id, resolution = self._resolve_recipe_entity(
            deployment_id=deployment_id, entity=entity, grain=Grain.EVIDENCE
        )
        if resolution is not None:
            return resolution
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    _DOCUMENTS_ABOUT,
                    {
                        "deployment_id": deployment_id,
                        "entity_id": entity_id,
                        "limit": k,
                    },
                )
                .mappings()
                .all()
            )
        total = int(rows[0]["total_count"]) if rows else 0
        sources = tuple(
            SourceRecord(
                doc_id=row["doc_id"],
                title=row["title"],
                source_kind=row["source_kind"],
                markdown_uri=row["markdown_uri"],
                mention_count=row["mention_count"],
                first_mentioned_at=row["first_mentioned_at"],
                last_mentioned_at=row["last_mentioned_at"],
            )
            for row in rows
        )
        return Envelope(
            grain=Grain.EVIDENCE,
            entities=(),
            sources=sources,
            freshness=_freshness(),
            truncation=_bounded_truncation(returned=len(sources), total=total, k=k),
            negative=None
            if sources
            else Negative(
                kind=NegativeKind.KNOWN_EMPTY,
                explanation=(
                    f"no ingested document has a resolved mention of {entity!r}"
                ),
                workaround="use text search to find unresolved textual mentions",
            ),
        )

    def claims_about(
        self, *, deployment_id: UUID, entity: str, query: str | None = None, k: int = 20
    ) -> Envelope:
        """Return current testimony from chunks mentioning one resolved entity."""
        _validate_batch_b_k(k=k)
        entity_id, resolution = self._resolve_recipe_entity(
            deployment_id=deployment_id, entity=entity, grain=Grain.EVIDENCE
        )
        if resolution is not None:
            return resolution
        candidate_limit = BOUNDED_SEMANTIC_CANDIDATES if query is not None else k
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    _CLAIMS_ABOUT_CANDIDATES,
                    {
                        "deployment_id": deployment_id,
                        "entity_id": entity_id,
                        "candidate_limit": candidate_limit,
                    },
                )
                .mappings()
                .all()
            )
        candidate_ids = tuple(row["claim_id"] for row in rows)
        total = int(rows[0]["total_count"]) if rows else 0
        ordered_ids, ranking = self._rank_bounded_claims(
            deployment_id=deployment_id, claim_ids=candidate_ids, query=query, k=k
        )
        evidence, dropped = self._confirm_claims(
            deployment_id=deployment_id, claim_ids=ordered_ids
        )
        confirmed = {record.claim_id for record in evidence}
        return Envelope(
            grain=Grain.EVIDENCE,
            evidence=evidence,
            ranking=tuple(item for item in ranking if item.item_id in confirmed),
            freshness=_freshness(),
            truncation=_bounded_truncation(returned=len(evidence), total=total, k=k),
            dropped_by_hydration=dropped,
            negative=None
            if evidence
            else Negative(
                kind=NegativeKind.KNOWN_EMPTY,
                explanation=f"no current source testimony mentions {entity!r}",
                workaround="broaden the query or inspect the mentioning documents",
            ),
        )

    def claims_as_of(
        self,
        *,
        deployment_id: UUID,
        from_: datetime,
        to: datetime,
        query: str | None = None,
        k: int = 20,
    ) -> Envelope:
        """Return historical testimony whose source-valid interval intersects a window."""
        _validate_batch_b_k(k=k)
        if to < from_:
            raise ValueError(
                "claims_as_of 'to' must be greater than or equal to 'from'"
            )
        candidate_limit = BOUNDED_SEMANTIC_CANDIDATES if query is not None else k
        with self._engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as connection:
            rows = (
                connection.execute(
                    _CLAIMS_AS_OF_CANDIDATES,
                    {
                        "deployment_id": deployment_id,
                        "from": from_,
                        "to": to,
                        "candidate_limit": candidate_limit,
                    },
                )
                .mappings()
                .all()
            )
            excluded_unstamped = int(
                connection.execute(
                    _UNSTAMPED_CLAIM_COUNT, {"deployment_id": deployment_id}
                ).scalar_one()
            )
        candidate_ids = tuple(row["claim_id"] for row in rows)
        total = int(rows[0]["total_count"]) if rows else 0
        ordered_ids, ranking = self._rank_bounded_claims(
            deployment_id=deployment_id, claim_ids=candidate_ids, query=query, k=k
        )
        evidence, dropped = self._confirm_claims(
            deployment_id=deployment_id, claim_ids=ordered_ids, current_only=False
        )
        confirmed = {record.claim_id for record in evidence}
        return Envelope(
            grain=Grain.EVIDENCE,
            evidence=evidence,
            ranking=tuple(item for item in ranking if item.item_id in confirmed),
            freshness=_freshness(),
            truncation=_bounded_truncation(returned=len(evidence), total=total, k=k),
            dropped_by_hydration=dropped,
            excluded_unstamped=excluded_unstamped,
            negative=None
            if evidence
            else Negative(
                kind=NegativeKind.KNOWN_EMPTY,
                explanation="no source-valid claim intersects the requested time window",
                workaround="widen the window or search unstamped testimony by text",
            ),
        )

    def chunk_neighbors(
        self, *, deployment_id: UUID, chunk_id: UUID, radius: int = 1
    ) -> Envelope:
        """Read a current source chunk and its section-order neighbors."""
        if not 1 <= radius <= 2:
            raise ValueError("chunk neighbor radius must be between 1 and 2")
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    _CHUNK_NEIGHBORS,
                    {
                        "deployment_id": deployment_id,
                        "chunk_id": chunk_id,
                        "radius": radius,
                    },
                )
                .mappings()
                .all()
            )
        candidate_ids = tuple(row["chunk_id"] for row in rows)
        if not candidate_ids:
            return Envelope(
                grain=Grain.EVIDENCE,
                freshness=_freshness(),
                negative=Negative(
                    kind=NegativeKind.UNKNOWN_ENTITY,
                    explanation=(
                        f"chunk_id {chunk_id} does not identify a current source chunk"
                    ),
                    workaround="search live source chunks again and use a returned chunk_id",
                ),
            )
        chunks, dropped = self._confirm_chunks(
            deployment_id=deployment_id, chunk_ids=candidate_ids
        )
        requested = radius * 2 + 1
        edge_truncated = len(candidate_ids) < requested
        return Envelope(
            grain=Grain.EVIDENCE,
            chunks=chunks,
            freshness=_freshness(),
            truncation=Truncation(
                truncated=edge_truncated,
                returned=len(chunks),
                estimated_total=requested,
                total_is_exact=True,
            ),
            dropped_by_hydration=dropped,
            negative=None
            if chunks
            else Negative(
                kind=NegativeKind.KNOWN_EMPTY,
                explanation="the neighboring source coordinates did not confirm",
                workaround="search live source chunks again",
            ),
        )

    def current_context(
        self,
        *,
        deployment_id: UUID,
        query: str,
        k: int = 15,
        evidence_per_fact: int = 3,
    ) -> Envelope:
        """Question-driven current facts with explicit two-stance evidence.

        P1 nominates relation and observation labels together. PostgreSQL then
        confirms both temporal clocks and hydrates only current testimony from
        live lineages (D48). Evidence is source-diverse within each stance and
        allocated rank-round-robin so the 60-record budget cannot leave a later
        returned fact unbacked.
        """
        _validate_current_context_bounds(k=k, evidence_per_fact=evidence_per_fact)
        as_of = datetime.now(tz=UTC)
        nominated = tuple(
            dict.fromkeys(
                UUID(item)
                for item in self._search_index.search_facts(
                    deployment_id=str(deployment_id),
                    vector=self._embed(query=query),
                    k=k + 1,
                    kind=None,
                )
            )
        )
        candidate_ids = nominated[:k]
        with self._engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as connection:
            fact_rows = (
                connection.execute(
                    _CONFIRM_CURRENT_FACTS,
                    {
                        "deployment_id": deployment_id,
                        "fact_ids": list(candidate_ids),
                        "as_of": as_of,
                    },
                )
                .mappings()
                .all()
                if candidate_ids
                else []
            )
            evidence_rows = (
                connection.execute(
                    _CURRENT_FACT_EVIDENCE,
                    {
                        "deployment_id": deployment_id,
                        "fact_ids": [row["fact_id"] for row in fact_rows],
                        "fact_kinds": [row["kind"] for row in fact_rows],
                        "per_stance_limit": evidence_per_fact,
                    },
                )
                .mappings()
                .all()
                if fact_rows
                else []
            )

        evidence_by_fact_stance: dict[tuple[UUID, str], list[RowMapping]] = {}
        totals: dict[tuple[UUID, str], int] = {}
        for row in evidence_rows:
            key = (row["fact_id"], str(row["stance"]))
            evidence_by_fact_stance.setdefault(key, []).append(row)
            totals[key] = int(row["evidence_total"])

        backed_rows = tuple(
            row
            for row in fact_rows
            if any(
                evidence_by_fact_stance.get((row["fact_id"], stance))
                for stance in _EVIDENCE_STANCES
            )
        )
        facts = self._enrich_current_context_facts(
            deployment_id=deployment_id, rows=backed_rows
        )
        selected = _select_fact_evidence(
            fact_ids=tuple(fact.fact_id for fact in facts),
            evidence_by_fact_stance=evidence_by_fact_stance,
            evidence_per_fact=evidence_per_fact,
            budget=CURRENT_CONTEXT_EVIDENCE_BUDGET,
        )
        returned_counts = Counter(
            (row["fact_id"], str(row["stance"])) for row in selected
        )
        associations = tuple(
            FactEvidence.model_validate(
                {
                    "fact_id": row["fact_id"],
                    "claim_id": row["claim_id"],
                    "stance": str(row["stance"]),
                }
            )
            for row in selected
        )
        evidence_by_id: dict[UUID, EvidenceResult] = {}
        for row in selected:
            claim_id = row["claim_id"]
            evidence_by_id.setdefault(
                claim_id,
                EvidenceResult.model_validate(
                    {
                        key: value
                        for key, value in dict(row).items()
                        if key
                        not in {
                            "fact_id",
                            "kind",
                            "stance",
                            "evidence_total",
                            "stance_rank",
                        }
                    }
                ),
            )
        exact_totals = tuple(
            EvidenceTotal(
                fact_id=fact.fact_id,
                stance=stance,
                returned=returned_counts[(fact.fact_id, stance)],
                total=totals.get((fact.fact_id, stance), 0),
            )
            for fact in facts
            for stance in _EVIDENCE_STANCES
        )
        dropped = len(candidate_ids) - len(facts)
        return Envelope(
            grain=Grain.FACT,
            facts=facts,
            evidence=tuple(evidence_by_id.values()),
            fact_evidence=associations,
            evidence_totals=exact_totals,
            freshness=_freshness(),
            truncation=Truncation(
                truncated=len(nominated) > k,
                returned=len(facts),
                estimated_total=len(nominated),
                total_is_exact=len(nominated) <= k,
            ),
            dropped_by_hydration=dropped,
            negative=None
            if facts
            else Negative(
                kind=NegativeKind.KNOWN_EMPTY,
                explanation=f"no current evidence-backed facts match {query!r}",
                workaround=(
                    "broaden the query or search claims and source passages for testimony"
                ),
            ),
        )

    def question_context(
        self,
        *,
        deployment_id: UUID,
        query: str,
        k: int = 50,
        candidate_k: int = 200,
        include_facts: bool = False,
        include_entities: bool = False,
    ) -> Envelope:
        """High-recall claim/chunk context with two opt-in v4 channels.

        The default remains the v3 hybrid claim/chunk answer. Facts reuse the
        existing `current_context` authority instead of duplicating its D48,
        both-stance, and 60-association rules. Entity resolution and semantic
        nominations are deduplicated, then confirmed together through
        `memory_v1.entities_current` before any candidate is returned.
        """
        _validate_question_context_bounds(k=k, candidate_k=candidate_k)
        base = self._question_context_retrieval(
            deployment_id=deployment_id, query=query, k=k, candidate_k=candidate_k
        )
        fact_context = (
            self.current_context(
                deployment_id=deployment_id,
                query=query,
                k=min(k, QUESTION_CONTEXT_FACT_CAP),
                evidence_per_fact=QUESTION_CONTEXT_EVIDENCE_PER_FACT,
            )
            if include_facts
            else None
        )
        entities: tuple[EntityCandidate, ...] = ()
        entity_drops = 0
        entity_truncated = False
        entity_estimated = 0
        if include_entities:
            (entities, entity_drops, entity_truncated, entity_estimated) = (
                self._question_context_entities(
                    deployment_id=deployment_id, query=query
                )
            )

        evidence_by_id = {record.claim_id: record for record in base.evidence}
        if fact_context is not None:
            for record in fact_context.evidence:
                evidence_by_id.setdefault(record.claim_id, record)
        evidence = tuple(evidence_by_id.values())
        facts = fact_context.facts if fact_context is not None else ()
        fact_evidence = fact_context.fact_evidence if fact_context is not None else ()
        evidence_totals = (
            fact_context.evidence_totals if fact_context is not None else ()
        )

        base_truncated = bool(base.truncation and base.truncation.truncated)
        fact_truncated = bool(
            fact_context
            and fact_context.truncation
            and fact_context.truncation.truncated
        )
        truncated = base_truncated or fact_truncated or entity_truncated
        returned = len(evidence) + len(base.chunks) + len(facts) + len(entities)
        estimated = returned
        exact = True
        if base.truncation is not None:
            estimated += max(
                0, base.truncation.estimated_total - base.truncation.returned
            )
            exact = exact and base.truncation.total_is_exact
        if fact_context is not None and fact_context.truncation is not None:
            estimated += max(
                0,
                fact_context.truncation.estimated_total
                - fact_context.truncation.returned,
            )
            exact = exact and fact_context.truncation.total_is_exact
        if include_entities:
            estimated += max(0, entity_estimated - len(entities))
            exact = exact and not entity_truncated

        has_payload = bool(evidence or base.chunks or facts or entities)
        return Envelope(
            grain=Grain.EVIDENCE,
            entities=entities,
            facts=facts,
            evidence=evidence,
            fact_evidence=fact_evidence,
            evidence_totals=evidence_totals,
            chunks=base.chunks,
            freshness=_freshness(),
            truncation=(
                Truncation(
                    truncated=True,
                    returned=returned,
                    estimated_total=max(estimated, returned + 1),
                    total_is_exact=exact,
                )
                if truncated
                else None
            ),
            dropped_by_hydration=(
                base.dropped_by_hydration
                + (fact_context.dropped_by_hydration if fact_context else 0)
                + entity_drops
            ),
            negative=(
                None
                if has_payload
                else Negative(
                    kind=NegativeKind.KNOWN_EMPTY,
                    explanation=f"no question context confirms for {query!r}",
                    workaround="broaden the query or inspect source artifacts",
                )
            ),
        )

    def multi_hop_context(
        self,
        *,
        deployment_id: UUID,
        graph_queries: "GraphQueries",
        query: str,
        entity_a: str,
        entity_b: str | None = None,
        k: int = 15,
        hops: int = 2,
        evidence_per_fact: int = 3,
    ) -> Envelope:
        """One-call graph connection context with quotable source evidence.

        Entity strings resolve through the uniform T0 recipe helper. P2 then
        supplies either a shortest path or a distance-ranked neighborhood,
        while one batched PostgreSQL statement re-confirms every nominated
        edge and hydrates both evidence stances. The ordinary question-context
        retrieval runs afterward and its claims/passages are unioned into the
        same flat evidence-grain envelope.
        """
        _validate_multi_hop_context_bounds(
            k=k, hops=hops, evidence_per_fact=evidence_per_fact
        )
        entity_a_id, resolution = self._resolve_recipe_entity(
            deployment_id=deployment_id, entity=entity_a, grain=Grain.EVIDENCE
        )
        if resolution is not None:
            return resolution
        assert entity_a_id is not None

        entity_b_id: UUID | None = None
        if entity_b is not None:
            entity_b_id, resolution = self._resolve_recipe_entity(
                deployment_id=deployment_id, entity=entity_b, grain=Grain.EVIDENCE
            )
            if resolution is not None:
                return resolution
            assert entity_b_id is not None

        graph = (
            graph_queries.path(
                from_entity_id=entity_a_id, to_entity_id=entity_b_id, max_hops=hops
            )
            if entity_b_id is not None
            else graph_queries.neighborhood(
                entity_id=entity_a_id, hops=hops, limit=k, include_paths=True
            )
        )
        candidate_edges_by_id: dict[UUID, GraphEdge] = {}
        for edge in graph.edges:
            candidate_edges_by_id.setdefault(edge.relation_id, edge)
            if len(candidate_edges_by_id) == k:
                break
        candidate_edge_ids = tuple(candidate_edges_by_id)

        evidence_rows: Sequence[RowMapping] = []
        if candidate_edge_ids:
            as_of = datetime.now(tz=UTC)
            with self._engine.connect().execution_options(
                isolation_level="REPEATABLE READ"
            ) as connection:
                # The invariant-compiled authority views are deliberately deep.
                # Preserve the written join order for this bounded hydration query
                # so PostgreSQL does not exhaust memory exploring equivalent plans.
                connection.exec_driver_sql("SET LOCAL join_collapse_limit = 1")
                connection.exec_driver_sql("SET LOCAL from_collapse_limit = 1")
                evidence_rows = (
                    connection.execute(
                        _MULTI_HOP_EDGE_EVIDENCE,
                        {
                            "deployment_id": deployment_id,
                            "relation_ids": list(candidate_edge_ids),
                            "as_of": as_of,
                            "per_stance_limit": evidence_per_fact,
                        },
                    )
                    .mappings()
                    .all()
                )

        confirmed_rows: dict[UUID, RowMapping] = {}
        evidence_by_fact_stance: dict[tuple[UUID, str], list[RowMapping]] = {}
        totals: dict[tuple[UUID, str], int] = {}
        for row in evidence_rows:
            relation_id = row["fact_id"]
            confirmed_rows.setdefault(relation_id, row)
            if row["claim_id"] is None:
                continue
            stance = str(row["stance"])
            key = (relation_id, stance)
            evidence_by_fact_stance.setdefault(key, []).append(row)
            totals[key] = int(row["evidence_total"])

        kept_edges_by_id: dict[UUID, GraphEdge] = {}
        confirmed_nodes: dict[UUID, tuple[str, str]] = {}
        for relation_id in candidate_edge_ids:
            row = confirmed_rows.get(relation_id)
            if row is None:
                continue
            withdrawn = bool(row["support_withdrawn"])
            has_current_support = bool(
                evidence_by_fact_stance.get((relation_id, "supports"))
            )
            if not has_current_support and not withdrawn:
                continue
            kept_edges_by_id[relation_id] = _graph_edge_from_confirmed_row(row=row)
            confirmed_nodes[row["subject_id"]] = (
                str(row["subject_name"]),
                str(row["subject_type"]),
            )
            confirmed_nodes[row["object_id"]] = (
                str(row["object_name"]),
                str(row["object_type"]),
            )

        retained_paths = _confirmed_graph_paths(
            paths=graph.paths, edges_by_id=kept_edges_by_id, nodes_by_id=confirmed_nodes
        )
        if entity_b_id is not None:
            retained_edge_ids = {
                edge.relation_id for path in retained_paths for edge in path.edges
            }
            kept_edges_by_id = {
                relation_id: edge
                for relation_id, edge in kept_edges_by_id.items()
                if relation_id in retained_edge_ids
            }
        retained_edges = tuple(kept_edges_by_id.values())
        retained_nodes = _confirmed_graph_nodes(
            graph_nodes=graph.nodes,
            paths=retained_paths,
            edges=retained_edges,
            nodes_by_id=confirmed_nodes,
        )

        selected = _select_fact_evidence(
            fact_ids=tuple(edge.relation_id for edge in retained_edges),
            evidence_by_fact_stance=evidence_by_fact_stance,
            evidence_per_fact=evidence_per_fact,
            budget=MULTI_HOP_CONTEXT_EVIDENCE_BUDGET,
        )
        returned_counts = Counter(
            (row["fact_id"], str(row["stance"])) for row in selected
        )
        associations = tuple(
            FactEvidence.model_validate(
                {
                    "fact_id": row["fact_id"],
                    "claim_id": row["claim_id"],
                    "stance": str(row["stance"]),
                }
            )
            for row in selected
        )
        edge_evidence_by_id: dict[UUID, EvidenceResult] = {}
        for row in selected:
            edge_evidence_by_id.setdefault(
                row["claim_id"], _evidence_result_from_fact_row(row=row)
            )
        exact_totals = tuple(
            EvidenceTotal(
                fact_id=edge.relation_id,
                stance=stance,
                returned=returned_counts[(edge.relation_id, stance)],
                total=totals.get((edge.relation_id, stance), 0),
            )
            for edge in retained_edges
            for stance in _EVIDENCE_STANCES
        )

        question_context = self._question_context_retrieval(
            deployment_id=deployment_id, query=query
        )
        evidence_by_id = dict(edge_evidence_by_id)
        for record in question_context.evidence:
            existing = evidence_by_id.get(record.claim_id)
            if existing is not None:
                if record.corroboration_count is not None:
                    evidence_by_id[record.claim_id] = existing.model_copy(
                        update={
                            "corroboration_count": record.corroboration_count,
                            "grouped_claim_ids": record.grouped_claim_ids,
                        }
                    )
                continue
            if len(evidence_by_id) == MULTI_HOP_CONTEXT_EVIDENCE_BUDGET:
                break
            evidence_by_id[record.claim_id] = record
        chunks_by_id: dict[UUID, ChunkEvidenceResult] = {}
        for record in question_context.chunks:
            if (
                len(evidence_by_id) + len(chunks_by_id)
                == MULTI_HOP_CONTEXT_EVIDENCE_BUDGET
            ):
                break
            chunks_by_id.setdefault(record.chunk_id, record)
        question_evidence_by_id = {
            record.claim_id: record for record in question_context.evidence
        }
        question_chunks_by_id = {
            record.chunk_id: record for record in question_context.chunks
        }
        uncapped_content_total = len(
            set(edge_evidence_by_id) | set(question_evidence_by_id)
        ) + len(question_chunks_by_id)
        returned_content_total = len(evidence_by_id) + len(chunks_by_id)
        content_elided = returned_content_total < uncapped_content_total

        graph_failed = graph.negative is not None
        confirmed_empty = entity_b_id is not None and not retained_paths
        neighborhood_empty = entity_b_id is None and not retained_edges
        path_exceeds_edge_cap = bool(
            entity_b_id is not None
            and graph.paths
            and not retained_paths
            and any(
                len({edge.relation_id for edge in path.edges}) > k
                for path in graph.paths
            )
        )
        negative = graph.negative
        if not graph_failed and path_exceeds_edge_cap:
            negative = Negative(
                kind=NegativeKind.BOUNDARY,
                explanation=(
                    f"a path within {hops} hop(s) exists, but its structural"
                    f" edges exceed the requested k={k} edge envelope cap"
                ),
                workaround="increase k to at least the path length",
            )
        elif not graph_failed and (confirmed_empty or neighborhood_empty):
            negative = Negative(
                kind=NegativeKind.KNOWN_EMPTY,
                explanation=(
                    f"no current evidence-backed path within {hops} hop(s) connects"
                    f" {entity_a!r} and {entity_b!r}"
                    if entity_b is not None
                    else (
                        f"no current evidence-backed edge within {hops} hop(s)"
                        f" surrounds {entity_a!r}"
                    )
                ),
                workaround="widen the hop bound or inspect source testimony directly",
            )

        graph_more = bool(graph.truncation and graph.truncation.truncated)
        edge_candidates_elided = len(candidate_edges_by_id) < len(
            {edge.relation_id for edge in graph.edges}
        )
        estimated_edges = len(candidate_edges_by_id)
        if graph_more or edge_candidates_elided:
            estimated_edges = max(estimated_edges + 1, k + 1)
        returned_records = len(retained_edges) + returned_content_total
        estimated_records = estimated_edges + uncapped_content_total
        return Envelope(
            grain=Grain.EVIDENCE,
            evidence=tuple(evidence_by_id.values()),
            fact_evidence=associations,
            evidence_totals=exact_totals,
            chunks=tuple(chunks_by_id.values()),
            nodes=retained_nodes,
            paths=retained_paths,
            edges=retained_edges,
            freshness=graph.freshness.model_copy(
                update={"pg_live_ts": datetime.now(tz=UTC)}
            ),
            truncation=Truncation(
                truncated=graph_more or edge_candidates_elided or content_elided,
                returned=returned_records,
                estimated_total=estimated_records,
                total_is_exact=not graph_more and not edge_candidates_elided,
            ),
            dropped_by_hydration=(
                len(candidate_edge_ids)
                - len(retained_edges)
                + graph.dropped_by_hydration
                + question_context.dropped_by_hydration
            ),
            negative=negative,
        )

    def lookup_relations(
        self,
        *,
        deployment_id: UUID,
        subject_entity_id: UUID | None = None,
        predicate: str | None = None,
        object_entity_id: UUID | None = None,
        valid_at: datetime | None = None,
    ) -> Envelope:
        """Relations matching the (s, p, o) pattern — fact grain (S1/S3/S9).

        Without `valid_at`, current means both clocks: still believed AND the
        valid-time window covers now. With `valid_at`, the window test moves
        to that instant (the S9-class as-of read; belief stays live — the
        believed_at axis arrives with its own parameter). The applied instant
        is echoed in the envelope. An existing entity with no matching facts
        is `known_empty` (S39).
        """
        as_of = valid_at or datetime.now(tz=UTC)
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    _LOOKUP_RELATIONS,
                    {
                        "deployment_id": deployment_id,
                        "subject_entity_id": subject_entity_id,
                        "predicate": predicate,
                        "object_entity_id": object_entity_id,
                        "as_of": as_of,
                    },
                )
                .mappings()
                .all()
            )
        facts = self._enrich_facts(
            deployment_id=deployment_id,
            facts=tuple(_fact_result(row=row, kind="relation") for row in rows),
            kind="relation",
        )
        return Envelope(
            grain=Grain.FACT,
            as_of_valid_at=valid_at,
            facts=facts,
            freshness=_freshness(),
            negative=None
            if facts
            else Negative(
                kind=NegativeKind.KNOWN_EMPTY,
                explanation="no live relations match the pattern",
                workaround=None,
            ),
        )

    def lookup_observations(
        self,
        *,
        deployment_id: UUID,
        entity_id: UUID,
        property_query: str | None = None,
        k: int = 10,
        valid_at: datetime | None = None,
    ) -> Envelope:
        """Observations on one entity — current, or as-of on the valid-time
        axis (S2/S9, D43): "headcount mid-2024" is the capped slice whose
        window covers that instant.

        With a property query, the facts channel NOMINATES by label similarity
        and the spine confirms live rows (D48); without one, the entity block
        is read directly.
        """
        dropped = 0
        as_of = valid_at or datetime.now(tz=UTC)
        if property_query is None:
            with self._engine.connect() as connection:
                rows = (
                    connection.execute(
                        _LOOKUP_OBSERVATIONS,
                        {
                            "deployment_id": deployment_id,
                            "entity_id": entity_id,
                            "as_of": as_of,
                        },
                    )
                    .mappings()
                    .all()
                )
        else:
            nominated = self._search_index.search_facts(
                deployment_id=str(deployment_id),
                vector=self._embed(query=property_query),
                k=k,
                kind="observation",
            )
            rows, dropped = self._confirm_observations(
                deployment_id=deployment_id,
                entity_id=entity_id,
                observation_ids=tuple(UUID(item) for item in nominated),
                as_of=as_of,
            )
        facts = self._enrich_facts(
            deployment_id=deployment_id,
            facts=tuple(_fact_result(row=row, kind="observation") for row in rows),
            kind="observation",
        )
        return Envelope(
            grain=Grain.FACT,
            as_of_valid_at=valid_at,
            facts=facts,
            freshness=_freshness(),
            dropped_by_hydration=dropped,
            negative=None
            if facts
            else Negative(
                kind=NegativeKind.KNOWN_EMPTY,
                explanation="no live observations match on this entity",
                workaround=None,
            ),
        )

    def search_claims(
        self,
        *,
        deployment_id: UUID,
        query: str,
        k: int = 10,
        channel: Literal["semantic", "bm25"] = "semantic",
    ) -> Envelope:
        """Claim search — EVIDENCE grain, never a current-fact answer.

        The claims channel nominates (current-testimony-only by default);
        hydration re-reads each claim from the spine and drops what no longer
        confirms, counting the drops (D48 nominate-then-drop honesty).
        """
        nominated = self._nominate_claim_ids(
            deployment_id=deployment_id, query=query, k=k, channel=channel
        )
        evidence, dropped = self._confirm_claims(
            deployment_id=deployment_id,
            claim_ids=tuple(UUID(item) for item in nominated),
        )
        return Envelope(
            grain=Grain.EVIDENCE,
            evidence=evidence,
            freshness=_freshness(),
            dropped_by_hydration=dropped,
            negative=None
            if evidence
            else Negative(
                kind=NegativeKind.KNOWN_EMPTY,
                explanation="no current-testimony claims match the query",
                workaround="broaden the query or inspect the source artifacts",
            ),
        )

    def nominate_claims(
        self,
        *,
        deployment_id: UUID,
        query: str,
        k: int = 10,
        channel: Literal["semantic", "bm25"] = "semantic",
    ) -> Envelope:
        """Rank claim IDs without returning unconfirmed claim content.

        This is the cheap, projection-only half of D48 for recipe composition:
        parallel channels can fuse their candidate orderings before one
        `hydrate_claims` confirmation. Candidate UUIDs and ranks are not facts.
        """
        nominated = self._nominate_claim_ids(
            deployment_id=deployment_id, query=query, k=k, channel=channel
        )
        return _nomination_envelope(
            ids=nominated, empty_explanation="no claims were nominated"
        )

    def search_chunks(
        self,
        *,
        deployment_id: UUID,
        query: str,
        k: int = 10,
        channel: Literal["semantic", "bm25"] = "semantic",
    ) -> Envelope:
        """Search live source chunks without pretending they are claims."""
        nominated = self._nominate_chunk_ids(
            deployment_id=deployment_id, query=query, k=k, channel=channel
        )
        chunks, dropped = self._confirm_chunks(
            deployment_id=deployment_id,
            chunk_ids=tuple(UUID(item) for item in nominated),
        )
        return Envelope(
            grain=Grain.EVIDENCE,
            chunks=chunks,
            freshness=_freshness(),
            dropped_by_hydration=dropped,
            negative=None
            if chunks
            else Negative(
                kind=NegativeKind.KNOWN_EMPTY,
                explanation="no live source chunks match the query",
                workaround="broaden the query or inspect the source artifacts",
            ),
        )

    def nominate_chunks(
        self,
        *,
        deployment_id: UUID,
        query: str,
        k: int = 10,
        channel: Literal["semantic", "bm25"] = "semantic",
    ) -> Envelope:
        """Rank source-chunk IDs without returning unconfirmed source text."""
        nominated = self._nominate_chunk_ids(
            deployment_id=deployment_id, query=query, k=k, channel=channel
        )
        return _nomination_envelope(
            ids=nominated, empty_explanation="no source chunks were nominated"
        )

    def _nominate_claim_ids(
        self,
        *,
        deployment_id: UUID,
        query: str,
        k: int,
        channel: Literal["semantic", "bm25"],
    ) -> tuple[str, ...]:
        """Run exactly one validated P1 claim-nomination channel."""
        _validate_nomination_request(k=k, channel=channel)
        if channel == "semantic":
            return self._search_index.search_claims(
                deployment_id=str(deployment_id),
                vector=self._embed(query=query),
                k=k,
                current_only=True,
            )
        return self._search_index.search_claims_lexical(
            deployment_id=str(deployment_id), query=query, k=k, current_only=True
        )

    def _nominate_chunk_ids(
        self,
        *,
        deployment_id: UUID,
        query: str,
        k: int,
        channel: Literal["semantic", "bm25"],
    ) -> tuple[str, ...]:
        """Run exactly one validated P1 source-chunk nomination channel."""
        _validate_nomination_request(k=k, channel=channel)
        if channel == "semantic":
            return self._search_index.search_chunks(
                deployment_id=str(deployment_id),
                vector=self._embed(query=query),
                k=k,
                policy_generation=self._policy_generation,
                embedder_generation=self._embedder_generation,
            )
        return self._search_index.search_chunks_lexical(
            deployment_id=str(deployment_id),
            query=query,
            k=k,
            policy_generation=self._policy_generation,
            embedder_generation=self._embedder_generation,
        )

    def hydrate_relation(self, *, deployment_id: UUID, relation_id: UUID) -> Envelope:
        """The S5 chain: relation → evidence claims → source documents.

        Composite grain: the fact, its supporting evidence-grain claims
        (verbatim spans and offsets against the representation they were cut
        from), and the ID-addressed document handles. Hydrate-by-ID is the
        AUDIT deepening hop: an invalidated relation is returned with its
        invalidation disclosed in `validity` (D48 re-reads and discloses —
        it does not refuse audit access); current-fact questions route
        through lookup, which filters both clocks.
        """
        with self._engine.connect() as connection:
            relation = (
                connection.execute(
                    _HYDRATE_RELATION,
                    {"deployment_id": deployment_id, "relation_id": relation_id},
                )
                .mappings()
                .one_or_none()
            )
            if relation is None:
                return Envelope(
                    grain=Grain.COMPOSITE,
                    freshness=_freshness(),
                    negative=Negative(
                        kind=NegativeKind.UNKNOWN_ENTITY,
                        explanation=f"relation {relation_id} does not exist",
                        workaround=None,
                    ),
                )
            claims = (
                connection.execute(
                    _HYDRATE_EVIDENCE_CLAIMS,
                    {"deployment_id": deployment_id, "relation_id": relation_id},
                )
                .mappings()
                .all()
            )
            sources = (
                connection.execute(
                    _HYDRATE_SOURCES,
                    {"deployment_id": deployment_id, "relation_id": relation_id},
                )
                .mappings()
                .all()
            )
        # the audit hop discloses the same S23 contradiction and D54 support
        # as a lookup — a contradicted relation is never hydrated one-sided
        facts = self._enrich_facts(
            deployment_id=deployment_id,
            facts=(_fact_result(row=relation, kind="relation"),),
            kind="relation",
        )
        return Envelope(
            grain=Grain.COMPOSITE,
            facts=facts,
            evidence=tuple(EvidenceResult.model_validate(dict(row)) for row in claims),
            sources=tuple(SourceRecord.model_validate(dict(row)) for row in sources),
            freshness=_freshness(),
        )

    def transcript(
        self,
        *,
        deployment_id: UUID,
        subject_kind: str,
        subject_id: UUID,
        limit: int = DEFAULT_TRANSCRIPT_LIMIT,
    ) -> Envelope:
        """The S8/S32/S35 audit query: any subject's decision history.

        "Why do we believe this?" as a first-class read, uniform across the
        four subjects a decision is about: a supersession-adjudicated
        `relation` or `observation`, a resolved/merged `entity` (its
        resolution decisions braided with its merges), or a compiled
        `k_page` (its compile provenance). Returned newest-last among the
        kept window; reads never trigger anything. The result is
        recent-first bounded by `limit` (default
        `DEFAULT_TRANSCRIPT_LIMIT`): when more history exists, the oldest
        rows drop and the envelope's truncation marker is set so callers
        see the cap rather than receiving everything (S18). An empty
        history is `known_empty`, not a guess; an unknown kind is a
        `boundary` naming the four that exist.
        """
        if limit < 1:
            raise ValueError("limit must be at least 1")
        statement = _TRANSCRIPT_BY_KIND.get(subject_kind)
        if statement is None:
            return Envelope(
                grain=Grain.COMPOSITE,
                freshness=_freshness(),
                negative=Negative(
                    kind=NegativeKind.BOUNDARY,
                    explanation=(f"no transcript for subject kind {subject_kind!r}"),
                    workaround="use one of: relation, observation, entity, k_page",
                ),
            )
        with self._engine.connect() as connection:
            rows = list(
                connection.execute(
                    statement,
                    {"deployment_id": deployment_id, "subject_id": subject_id},
                )
                .mappings()
                .all()
            )
        total = len(rows)
        truncated = total > limit
        # SQL orders oldest→newest (newest-last). Keep the most recent
        # `limit` rows so a long entity log still answers "what happened
        # lately" without flooding the reader context.
        kept = rows[-limit:] if truncated else rows
        return Envelope(
            grain=Grain.COMPOSITE,
            transcript=tuple(TranscriptEntry.model_validate(dict(row)) for row in kept),
            freshness=_freshness(),
            truncation=Truncation(
                truncated=truncated,
                returned=len(kept),
                estimated_total=total,
                total_is_exact=True,
            ),
            negative=None
            if kept
            else Negative(
                kind=NegativeKind.KNOWN_EMPTY,
                explanation=f"no decision history for this {subject_kind}",
                workaround=None,
            ),
        )

    def transcript_relation(
        self, *, deployment_id: UUID, relation_id: UUID
    ) -> Envelope:
        """A relation's decision history — the `transcript` primitive, relation
        arm (kept as the named surface the HTTP API and recipes bind to)."""
        return self.transcript(
            deployment_id=deployment_id, subject_kind="relation", subject_id=relation_id
        )

    def fuse(
        self,
        *,
        rankings: Sequence[Sequence[UUID]],
        k: int = DEFAULT_RRF_K,
        limit: int | None = None,
    ) -> Envelope:
        """RRF-merge parallel channel rankings into one order (D9/S46).

        An operator, not a spine read: the same reciprocal-rank fusion a
        recipe applies, exposed so an agent's ad-hoc channel set fuses
        identically. The grain is EVIDENCE — a fused order is over
        nominations still to be confirmed by id-hydration (D48), never
        current-fact truth on its own. Call `hydrate_claims` on the ranked
        ids when the caller needs claim text, not only scores.
        """
        fused = reciprocal_rank_fusion(rankings=rankings, k=k)
        if limit is not None:
            if limit < 1:
                raise ValueError("fuse limit must be at least 1")
            fused = fused[:limit]
        return Envelope(
            grain=Grain.EVIDENCE,
            ranking=fused,
            freshness=_freshness(),
            negative=None
            if fused
            else Negative(
                kind=NegativeKind.KNOWN_EMPTY,
                explanation="no channel supplied any candidate to fuse",
                workaround=None,
            ),
        )

    def hydrate_claims(
        self,
        *,
        deployment_id: UUID,
        claim_ids: Sequence[UUID],
        ranking: Sequence[RankedItem] = (),
        limit: int | None = None,
        group_exact_text: bool = False,
    ) -> Envelope:
        """Confirm claim ids into evidence rows, keeping any prior ranking.

        The D48 confirmation hop for an ordered claim-id list (typically the
        output of `fuse`/`rerank`): re-reads each claim from the spine and
        drops what no longer confirms. When a ranking is supplied, scores and
        order are preserved on the envelope for the confirmed ids so a fused
        result is usable without a second tool call. Hybrid recipes pass their
        final ``limit`` only here, after the complete fused candidate pool has
        been confirmed, so a rejected head candidate is deterministically
        replaced from the already-fetched tail. Claim hybrids additionally
        group exact normalized-text duplicates before that final cut.
        """
        if limit is not None and limit < 1:
            raise ValueError("hydrate_claims limit must be at least 1")
        ordered_ids = tuple(claim_ids)
        evidence, dropped = self._confirm_claims(
            deployment_id=deployment_id, claim_ids=ordered_ids
        )
        if group_exact_text:
            evidence = _group_claim_evidence(evidence=evidence)
        if limit is not None:
            evidence = evidence[:limit]
        returned = {record.claim_id for record in evidence}
        kept_ranking = tuple(item for item in ranking if item.item_id in returned)
        return Envelope(
            grain=Grain.EVIDENCE,
            evidence=evidence,
            ranking=kept_ranking,
            freshness=_freshness(),
            dropped_by_hydration=dropped,
            negative=None
            if evidence
            else Negative(
                kind=NegativeKind.KNOWN_EMPTY,
                explanation="no nominated claims confirmed at hydration",
                workaround="broaden the query or inspect the source artifacts",
            ),
        )

    def hydrate_chunks(
        self,
        *,
        deployment_id: UUID,
        chunk_ids: Sequence[UUID],
        ranking: Sequence[RankedItem] = (),
        limit: int | None = None,
    ) -> Envelope:
        """Confirm chunk ids into live source evidence, preserving scores.

        A hybrid supplies its final ``limit`` after the complete fused pool,
        allowing confirmed tail candidates to refill head candidates that no
        longer pass D48 without another projection read.
        """
        if limit is not None and limit < 1:
            raise ValueError("hydrate_chunks limit must be at least 1")
        ordered_ids = tuple(chunk_ids)
        chunks, dropped = self._confirm_chunks(
            deployment_id=deployment_id, chunk_ids=ordered_ids
        )
        if limit is not None:
            chunks = chunks[:limit]
        returned = {record.chunk_id for record in chunks}
        kept_ranking = tuple(item for item in ranking if item.item_id in returned)
        return Envelope(
            grain=Grain.EVIDENCE,
            chunks=chunks,
            ranking=kept_ranking,
            freshness=_freshness(),
            dropped_by_hydration=dropped,
            negative=None
            if chunks
            else Negative(
                kind=NegativeKind.KNOWN_EMPTY,
                explanation="no nominated chunks confirmed at hydration",
                workaround="broaden the query or inspect the source artifacts",
            ),
        )

    def combine_evidence(self, *, inputs: Sequence[Envelope]) -> Envelope:
        """Combine typed claims and chunks without cross-fusing their UUIDs."""
        for envelope in inputs:
            if envelope.grain is not Grain.EVIDENCE or any(
                (
                    envelope.parts,
                    envelope.entities,
                    envelope.facts,
                    envelope.sources,
                    envelope.transcript,
                    envelope.nodes,
                    envelope.paths,
                    envelope.edges,
                    envelope.changes,
                    envelope.aggregate is not None,
                    envelope.pages,
                )
            ):
                raise ValueError(
                    "combine_evidence accepts only evidence-grain claim/chunk envelopes"
                )
        truncations = tuple(
            envelope.truncation
            for envelope in inputs
            if envelope.truncation is not None
        )
        if len(truncations) > 1:
            raise ValueError(
                "combine_evidence cannot merge multiple continuation tokens"
            )
        evidence_by_id = {
            record.claim_id: record
            for envelope in inputs
            for record in envelope.evidence
        }
        chunks_by_id = {
            record.chunk_id: record for envelope in inputs for record in envelope.chunks
        }
        evidence = tuple(evidence_by_id.values())
        chunks = tuple(chunks_by_id.values())
        return Envelope(
            grain=Grain.EVIDENCE,
            evidence=evidence,
            chunks=chunks,
            freshness=_freshness(),
            truncation=truncations[0] if truncations else None,
            dropped_by_hydration=sum(
                envelope.dropped_by_hydration for envelope in inputs
            ),
            negative=None
            if evidence or chunks
            else Negative(
                kind=NegativeKind.KNOWN_EMPTY,
                explanation="no claim or source evidence confirmed",
                workaround="broaden the query or inspect the source artifacts",
            ),
        )

    def rerank(self, *, items: Sequence[RankedItem], signal: str) -> Envelope:
        """Reorder candidates by one inspectable signal (D9/S46/S48).

        `graph_distance` and `evidence_count` are the direct signals;
        `weighted_relevance` applies WP-5.6's measured normalized blend while
        preserving every contribution on the item. `cross_encoder` needs a
        configured reranker port and is off by default — asking for it, or
        for any unknown signal, is a typed `boundary`, never a silent
        identity sort.
        """
        if signal == "cross_encoder":
            return self._rerank_boundary(
                explanation=(
                    "cross-encoder reranking needs a configured reranker port"
                    " and is off by default"
                ),
                workaround=(
                    "use graph_distance, evidence_count, or weighted_relevance"
                ),
            )
        if signal == "weighted_relevance":
            ranked = rerank_by_weighted_signals(items=items)
            return Envelope(
                grain=Grain.EVIDENCE, ranking=ranked, freshness=_freshness()
            )
        ascending = _RERANK_SIGNALS.get(signal)
        if ascending is None:
            return self._rerank_boundary(
                explanation=f"no rerank signal {signal!r}",
                workaround=(
                    "use graph_distance, evidence_count, or weighted_relevance"
                ),
            )
        ranked = rerank_by_signal(items=items, signal=signal, ascending=ascending)
        return Envelope(grain=Grain.EVIDENCE, ranking=ranked, freshness=_freshness())

    def delta(
        self,
        *,
        deployment_id: UUID,
        since: datetime,
        kinds: tuple[str, ...] | None = None,
        limit: int = DEFAULT_DELTA_LIMIT,
        continuation: str | None = None,
    ) -> Envelope:
        """The change feed as a query: what changed since `since` (S13/S14/S30).

        Four timestamped change types across the evidence kinds and K pages:
        `new` (ingested after `since`), `invalidated` (retracted after it —
        source-removal retractions land here too, since they set
        `invalidated_at`), `capped` (a relation or observation whose validity
        window a supersede closed — dated by the adjudication), and
        `recompiled` (a K page rebuilt after it). `kinds` filters to a subset
        of {relation, observation, claim, page}.

        Ordered newest-first over the FULL `(at, id)` key and bounded: hitting
        `limit` sets a truncation marker carrying an opaque `continuation`.
        Paginating means passing that token back (keeping the same `since`) —
        it resumes strictly before the last row seen, so a page boundary that
        splits rows sharing one timestamp never drops the tied remainder.
        """
        if limit < 1:
            raise ValueError("limit must be at least 1")
        cursor = _decode_feed_cursor(continuation)
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    _DELTA_FEED,
                    {
                        "deployment_id": deployment_id,
                        "since": since,
                        "kinds": list(kinds) if kinds else None,
                        "cursor_at": cursor[0] if cursor else None,
                        "cursor_id": str(cursor[1]) if cursor else None,
                        "fetch": limit + 1,
                    },
                )
                .mappings()
                .all()
            )
        truncated = len(rows) > limit
        kept = rows[:limit]
        changes = tuple(
            ChangeRecord(
                kind=row["kind"],
                change=row["change"],
                id=row["id"],
                label=row["label"],
                at=row["at"],
            )
            for row in kept
        )
        next_cursor = (
            _encode_feed_cursor(at=kept[-1]["at"], item_id=kept[-1]["id"])
            if truncated and kept
            else None
        )
        return Envelope(
            grain=Grain.COMPOSITE,
            as_of_believed_at=since,
            changes=changes,
            freshness=_freshness(),
            truncation=Truncation(
                truncated=truncated,
                returned=len(changes),
                estimated_total=len(changes),
                total_is_exact=not truncated,
                continuation=next_cursor,
            ),
            negative=None
            if changes
            else Negative(
                kind=NegativeKind.KNOWN_EMPTY,
                explanation="nothing changed in the requested window",
                workaround=None,
            ),
        )

    def pages_about(
        self,
        *,
        deployment_id: UUID,
        entity_id: UUID | None = None,
        key_kind: str | None = None,
        key_value: str | None = None,
    ) -> Envelope:
        """Which K pages exist about a subject (S31/S45): the routing index,
        read backwards.

        The rule-key inverted index built to ROUTE writes doubles as the
        reader's discovery index — mechanically, no LLM. Pass an `entity_id`
        (shorthand for the `entity` key) or an explicit `key_kind`/`key_value`
        (`predicate`, `community`, `doc_source`). Each page reports its
        compile state and a `stale` flag — inputs changed but not yet
        recompiled — so discovery never presents an out-of-date page as
        fresh. COMPILED grain: these are pre-paid syntheses, not raw facts.
        """
        if entity_id is not None:
            key_kind, key_value = "entity", str(entity_id)
        if key_kind is None or key_value is None:
            raise ValueError("pages_about needs an entity_id or a key_kind+key_value")
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    _PAGES_ABOUT,
                    {
                        "deployment_id": deployment_id,
                        "key_kind": key_kind,
                        "key_value": key_value,
                    },
                )
                .mappings()
                .all()
            )
        pages = tuple(
            PageRef(
                artifact_id=row["artifact_id"],
                page_kind=row["page_kind"],
                git_path=row["git_path"],
                page_summary=row["page_summary"],
                last_compiled_at=row["last_compiled_at"],
                status=row["status"],
                stale=row["stale"],
                open_review_flags=row["open_review_flags"],
                redaction_required=row["redaction_required"],
            )
            for row in rows
        )
        return Envelope(
            grain=Grain.COMPILED,
            pages=pages,
            freshness=_freshness(),
            negative=None
            if pages
            else Negative(
                kind=NegativeKind.KNOWN_EMPTY,
                explanation=f"no K pages route on {key_kind}={key_value!r}",
                workaround="query the primitives directly; K synthesis is optional",
            ),
        )

    def aggregate(
        self,
        *,
        deployment_id: UUID,
        form: str,
        subject_entity_id: UUID | None = None,
        predicate: str | None = None,
        entity_type: str | None = None,
        since: datetime | None = None,
        limit: int = 50,
    ) -> Envelope:
        """An enumerated aggregate — never a general GROUP BY (retrieval §9).

        Each `form` is a bounded SQL shape with a predictable cost, because
        an unbounded ad-hoc aggregation over 10⁸ rows is a denial of service
        against the spine (the escape hatch is `scan`). The forms: `count`,
        `group_by_predicate`, `group_by_object`, `timeline` (an entity's
        facts by year), `delta_top_entities` (facts gained since T, bounded
        by the delta window — S30), and `typed_absence` (entities of a type
        with no relation of a predicate — S40, answerable because the
        ontology types entities). A `limit`-bounded form that hits its cap
        sets an explicit truncation marker — the bucket total is then a
        floor, never a silent "this is all there is". An unknown form is a
        typed `boundary`.
        """
        if limit < 1:
            raise ValueError("limit must be at least 1")
        builder = _AGGREGATE_FORMS.get(form)
        if builder is None:
            return Envelope(
                grain=Grain.FACT,
                freshness=_freshness(),
                negative=Negative(
                    kind=NegativeKind.BOUNDARY,
                    explanation=f"no enumerated aggregate {form!r}",
                    workaround=f"use one of: {', '.join(sorted(_AGGREGATE_FORMS))}",
                ),
            )
        statement, needs = builder
        parameters = {
            "deployment_id": deployment_id,
            "subject_entity_id": subject_entity_id,
            "predicate": predicate,
            "entity_type": entity_type,
            "since": since,
            "fetch": limit + 1,  # one extra row reveals a truncation honestly
        }
        for required, value in (
            ("subject_entity_id", subject_entity_id),
            ("predicate", predicate),
            ("entity_type", entity_type),
            ("since", since),
        ):
            if required in needs and value is None:
                raise ValueError(f"aggregate {form!r} requires {required}")
        with self._engine.connect() as connection:
            rows = connection.execute(statement, parameters).mappings().all()
        bounded = form in _BOUNDED_AGGREGATE_FORMS
        truncated = bounded and len(rows) > limit
        buckets = tuple(
            AggregateBucket(
                key=None if row["key"] is None else str(row["key"]),
                count=row["count"],
                entity_id=row.get("entity_id"),
            )
            for row in (rows[:limit] if bounded else rows)
        )
        total = sum(bucket.count for bucket in buckets)
        return Envelope(
            grain=Grain.FACT,
            as_of_believed_at=since,
            aggregate=AggregateReport(
                form=form,
                buckets=buckets,
                total=total,
                bounded_by="delta window" if form == "delta_top_entities" else None,
            ),
            freshness=_freshness(),
            truncation=Truncation(
                truncated=truncated,
                returned=len(buckets),
                estimated_total=len(buckets),
                total_is_exact=not truncated,
            )
            if bounded
            else None,
        )

    def scan(
        self, *, deployment_id: UUID, kind: str, batch_size: int = DEFAULT_SCAN_BATCH
    ) -> Iterator[ScanRow]:
        """The batch surface (S53): stream a filtered export, row by row.

        A generator over the SEPARATE batch pool (`batch_engine`), using a
        server-side cursor so a full export streams in bounded memory and
        never buffers 10⁸ rows or starves the interactive pool. Same
        zero-LLM read, same grain labels; no interactive-latency promise.
        `kind` selects the export: `relation`, `observation`, or `claim`. An
        unknown kind raises rather than streaming a silent empty export.
        """
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        statement = _SCAN_EXPORTS.get(kind)
        if statement is None:
            raise ValueError(
                f"no scan export {kind!r}; use relation, observation, or claim"
            )
        connection = self._batch_engine.connect().execution_options(stream_results=True)
        try:
            result = connection.execute(statement, {"deployment_id": deployment_id})
            for partition in result.mappings().partitions(batch_size):
                for row in partition:
                    yield ScanRow(
                        kind=kind, id=row["id"], label=row["label"], at=row["at"]
                    )
        finally:
            connection.close()

    def _question_context_retrieval(
        self,
        *,
        deployment_id: UUID,
        query: str,
        k: int = MULTI_HOP_QUESTION_CONTEXT_K,
        candidate_k: int = MULTI_HOP_QUESTION_CONTEXT_CANDIDATE_K,
    ) -> Envelope:
        """Run the stock question-context mechanics inside a compound op.

        This deliberately mirrors the registered recipe's two independent
        semantic/BM25 nominations, RRF, one confirmation per grain, and typed
        claim/chunk union. Keeping it inside ``multi_hop_context`` avoids the
        executor dataflow and ``combine_evidence`` shape conflict that makes a
        multi-step public Batch D chain invalid.
        """

        def hydrate_claim_context() -> Envelope:
            semantic = self.nominate_claims(
                deployment_id=deployment_id,
                query=query,
                k=candidate_k,
                channel="semantic",
            )
            lexical = self.nominate_claims(
                deployment_id=deployment_id, query=query, k=candidate_k, channel="bm25"
            )
            fused = self.fuse(
                rankings=(
                    tuple(item.item_id for item in semantic.ranking),
                    tuple(item.item_id for item in lexical.ranking),
                ),
                k=DEFAULT_RRF_K,
            )
            return self.hydrate_claims(
                deployment_id=deployment_id,
                claim_ids=tuple(item.item_id for item in fused.ranking),
                ranking=fused.ranking,
                limit=k,
                group_exact_text=True,
            )

        def hydrate_chunk_context() -> Envelope:
            semantic = self.nominate_chunks(
                deployment_id=deployment_id,
                query=query,
                k=candidate_k,
                channel="semantic",
            )
            lexical = self.nominate_chunks(
                deployment_id=deployment_id, query=query, k=candidate_k, channel="bm25"
            )
            fused = self.fuse(
                rankings=(
                    tuple(item.item_id for item in semantic.ranking),
                    tuple(item.item_id for item in lexical.ranking),
                ),
                k=DEFAULT_RRF_K,
            )
            return self.hydrate_chunks(
                deployment_id=deployment_id,
                chunk_ids=tuple(item.item_id for item in fused.ranking),
                ranking=fused.ranking,
                limit=k,
            )

        return self.combine_evidence(
            inputs=(hydrate_claim_context(), hydrate_chunk_context())
        )

    def _question_context_entities(
        self, *, deployment_id: UUID, query: str
    ) -> tuple[tuple[EntityCandidate, ...], int, bool, int]:
        """Resolution-first semantic entities, confirmed once in PostgreSQL."""
        resolved = self.resolve(deployment_id=deployment_id, name=query)
        search = getattr(self._search_index, "search_entities_scored", None)
        if not callable(search):
            raise RuntimeError(
                "include_entities needs the semantic entity nomination channel"
            )
        nominations = cast(
            "tuple[P1Nomination, ...]",
            search(
                deployment_id=str(deployment_id),
                vector=self._embed(query=query),
                k=QUESTION_CONTEXT_ENTITY_CAP + 1,
                entity_type=None,
            ),
        )
        ordered: list[tuple[UUID, str, int]] = [
            (candidate.entity_id, candidate.tier, candidate.context_hits)
            for candidate in resolved.entities
        ]
        malformed = 0
        for nomination in nominations:
            try:
                ordered.append((UUID(str(nomination.item_id)), "semantic", 0))
            except (AttributeError, ValueError):
                malformed += 1
        deduplicated: list[tuple[UUID, str, int]] = []
        seen: set[UUID] = set()
        for item in ordered:
            if item[0] in seen:
                continue
            seen.add(item[0])
            deduplicated.append(item)
        if not deduplicated:
            return (), malformed, False, 0
        with self._engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as connection:
            rows = (
                connection.execute(
                    _CONFIRM_CONTEXT_ENTITIES,
                    {
                        "deployment_id": deployment_id,
                        "entity_ids": [item[0] for item in deduplicated],
                    },
                )
                .mappings()
                .all()
            )
        confirmed = {row["entity_id"]: row for row in rows}
        confirmed_entities = tuple(
            EntityCandidate(
                entity_id=entity_id,
                canonical_name=str(confirmed[entity_id]["canonical_name"]),
                type=str(confirmed[entity_id]["entity_type"]),
                tier=tier,
                context_hits=context_hits,
            )
            for entity_id, tier, context_hits in deduplicated
            if entity_id in confirmed
        )
        truncated = len(confirmed_entities) > QUESTION_CONTEXT_ENTITY_CAP
        entities = confirmed_entities[:QUESTION_CONTEXT_ENTITY_CAP]
        dropped = malformed + len(deduplicated) - len(confirmed_entities)
        return entities, dropped, truncated, len(confirmed_entities)

    def _resolve_recipe_entity(
        self, *, deployment_id: UUID, entity: str, grain: Grain
    ) -> tuple[UUID | None, Envelope | None]:
        """Apply principle 9 to one string entity parameter.

        The T0 ladder may return no candidate, exactly one, or an ambiguity.
        Recipes never silently take the first ambiguity: candidates remain in
        ``entities[]`` and the negative names the boundary.
        """
        resolved = self.resolve(deployment_id=deployment_id, name=entity)
        if not resolved.entities:
            return None, Envelope(
                grain=grain, freshness=_freshness(), negative=resolved.negative
            )
        if len(resolved.entities) > 1:
            names = ", ".join(
                f"{candidate.canonical_name} ({candidate.entity_id})"
                for candidate in resolved.entities
            )
            return None, Envelope(
                grain=grain,
                entities=resolved.entities,
                freshness=_freshness(),
                negative=Negative(
                    kind=NegativeKind.BOUNDARY,
                    explanation=(
                        f"{entity!r} is ambiguous between these candidates: {names}"
                    ),
                    workaround="retry with an unambiguous alias or resolve an entity UUID first",
                ),
            )
        return next(iter(resolved.entities)).entity_id, None

    def _rank_bounded_claims(
        self,
        *,
        deployment_id: UUID,
        claim_ids: tuple[UUID, ...],
        query: str | None,
        k: int,
    ) -> tuple[tuple[UUID, ...], tuple[RankedItem, ...]]:
        """Optionally semantic-rank only a Postgres-bounded claim-id set."""
        if query is None:
            return claim_ids[:k], ()
        if self._claim_vector_index is None:
            raise RuntimeError(
                "bounded semantic claim reranking requires ClaimVectorLookupPort"
            )
        query_vector = self._embed(query=query)
        vectors = self._claim_vector_index.claim_vectors(
            deployment_id=str(deployment_id),
            claim_ids=tuple(str(claim_id) for claim_id in claim_ids),
        )
        scored = tuple(
            (
                claim_id,
                _cosine_similarity(
                    query_vector,
                    vectors.get(str(claim_id), tuple(0.0 for _ in query_vector)),
                ),
            )
            for claim_id in claim_ids
        )
        ordered = tuple(sorted(scored, key=lambda pair: (-pair[1], pair[0].bytes))[:k])
        return (
            tuple(claim_id for claim_id, _score in ordered),
            tuple(
                RankedItem(
                    item_id=claim_id,
                    score=score,
                    signals={"semantic_similarity": score},
                )
                for claim_id, score in ordered
            ),
        )

    def _rerank_boundary(self, *, explanation: str, workaround: str) -> Envelope:
        """A rerank request the engine cannot honor, as a typed boundary."""
        return Envelope(
            grain=Grain.EVIDENCE,
            freshness=_freshness(),
            negative=Negative(
                kind=NegativeKind.BOUNDARY,
                explanation=explanation,
                workaround=workaround,
            ),
        )

    def _enrich_facts(
        self, *, deployment_id: UUID, facts: tuple[FactResult, ...], kind: str
    ) -> tuple[FactResult, ...]:
        """Attach the S23 contradiction block and the D54 support marker.

        For every returned fact in a live contradiction group, the OTHER
        live sides come back inline (bounded by the cap, with
        group_id/returned/total/continuation) — one-sided is never a valid
        answer. A fact under an open `support_withdrawn` review flag is
        marked `withdrawn` (flagged, not vanished). Two bounded batch reads,
        never one-per-fact.
        """
        if not facts:
            return facts
        groups = [
            fact.contradiction_group
            for fact in facts
            if fact.contradiction_group is not None
        ]
        members_by_group: dict[UUID, list[dict[str, object]]] = {}
        withdrawn: set[UUID] = set()
        with self._engine.connect() as connection:
            if groups:
                for row in (
                    connection.execute(
                        _CONTRADICTION_MEMBERS[kind],
                        {"deployment_id": deployment_id, "groups": groups},
                    )
                    .mappings()
                    .all()
                ):
                    members_by_group.setdefault(row["contradiction_group"], []).append(
                        dict(row)
                    )
            withdrawn = {
                row["fact_id"]
                for row in connection.execute(
                    _OPEN_SUPPORT_FLAGS,
                    {
                        "deployment_id": deployment_id,
                        "fact_ids": [str(fact.fact_id) for fact in facts],
                    },
                )
                .mappings()
                .all()
            }
        return tuple(
            self._enrich_one(
                fact=fact, members_by_group=members_by_group, withdrawn=withdrawn
            )
            for fact in facts
        )

    def _enrich_current_context_facts(
        self, *, deployment_id: UUID, rows: tuple[RowMapping, ...]
    ) -> tuple[FactResult, ...]:
        """Build and enrich a mixed relation/observation fact nomination."""
        by_kind: dict[str, tuple[FactResult, ...]] = {
            kind: tuple(
                _fact_result(row=row, kind=kind) for row in rows if row["kind"] == kind
            )
            for kind in ("relation", "observation")
        }
        enriched = {
            fact.fact_id: fact
            for kind, facts in by_kind.items()
            for fact in self._enrich_facts(
                deployment_id=deployment_id, facts=facts, kind=kind
            )
        }
        return tuple(enriched[row["fact_id"]] for row in rows)

    def _enrich_one(
        self,
        *,
        fact: FactResult,
        members_by_group: dict[UUID, list[dict[str, object]]],
        withdrawn: set[UUID],
    ) -> FactResult:
        """One fact, with its contradiction block and support marker resolved."""
        update: dict[str, object] = {}
        if fact.fact_id in withdrawn:
            update["support"] = FactSupport.WITHDRAWN
        if fact.contradiction_group is not None:
            others = [
                member
                for member in members_by_group.get(fact.contradiction_group, [])
                if member["fact_id"] != fact.fact_id
            ]
            returned = others[:CONTRADICTION_COMEMBER_CAP]
            update["contradiction"] = Contradiction(
                group_id=fact.contradiction_group,
                co_members=tuple(_co_member(member) for member in returned),
                returned=len(returned),
                total=len(others),
                continuation=(
                    str(returned[-1]["fact_id"])
                    if len(returned) < len(others)
                    else None
                ),
            )
        return fact.model_copy(update=update) if update else fact

    def _confirm_claims(
        self,
        *,
        deployment_id: UUID,
        claim_ids: tuple[UUID, ...],
        current_only: bool = True,
        include_deleted: bool = False,
    ) -> tuple[tuple[EvidenceResult, ...], int]:
        """The D48 confirmation hop for claim nominations, order-preserving."""
        if not claim_ids:
            return (), 0
        rows: list[RowMapping] = []
        # Multiple chunks are one answer, so they must observe one database
        # snapshot rather than mixing currency states across round trips.
        with self._engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as connection:
            for batch in batched(claim_ids, INTERACTIVE_HYDRATION_BATCH_SIZE):
                rows.extend(
                    connection.execute(
                        _CONFIRM_CLAIMS,
                        {
                            "deployment_id": deployment_id,
                            "claim_ids": list(batch),
                            "current_only": current_only,
                            "include_deleted": include_deleted,
                        },
                    )
                    .mappings()
                    .all()
                )
        confirmed = {row["claim_id"]: row for row in rows}
        results = tuple(
            EvidenceResult.model_validate(dict(confirmed[claim_id]))
            for claim_id in claim_ids
            if claim_id in confirmed
        )
        return results, len(claim_ids) - len(results)

    def _confirm_chunks(
        self, *, deployment_id: UUID, chunk_ids: tuple[UUID, ...]
    ) -> tuple[tuple[ChunkEvidenceResult, ...], int]:
        """D48-confirm source coordinates, then hydrate their P1 bodies."""
        if not chunk_ids:
            return (), 0
        rows: list[RowMapping] = []
        with self._engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as connection:
            for batch in batched(chunk_ids, INTERACTIVE_HYDRATION_BATCH_SIZE):
                rows.extend(
                    connection.execute(
                        _CONFIRM_CHUNKS,
                        {"deployment_id": deployment_id, "chunk_ids": list(batch)},
                    )
                    .mappings()
                    .all()
                )
        confirmed = {row["chunk_id"]: row for row in rows}
        texts = self._search_index.chunk_texts(
            deployment_id=str(deployment_id),
            chunk_ids=tuple(str(item) for item in confirmed),
            policy_generation=self._policy_generation,
            embedder_generation=self._embedder_generation,
        )
        results: list[ChunkEvidenceResult] = []
        for chunk_id in chunk_ids:
            row = confirmed.get(chunk_id)
            projected = texts.get(str(chunk_id))
            if row is None or projected is None:
                continue
            # D80: P1 text is body-only when policy_generation is present.
            # Legacy rows may store prefix + "\n\n" + body in P1 text; strip
            # the stored prefix when hydrating so agents never see mangled body.
            location_header = row.get("location_header") or row.get("context_prefix")
            policy_generation = row.get("policy_generation") or row.get(
                "embedding_input_policy_version"
            )
            chunk_text = projected.indexed_text
            if not policy_generation and location_header:
                chunk_text = _strip_legacy_prefix(
                    indexed_text=chunk_text, location_header=str(location_header)
                )
            if projected.section_role != row["section_role"]:
                continue
            results.append(
                ChunkEvidenceResult(
                    chunk_id=chunk_id,
                    doc_id=row["doc_id"],
                    version_id=row["version_id"],
                    representation_id=row["representation_id"],
                    chunk_text=chunk_text,
                    context_prefix=location_header,
                    char_start=row["char_start"],
                    char_end=row["char_end"],
                    section_role=row["section_role"],
                    document_title=row["document_title"],
                    source_kind=row["source_kind"],
                    source_modified_at=row["source_modified_at"],
                    published_at=row["published_at"],
                )
            )
        return tuple(results), len(chunk_ids) - len(results)

    def _confirm_observations(
        self,
        *,
        deployment_id: UUID,
        entity_id: UUID,
        observation_ids: tuple[UUID, ...],
        as_of: datetime,
    ) -> tuple[tuple[dict[str, object], ...], int]:
        """The D48 confirmation hop for observation nominations."""
        if not observation_ids:
            return (), 0
        rows: list[RowMapping] = []
        with self._engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as connection:
            for batch in batched(observation_ids, INTERACTIVE_HYDRATION_BATCH_SIZE):
                rows.extend(
                    connection.execute(
                        _CONFIRM_OBSERVATIONS,
                        {
                            "deployment_id": deployment_id,
                            "entity_id": entity_id,
                            "observation_ids": list(batch),
                            "as_of": as_of,
                        },
                    )
                    .mappings()
                    .all()
                )
        confirmed = {row["fact_id"]: dict(row) for row in rows}
        results = tuple(
            confirmed[observation_id]
            for observation_id in observation_ids
            if observation_id in confirmed
        )
        return results, len(observation_ids) - len(results)

    def _embed(self, *, query: str) -> tuple[float, ...]:
        """One query-string embedding through the configured port (D63)."""
        response = self._model_provider.embed(
            request=EmbeddingRequest(model=self._embedding_model, texts=(query,))
        )
        return response.vectors[0]


def _strip_legacy_prefix(*, indexed_text: str, location_header: str) -> str:
    """Remove a legacy prefix embedded in P1 text when policy stamps are absent.

    Pre-D80 rows stored ``prefix + "\\n\\n" + body`` in the Lance text column.
    D80 stores body-only; this branch keeps evidence hydration correct for
    unrebuilt legacy rows without inventing new body bytes.
    """
    if not location_header:
        return indexed_text
    for separator in ("\n\n", "\n"):
        marker = f"{location_header}{separator}"
        if indexed_text.startswith(marker):
            return indexed_text[len(marker) :]
    return indexed_text


def _validate_nomination_request(*, k: int, channel: str) -> None:
    """Reject unbounded or misspelled projection-search requests."""
    if not 1 <= k <= 400:
        raise ValueError("nomination k must be between 1 and 400")
    if channel not in {"semantic", "bm25"}:
        raise ValueError(
            f"unknown retrieval channel {channel!r}; use 'semantic' or 'bm25'"
        )


def _validate_batch_b_k(*, k: int) -> None:
    """Enforce the four new recipes' shared public result bound."""
    if not 1 <= k <= 50:
        raise ValueError("recipe k must be between 1 and 50")


def _validate_current_context_bounds(*, k: int, evidence_per_fact: int) -> None:
    """Enforce Batch C's public fact and per-stance evidence bounds."""
    if not 1 <= k <= 30:
        raise ValueError("current_context k must be between 1 and 30")
    if not 1 <= evidence_per_fact <= 5:
        raise ValueError("evidence_per_fact must be between 1 and 5")


def _validate_question_context_bounds(*, k: int, candidate_k: int) -> None:
    """Enforce the retained question-context v4 public bounds."""
    if not 1 <= k <= 100:
        raise ValueError("question_context k must be between 1 and 100")
    if not 1 <= candidate_k <= 400:
        raise ValueError("question_context candidate_k must be between 1 and 400")


def _validate_multi_hop_context_bounds(
    *, k: int, hops: int, evidence_per_fact: int
) -> None:
    """Enforce Batch D's public edge, hop, and per-stance evidence bounds."""
    if not 1 <= k <= 30:
        raise ValueError("multi_hop_context k must be between 1 and 30")
    if not 1 <= hops <= 2:
        raise ValueError("multi_hop_context hops must be between 1 and 2")
    if not 1 <= evidence_per_fact <= 5:
        raise ValueError("evidence_per_fact must be between 1 and 5")


def _select_fact_evidence(
    *,
    fact_ids: Sequence[UUID],
    evidence_by_fact_stance: dict[tuple[UUID, str], list[RowMapping]],
    evidence_per_fact: int,
    budget: int,
) -> tuple[RowMapping, ...]:
    """Allocate two-stance evidence fairly within the hard envelope budget.

    Each rank round visits every returned fact and both stances before taking
    another claim from any fact. Because k is at most 30 and the budget is 60,
    the first round always gives every backed fact at least one association;
    when both stances exist it also exposes both before adding depth.
    """
    selected: list[RowMapping] = []
    for rank in range(evidence_per_fact):
        for fact_id in fact_ids:
            for stance in _EVIDENCE_STANCES:
                candidates = evidence_by_fact_stance.get((fact_id, stance), [])
                if rank < len(candidates):
                    selected.append(candidates[rank])
                    if len(selected) == budget:
                        return tuple(selected)
    return tuple(selected)


def _evidence_result_from_fact_row(*, row: RowMapping) -> EvidenceResult:
    """Project one joined fact-evidence row to the public claim contract."""
    excluded = {
        "fact_id",
        "kind",
        "nomination_rank",
        "stance",
        "evidence_total",
        "stance_rank",
        "support_withdrawn",
        "subject_id",
        "object_id",
        "predicate",
        "fact",
        "evidence_count",
        "valid_from",
        "valid_until",
        "ingested_at",
        "invalidated_at",
        "subject_name",
        "subject_type",
        "object_name",
        "object_type",
    }
    return EvidenceResult.model_validate(
        {key: value for key, value in dict(row).items() if key not in excluded}
    )


def _graph_edge_from_confirmed_row(*, row: RowMapping) -> GraphEdge:
    """Build one edge from live PostgreSQL state, never projection text."""
    return GraphEdge(
        relation_id=row["fact_id"],
        subject_id=row["subject_id"],
        object_id=row["object_id"],
        predicate=row["predicate"],
        fact=row["fact"],
        evidence_count=row["evidence_count"],
        valid_from=row["valid_from"],
        valid_until=row["valid_until"],
        ingested_at=row["ingested_at"],
        invalidated_at=row["invalidated_at"],
        support=(
            FactSupport.WITHDRAWN if row["support_withdrawn"] else FactSupport.CURRENT
        ),
    )


def _confirmed_graph_paths(
    *,
    paths: Sequence[GraphPath],
    edges_by_id: dict[UUID, GraphEdge],
    nodes_by_id: dict[UUID, tuple[str, str]],
) -> tuple[GraphPath, ...]:
    """D48-confirm paths as units and replace projection labels from PG."""
    confirmed: list[GraphPath] = []
    for path in paths:
        if not all(edge.relation_id in edges_by_id for edge in path.edges):
            continue
        nodes = tuple(
            GraphNode(
                entity_id=node.entity_id,
                name=nodes_by_id.get(node.entity_id, (node.name, node.type))[0],
                type=nodes_by_id.get(node.entity_id, (node.name, node.type))[1],
                hops=node.hops,
            )
            for node in path.nodes
        )
        confirmed.append(
            path.model_copy(
                update={
                    "nodes": nodes,
                    "edges": tuple(
                        edges_by_id[edge.relation_id] for edge in path.edges
                    ),
                }
            )
        )
    return tuple(confirmed)


def _confirmed_graph_nodes(
    *,
    graph_nodes: Sequence[GraphNode],
    paths: Sequence[GraphPath],
    edges: Sequence[GraphEdge],
    nodes_by_id: dict[UUID, tuple[str, str]],
) -> tuple[GraphNode, ...]:
    """Keep only nodes connected by returned edges, in graph-rank order."""
    connected_ids = {
        entity_id for edge in edges for entity_id in (edge.subject_id, edge.object_id)
    }
    ordered = tuple(graph_nodes) + tuple(node for path in paths for node in path.nodes)
    returned: dict[UUID, GraphNode] = {}
    for node in ordered:
        if node.entity_id not in connected_ids or node.entity_id in returned:
            continue
        name, entity_type = nodes_by_id.get(node.entity_id, (node.name, node.type))
        returned[node.entity_id] = node.model_copy(
            update={"name": name, "type": entity_type}
        )
    return tuple(returned.values())


def _bounded_truncation(*, returned: int, total: int, k: int) -> Truncation:
    """Disclose an exact list total and whether its public k cap elided rows."""
    return Truncation(
        truncated=total > k,
        returned=returned,
        estimated_total=total,
        total_is_exact=True,
    )


def _normalize_hybrid_text(*, value: str) -> str:
    """Batch E's recipe-versioned exact-text grouping normalizer.

    The transformation order is binding: NFKC, casefold, whitespace-run
    collapse, then removal of leading and trailing Unicode punctuation. It
    deliberately performs no stemming, lemmatization, or semantic matching.
    """
    normalized = unicodedata.normalize("NFKC", value).casefold()
    collapsed = " ".join(normalized.split())
    start = 0
    end = len(collapsed)
    while start < end and unicodedata.category(collapsed[start]).startswith("P"):
        start += 1
    while end > start and unicodedata.category(collapsed[end - 1]).startswith("P"):
        end -= 1
    return collapsed[start:end]


def _group_claim_evidence(
    *, evidence: Sequence[EvidenceResult]
) -> tuple[EvidenceResult, ...]:
    """Group confirmed claims in incoming rank order by normalized text."""
    grouped: dict[str, list[EvidenceResult]] = {}
    for record in evidence:
        grouped.setdefault(_normalize_hybrid_text(value=record.claim_text), []).append(
            record
        )
    return tuple(
        members[0].model_copy(
            update={
                "corroboration_count": len({member.doc_id for member in members}),
                "grouped_claim_ids": tuple(member.claim_id for member in members),
            }
        )
        for members in grouped.values()
    )


def _cosine_similarity(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    """Cosine similarity for one bounded query/candidate vector pair."""
    if len(a) != len(b):
        raise ValueError("claim vector dimension differs from the query embedding")
    a_norm = math.sqrt(sum(value * value for value in a))
    b_norm = math.sqrt(sum(value * value for value in b))
    if a_norm == 0 or b_norm == 0:
        return 0.0
    return sum(left * right for left, right in zip(a, b, strict=True)) / (
        a_norm * b_norm
    )


def _nomination_envelope(*, ids: Sequence[str], empty_explanation: str) -> Envelope:
    """Represent ordered projection IDs without claiming they are hydrated."""
    ranking = tuple(
        RankedItem(
            item_id=UUID(item),
            score=1.0 / position,
            signals={"source_rank": float(position)},
        )
        for position, item in enumerate(ids, start=1)
    )
    return Envelope(
        grain=Grain.EVIDENCE,
        ranking=ranking,
        freshness=_freshness(),
        negative=None
        if ranking
        else Negative(
            kind=NegativeKind.KNOWN_EMPTY,
            explanation=empty_explanation,
            workaround="broaden the query or inspect the source artifacts",
        ),
    )


def _freshness() -> Freshness:
    """The skeleton's freshness stamps: PG is live; P1 is written inline.

    The `believed_at` horizons are null (unbounded): Postgres holds full
    belief history, and under D69 the hot P2 view keeps every relation whose
    endpoints stay emitted. A channel that grows a real finite horizon fills
    these in, and `believed_at_boundary` turns a query before it into a typed
    boundary.
    """
    return Freshness(pg_live_ts=datetime.now(tz=UTC))


def believed_at_boundary(
    *, believed_at: datetime | None, horizon: datetime | None
) -> Negative | None:
    """A typed boundary when a `believed_at` query predates a channel horizon.

    Belief history is not infinite on every channel: if a channel reports a
    finite `believed_at` horizon and the caller asks for an instant before
    it, that is a stated capability limit (retrieval §3) — a `boundary` that
    names the fallback, never a silently truncated answer. Null horizon
    (unbounded) never triggers it.
    """
    if believed_at is None or horizon is None or believed_at >= horizon:
        return None
    return Negative(
        kind=NegativeKind.BOUNDARY,
        explanation=(
            f"believed_at {believed_at.isoformat()} is before this channel's"
            f" retention horizon {horizon.isoformat()}"
        ),
        workaround="query a later instant, or read Postgres belief history",
    )


def _fact_result(*, row, kind: str) -> FactResult:  # noqa: ANN001
    """Build one fact-grain record from a hydrated spine row."""
    mapping = dict(row)
    return FactResult(
        fact_id=row["fact_id"],
        kind=kind,
        label=row["label"],
        evidence_count=row["evidence_count"],
        contradiction_group=mapping.get("contradiction_group"),
        validity=Validity(
            valid_from=row["valid_from"],
            valid_until=row["valid_until"],
            ingested_at=row["ingested_at"],
            invalidated_at=row["invalidated_at"],
        ),
    )


def _co_member(row: dict[str, object]) -> CoMember:
    """Build one contradiction co-member record from a live spine row."""
    return CoMember(
        fact_id=row["fact_id"],  # type: ignore[arg-type]
        label=row["label"],  # type: ignore[arg-type]
        evidence_count=row["evidence_count"],  # type: ignore[arg-type]
        validity=Validity(
            valid_from=row["valid_from"],  # type: ignore[arg-type]
            valid_until=row["valid_until"],  # type: ignore[arg-type]
            ingested_at=row["ingested_at"],  # type: ignore[arg-type]
            invalidated_at=row["invalidated_at"],  # type: ignore[arg-type]
        ),
    )


_DOCUMENTS_ABOUT = text(
    """
    WITH mentioned AS (
        SELECT d.doc_id, d.title, d.source_kind, r.markdown_uri,
               count(DISTINCT m.mention_id) AS mention_count,
               min(m.created_at) AS first_mentioned_at,
               max(m.created_at) AS last_mentioned_at
        FROM resolution_decisions rd
        JOIN mentions m
          ON m.deployment_id = rd.deployment_id
         AND m.mention_id = rd.mention_id
        JOIN documents d
          ON d.deployment_id = m.deployment_id
         AND d.doc_id = m.doc_id
        LEFT JOIN document_versions v
          ON v.deployment_id = d.deployment_id
         AND v.version_id = d.current_version_id
        LEFT JOIN document_representations r
          ON r.deployment_id = v.deployment_id
         AND r.representation_id = v.current_representation_id
        WHERE rd.deployment_id = :deployment_id
          AND rd.entity_id = :entity_id
          AND rd.superseded_by IS NULL
          AND d.deleted_at IS NULL
        GROUP BY d.doc_id, d.title, d.source_kind, r.markdown_uri
    )
    SELECT mentioned.*, count(*) OVER () AS total_count
    FROM mentioned
    ORDER BY mention_count DESC, last_mentioned_at DESC, doc_id
    LIMIT :limit
    """
)

_CLAIMS_ABOUT_CANDIDATES = text(
    """
    WITH matched AS (
        SELECT c.claim_id, max(c.asserted_at) AS asserted_at,
               max(c.ingested_at) AS ingested_at
        FROM resolution_decisions rd
        JOIN mentions m
          ON m.deployment_id = rd.deployment_id
         AND m.mention_id = rd.mention_id
        JOIN chunk_claims cc
          ON cc.deployment_id = m.deployment_id
         AND cc.chunk_id = m.chunk_id
        JOIN claims c
          ON c.deployment_id = cc.deployment_id
         AND c.claim_id = cc.claim_id
        LEFT JOIN documents d
          ON d.deployment_id = c.deployment_id
         AND d.doc_id = c.doc_id
        WHERE rd.deployment_id = :deployment_id
          AND rd.entity_id = :entity_id
          AND rd.superseded_by IS NULL
          AND c.is_current_testimony
          AND (d.doc_id IS NULL OR d.deleted_at IS NULL)
        GROUP BY c.claim_id
    )
    SELECT claim_id, count(*) OVER () AS total_count
    FROM matched
    ORDER BY asserted_at DESC NULLS LAST, ingested_at DESC, claim_id
    LIMIT :candidate_limit
    """
)

_CLAIMS_AS_OF_CANDIDATES = text(
    """
    SELECT c.claim_id, count(*) OVER () AS total_count
    FROM claims c
    LEFT JOIN documents d
      ON d.deployment_id = c.deployment_id
     AND d.doc_id = c.doc_id
    WHERE c.deployment_id = :deployment_id
      AND c.claim_valid_precision <> 'unknown'
      AND c.claim_valid_from <= :to
      AND (c.claim_valid_until IS NULL OR c.claim_valid_until >= :from)
      AND (d.doc_id IS NULL OR d.deleted_at IS NULL)
    ORDER BY c.claim_valid_from DESC, c.claim_id
    LIMIT :candidate_limit
    """
)

_UNSTAMPED_CLAIM_COUNT = text(
    """
    SELECT count(*)
    FROM claims
    WHERE deployment_id = :deployment_id
      AND claim_valid_precision = 'unknown'
    """
)

_CHUNK_NEIGHBORS = text(
    """
    WITH focal AS (
        SELECT ch.doc_id, ch.version_id, ch.representation_id, ch.ordinal
        FROM chunks ch
        JOIN documents d
          ON d.deployment_id = ch.deployment_id AND d.doc_id = ch.doc_id
        JOIN document_versions v
          ON v.deployment_id = ch.deployment_id AND v.version_id = ch.version_id
        JOIN document_representations r
          ON r.deployment_id = ch.deployment_id
         AND r.representation_id = ch.representation_id
        WHERE ch.deployment_id = :deployment_id
          AND ch.chunk_id = :chunk_id
          AND d.deleted_at IS NULL
          AND d.current_version_id = ch.version_id
          AND v.current_representation_id = ch.representation_id
          AND v.status = 'ready'
          AND v.deleted_at IS NULL
          AND r.status = 'ready'
    )
    SELECT ch.chunk_id, ch.ordinal
    FROM focal
    JOIN chunks ch
      ON ch.deployment_id = :deployment_id
     AND ch.doc_id = focal.doc_id
     AND ch.version_id = focal.version_id
     AND ch.representation_id = focal.representation_id
     AND ch.ordinal BETWEEN focal.ordinal - :radius AND focal.ordinal + :radius
    ORDER BY ch.ordinal, ch.chunk_id
    """
)

_RESOLVE_T0 = text(
    """
    WITH RECURSIVE matched AS (
        SELECT entities.entity_id, entities.canonical_name, entities.type,
               entities.status, entities.merged_into
        FROM aliases
        JOIN entities ON entities.deployment_id = aliases.deployment_id
                     AND entities.entity_id = aliases.entity_id
        WHERE aliases.deployment_id = :deployment_id
          AND aliases.normalized_lemma = :lemma
        UNION
        -- follow merge redirects to the survivor (S60: resolve returns
        -- CURRENT identities; the redirect chain is walked, never dead-ended)
        SELECT survivor.entity_id, survivor.canonical_name, survivor.type,
               survivor.status, survivor.merged_into
        FROM matched
        JOIN entities survivor ON survivor.deployment_id = :deployment_id
                              AND survivor.entity_id = matched.merged_into
        WHERE matched.status = 'merged'
    )
    SELECT DISTINCT entity_id, canonical_name, type
    FROM matched
    WHERE status = 'active'
      AND (CAST(:entity_type AS text) IS NULL OR type = :entity_type)
    """
)

_CONFIRM_CONTEXT_ENTITIES = text(
    """
    SELECT entity_id, canonical_name, entity_type
    FROM memory_v1.entities_current
    WHERE deployment_id = :deployment_id
      AND entity_id = ANY(:entity_ids)
    """
)

_RESOLVE_CONTEXT_HITS = text(
    """
    SELECT candidate_id, count(DISTINCT context_entity_id) AS context_hits
    FROM (
        SELECT subject_entity_id AS candidate_id,
               object_entity_id AS context_entity_id
        FROM relations
        WHERE deployment_id = :deployment_id
          AND subject_entity_id = ANY(:candidate_ids)
          AND object_entity_id = ANY(:context_entity_ids)
          AND invalidated_at IS NULL
          AND (valid_from IS NULL OR valid_from <= now())
          AND (valid_until IS NULL OR valid_until > now())
        UNION ALL
        SELECT object_entity_id AS candidate_id,
               subject_entity_id AS context_entity_id
        FROM relations
        WHERE deployment_id = :deployment_id
          AND object_entity_id = ANY(:candidate_ids)
          AND subject_entity_id = ANY(:context_entity_ids)
          AND invalidated_at IS NULL
          AND (valid_from IS NULL OR valid_from <= now())
          AND (valid_until IS NULL OR valid_until > now())
    ) adjacent
    GROUP BY candidate_id
    """
)

_LOOKUP_RELATIONS = text(
    """
    SELECT relation_id AS fact_id,
           coalesce(fact_label, predicate) AS label,
           evidence_count, valid_from, valid_until, ingested_at, invalidated_at,
           contradiction_group
    FROM relations
    WHERE deployment_id = :deployment_id
      AND invalidated_at IS NULL
      AND (valid_from IS NULL OR valid_from <= :as_of)
      AND (valid_until IS NULL OR valid_until > :as_of)
      AND (CAST(:subject_entity_id AS uuid) IS NULL
           OR subject_entity_id = :subject_entity_id)
      AND (CAST(:predicate AS text) IS NULL OR predicate = :predicate)
      AND (CAST(:object_entity_id AS uuid) IS NULL
           OR object_entity_id = :object_entity_id)
    ORDER BY evidence_count DESC, ingested_at
    """
)

_LOOKUP_OBSERVATIONS = text(
    """
    SELECT observation_id AS fact_id, statement AS label,
           evidence_count, valid_from, valid_until, ingested_at, invalidated_at,
           contradiction_group
    FROM observations
    WHERE deployment_id = :deployment_id
      AND subject_entity_id = :entity_id
      AND invalidated_at IS NULL
      AND (valid_from IS NULL OR valid_from <= :as_of)
      AND (valid_until IS NULL OR valid_until > :as_of)
    ORDER BY evidence_count DESC, ingested_at
    """
)

_CONFIRM_CURRENT_FACTS = text(
    """
    WITH requested AS (
        SELECT fact_id, nomination_rank
        FROM unnest(CAST(:fact_ids AS uuid[])) WITH ORDINALITY
             AS nominated(fact_id, nomination_rank)
    ), confirmed AS (
        SELECT requested.nomination_rank, 'relation'::text AS kind,
               r.relation_id AS fact_id,
               coalesce(r.fact_label, r.predicate) AS label,
               r.evidence_count, r.valid_from, r.valid_until,
               r.ingested_at, r.invalidated_at, r.contradiction_group
        FROM requested
        JOIN relations r
          ON r.deployment_id = :deployment_id
         AND r.relation_id = requested.fact_id
        WHERE r.invalidated_at IS NULL
          AND (r.valid_from IS NULL OR r.valid_from <= :as_of)
          AND (r.valid_until IS NULL OR r.valid_until > :as_of)
        UNION ALL
        SELECT requested.nomination_rank, 'observation'::text AS kind,
               o.observation_id AS fact_id, o.statement AS label,
               o.evidence_count, o.valid_from, o.valid_until,
               o.ingested_at, o.invalidated_at, o.contradiction_group
        FROM requested
        JOIN observations o
          ON o.deployment_id = :deployment_id
         AND o.observation_id = requested.fact_id
        WHERE o.invalidated_at IS NULL
          AND (o.valid_from IS NULL OR o.valid_from <= :as_of)
          AND (o.valid_until IS NULL OR o.valid_until > :as_of)
    )
    SELECT *
    FROM confirmed
    ORDER BY nomination_rank, kind, fact_id
    """
)

_CURRENT_FACT_EVIDENCE = text(
    """
    WITH requested AS (
        SELECT fact_id, kind, nomination_rank
        FROM unnest(
            CAST(:fact_ids AS uuid[]), CAST(:fact_kinds AS text[])
        ) WITH ORDINALITY AS confirmed(fact_id, kind, nomination_rank)
    ), links AS (
        SELECT requested.fact_id, requested.kind, requested.nomination_rank,
               e.claim_id, e.doc_id, e.stance::text AS stance
        FROM requested
        JOIN memory_v1.fact_claim_evidence_live e
          ON e.deployment_id = :deployment_id
         AND e.fact_kind = requested.kind
         AND e.fact_id = requested.fact_id
    ), totals AS (
        SELECT requested.fact_id, requested.kind, lineage.stance,
               count(*)::bigint AS evidence_total
        FROM requested
        JOIN memory_v1.evidence_lineage lineage
          ON lineage.deployment_id = :deployment_id
         AND lineage.fact_kind = requested.kind
         AND lineage.fact_id = requested.fact_id
        GROUP BY requested.fact_id, requested.kind, lineage.stance
    ), eligible AS (
        SELECT links.fact_id, links.kind, links.nomination_rank, links.stance,
               c.claim_id, c.doc_id, c.chunk_id, c.claim_text, c.source_span,
               c.char_start, c.char_end, c.is_attributed,
               true AS is_current_testimony, c.asserted_at, c.claim_valid_from,
               c.claim_valid_until, c.claim_valid_precision::text,
               c.claim_valid_kind::text, d.title AS document_title,
               d.source_kind, c.ingested_at AS evidence_ingested_at,
               totals.evidence_total,
               row_number() OVER (
                   PARTITION BY links.fact_id, links.stance, links.doc_id
                   ORDER BY c.asserted_at DESC NULLS LAST,
                            c.ingested_at DESC, c.claim_id
               ) AS lineage_claim_rank
        FROM links
        JOIN memory_v1.claims_live c
          ON c.deployment_id = :deployment_id
         AND c.claim_id = links.claim_id
         AND c.doc_id = links.doc_id
        JOIN memory_v1.documents_live d
          ON d.deployment_id = c.deployment_id
         AND d.doc_id = c.doc_id
        JOIN totals
          ON totals.fact_id = links.fact_id
         AND totals.kind = links.kind
         AND totals.stance = links.stance
    ), diverse AS (
        SELECT eligible.*,
               row_number() OVER (
                   PARTITION BY fact_id, stance
                   ORDER BY lineage_claim_rank,
                            asserted_at DESC NULLS LAST,
                            evidence_ingested_at DESC, doc_id, claim_id
               ) AS stance_rank
        FROM eligible
        WHERE lineage_claim_rank = 1
    )
    SELECT fact_id, kind, stance, evidence_total, stance_rank,
           claim_id, doc_id, chunk_id, claim_text, source_span,
           char_start, char_end, is_attributed, is_current_testimony,
           asserted_at, claim_valid_from, claim_valid_until,
           claim_valid_precision, claim_valid_kind, document_title, source_kind
    FROM diverse
    WHERE stance_rank <= :per_stance_limit
    ORDER BY nomination_rank,
             CASE stance WHEN 'supports' THEN 0 ELSE 1 END,
             stance_rank, claim_id
    """
)

_MULTI_HOP_EDGE_EVIDENCE = text(
    """
    WITH requested AS (
        SELECT relation_id, graph_rank
        FROM unnest(CAST(:relation_ids AS uuid[])) WITH ORDINALITY
             AS nominated(relation_id, graph_rank)
    ), confirmed AS MATERIALIZED (
        SELECT requested.graph_rank, r.relation_id AS fact_id,
               r.subject_entity_id AS subject_id,
               r.object_entity_id AS object_id, r.predicate,
               r.fact_label AS fact,
               r.evidence_count_current AS evidence_count,
               r.valid_from, r.valid_until, r.ingested_at, r.invalidated_at,
               subject.canonical_name AS subject_name,
               subject.type::text AS subject_type,
               object.canonical_name AS object_name,
               object.type::text AS object_type,
               EXISTS (
                   SELECT 1
                   FROM review_queue q
                   WHERE q.deployment_id = :deployment_id
                     AND q.item_kind = 'support_withdrawn'
                     AND q.status IN ('pending', 'deferred')
                     AND (q.candidate ->> 'fact_id') = r.relation_id::text
               ) AS support_withdrawn
        FROM requested
        JOIN memory_v1.graph_edges_visible_history r
          ON r.deployment_id = :deployment_id
         AND r.relation_id = requested.relation_id
        -- The graph view already proved both endpoints current. These base
        -- joins hydrate names and types only; repeating entities_current here
        -- expands its full authorization plan twice without changing
        -- membership.
        JOIN entities subject
          ON subject.deployment_id = r.deployment_id
         AND subject.entity_id = r.subject_entity_id
        JOIN entities object
          ON object.deployment_id = r.deployment_id
         AND object.entity_id = r.object_entity_id
        WHERE r.invalidated_at IS NULL
          AND (r.valid_from IS NULL OR r.valid_from <= :as_of)
          AND (r.valid_until IS NULL OR r.valid_until > :as_of)
    ), links AS (
        SELECT confirmed.fact_id, confirmed.graph_rank,
               e.claim_id, e.doc_id, e.stance::text AS stance
        FROM confirmed
        JOIN memory_v1.fact_claim_evidence_live e
          ON e.deployment_id = :deployment_id
         AND e.fact_kind = 'relation'
         AND e.fact_id = confirmed.fact_id
    ), totals AS (
        SELECT confirmed.fact_id, lineage.stance,
               count(*)::bigint AS evidence_total
        FROM confirmed
        JOIN memory_v1.evidence_lineage lineage
          ON lineage.deployment_id = :deployment_id
         AND lineage.fact_kind = 'relation'
         AND lineage.fact_id = confirmed.fact_id
        GROUP BY confirmed.fact_id, lineage.stance
    ), eligible AS (
        SELECT links.fact_id, links.graph_rank, links.stance,
               c.claim_id, c.doc_id, c.chunk_id, c.claim_text, c.source_span,
               c.char_start, c.char_end, c.is_attributed,
               true AS is_current_testimony, c.asserted_at, c.claim_valid_from,
               c.claim_valid_until, c.claim_valid_precision::text,
               c.claim_valid_kind::text, d.title AS document_title,
               d.source_kind, c.ingested_at AS evidence_ingested_at,
               totals.evidence_total,
               row_number() OVER (
                   PARTITION BY links.fact_id, links.stance, links.doc_id
                   ORDER BY c.asserted_at DESC NULLS LAST,
                            c.ingested_at DESC, c.claim_id
               ) AS lineage_claim_rank
        FROM links
        JOIN memory_v1.claims_live c
          ON c.deployment_id = :deployment_id
         AND c.claim_id = links.claim_id
         AND c.doc_id = links.doc_id
        JOIN memory_v1.documents_live d
          ON d.deployment_id = c.deployment_id
         AND d.doc_id = c.doc_id
        JOIN totals
          ON totals.fact_id = links.fact_id
         AND totals.stance = links.stance
    ), diverse AS (
        SELECT eligible.*,
               row_number() OVER (
                   PARTITION BY fact_id, stance
                   ORDER BY lineage_claim_rank,
                            asserted_at DESC NULLS LAST,
                            evidence_ingested_at DESC, doc_id, claim_id
               ) AS stance_rank
        FROM eligible
        WHERE lineage_claim_rank = 1
    ), limited AS (
        SELECT *
        FROM diverse
        WHERE stance_rank <= :per_stance_limit
    )
    SELECT confirmed.graph_rank AS nomination_rank, confirmed.fact_id,
           confirmed.subject_id, confirmed.object_id, confirmed.predicate,
           confirmed.fact, confirmed.evidence_count, confirmed.valid_from,
           confirmed.valid_until, confirmed.ingested_at, confirmed.invalidated_at,
           confirmed.subject_name, confirmed.subject_type,
           confirmed.object_name, confirmed.object_type,
           confirmed.support_withdrawn,
           limited.stance, limited.evidence_total, limited.stance_rank,
           limited.claim_id, limited.doc_id, limited.chunk_id,
           limited.claim_text, limited.source_span, limited.char_start,
           limited.char_end, limited.is_attributed,
           limited.is_current_testimony, limited.asserted_at,
           limited.claim_valid_from, limited.claim_valid_until,
           limited.claim_valid_precision, limited.claim_valid_kind,
           limited.document_title, limited.source_kind
    FROM confirmed
    LEFT JOIN limited ON limited.fact_id = confirmed.fact_id
    ORDER BY confirmed.graph_rank,
             CASE limited.stance WHEN 'supports' THEN 0
                                 WHEN 'contradicts' THEN 1 ELSE 2 END,
             limited.stance_rank NULLS LAST, limited.claim_id NULLS LAST
    """
)

_CONFIRM_OBSERVATIONS = text(
    """
    SELECT observation_id AS fact_id, statement AS label,
           evidence_count, valid_from, valid_until, ingested_at, invalidated_at,
           contradiction_group
    FROM observations
    WHERE deployment_id = :deployment_id
      AND subject_entity_id = :entity_id
      AND observation_id = ANY(:observation_ids)
      AND invalidated_at IS NULL
      AND (valid_from IS NULL OR valid_from <= :as_of)
      AND (valid_until IS NULL OR valid_until > :as_of)
    """
)

_CONFIRM_CLAIMS = text(
    """
    SELECT c.claim_id, c.doc_id, c.chunk_id, c.claim_text, c.source_span,
           c.char_start, c.char_end, c.is_attributed, c.is_current_testimony,
           c.asserted_at, c.claim_valid_from, c.claim_valid_until,
           c.claim_valid_precision::text, c.claim_valid_kind::text,
           d.title AS document_title, d.source_kind
    FROM claims c
    -- Imported/legacy claims may lack a document catalog row. Keep those
    -- evidence records, but fail closed when an existing lineage is tombstoned.
    LEFT JOIN documents d
      ON d.deployment_id = c.deployment_id AND d.doc_id = c.doc_id
    WHERE c.deployment_id = :deployment_id
      AND c.claim_id = ANY(:claim_ids)
      AND (NOT CAST(:current_only AS boolean) OR c.is_current_testimony)
      AND (
        CAST(:include_deleted AS boolean)
        OR d.doc_id IS NULL
        OR d.deleted_at IS NULL
      )
    """
)

_CONFIRM_CHUNKS = text(
    """
    SELECT ch.chunk_id, ch.doc_id, ch.version_id, ch.representation_id,
           ch.char_start, ch.char_end, ch.context_prefix, ch.location_header,
           ch.policy_generation, ch.embedding_input_policy_version,
           s.role::text AS section_role,
           d.title AS document_title, d.source_kind,
           v.source_modified_at, v.published_at
    FROM chunks ch
    JOIN documents d
      ON d.deployment_id = ch.deployment_id AND d.doc_id = ch.doc_id
    JOIN document_versions v
      ON v.deployment_id = ch.deployment_id AND v.version_id = ch.version_id
    JOIN document_representations r
      ON r.deployment_id = ch.deployment_id
     AND r.representation_id = ch.representation_id
    JOIN document_sections s
      ON s.deployment_id = ch.deployment_id AND s.section_id = ch.section_id
    WHERE ch.deployment_id = :deployment_id
      AND ch.chunk_id = ANY(:chunk_ids)
      AND d.deleted_at IS NULL
      AND d.current_version_id = ch.version_id
      AND v.current_representation_id = ch.representation_id
      AND v.status = 'ready'
      AND v.deleted_at IS NULL
      AND r.status = 'ready'
    """
)

_HYDRATE_RELATION = text(
    """
    SELECT relation_id AS fact_id,
           coalesce(fact_label, predicate) AS label,
           evidence_count, valid_from, valid_until, ingested_at, invalidated_at,
           contradiction_group
    FROM relations
    WHERE deployment_id = :deployment_id AND relation_id = :relation_id
    """
)

_HYDRATE_EVIDENCE_CLAIMS = text(
    """
    SELECT c.claim_id, c.doc_id, c.chunk_id, c.claim_text, c.source_span,
           c.char_start, c.char_end, c.is_attributed, c.is_current_testimony,
           c.asserted_at, c.claim_valid_from, c.claim_valid_until,
           c.claim_valid_precision::text, c.claim_valid_kind::text,
           d.title AS document_title, d.source_kind
    FROM relation_evidence e
    JOIN claims c ON c.deployment_id = e.deployment_id
                 AND c.claim_id = e.claim_id
                 AND c.doc_id = e.doc_id
    LEFT JOIN documents d
      ON d.deployment_id = c.deployment_id AND d.doc_id = c.doc_id
    WHERE e.deployment_id = :deployment_id
      AND e.relation_id = :relation_id
      AND e.stance = 'supports'
      AND c.is_current_testimony
      AND (d.doc_id IS NULL OR d.deleted_at IS NULL)
    ORDER BY c.ingested_at, c.claim_id
    """
)

_HYDRATE_SOURCES = text(
    """
    SELECT DISTINCT d.doc_id, d.title, d.source_kind, r.markdown_uri
    FROM relation_evidence e
    JOIN claims c ON c.deployment_id = e.deployment_id
                 AND c.claim_id = e.claim_id
                 AND c.doc_id = e.doc_id
    JOIN chunks ch ON ch.deployment_id = c.deployment_id
                  AND ch.chunk_id = c.chunk_id
    JOIN documents d ON d.deployment_id = e.deployment_id
                    AND d.doc_id = e.doc_id
    LEFT JOIN document_representations r
           ON r.deployment_id = ch.deployment_id
          AND r.representation_id = ch.representation_id
    WHERE e.deployment_id = :deployment_id
      AND e.relation_id = :relation_id
      AND e.stance = 'supports'
      AND c.is_current_testimony
      AND d.deleted_at IS NULL
    """
)

_RELATION_TRANSCRIPT = text(
    """
    -- related_id is always the OTHER relation in the pair, whichever side of
    -- the adjudication the subject sits on (never the subject itself)
    SELECT 'relation' AS subject_kind,
           outcome::text AS outcome, method::text AS method, confidence,
           CASE WHEN relation_id = :subject_id THEN related_relation_id
                ELSE relation_id END AS related_id,
           decided_by::text AS decided_by, decided_at, features
    FROM relation_adjudications
    WHERE deployment_id = :deployment_id
      AND (relation_id = :subject_id OR related_relation_id = :subject_id)
    ORDER BY decided_at, adjudication_id
    """
)

_OBSERVATION_TRANSCRIPT = text(
    """
    SELECT 'observation' AS subject_kind,
           outcome::text AS outcome, method::text AS method, confidence,
           CASE WHEN observation_id = :subject_id THEN related_observation_id
                ELSE observation_id END AS related_id,
           decided_by::text AS decided_by, decided_at, features
    FROM observation_adjudications
    WHERE deployment_id = :deployment_id
      AND (observation_id = :subject_id OR related_observation_id = :subject_id)
    ORDER BY decided_at, adjudication_id
    """
)

_ENTITY_TRANSCRIPT = text(
    """
    -- an entity's decision history braids two append-only logs: how each of
    -- its mentions resolved (resolution_decisions) and every merge it took
    -- part in (merge_events), newest-last across both. related_id is the
    -- COUNTERPART entity of a merge (never the subject); a reversed merge is
    -- an unmerge. The per-arm primary key breaks decided_at ties so the
    -- recent-first truncation boundary is deterministic under batch inserts.
    SELECT subject_kind, outcome, method, confidence, related_id,
           decided_by, decided_at, features
    FROM (
        SELECT 'entity' AS subject_kind,
               CASE WHEN is_new_entity THEN 'new_entity' ELSE 'linked' END
                   AS outcome,
               method::text AS method, confidence,
               mention_id AS related_id, decided_by::text AS decided_by,
               decided_at, features, decision_id AS event_id
        FROM resolution_decisions
        WHERE deployment_id = :deployment_id AND entity_id = :subject_id
        UNION ALL
        SELECT 'entity' AS subject_kind,
               CASE WHEN reversed_by IS NOT NULL THEN 'unmerge' ELSE 'merge' END
                   AS outcome,
               'merge_event' AS method, NULL::real AS confidence,
               CASE WHEN survivor_id = :subject_id THEN absorbed_id
                    ELSE survivor_id END AS related_id,
               decided_by::text AS decided_by, decided_at,
               evidence AS features, merge_id AS event_id
        FROM merge_events
        WHERE deployment_id = :deployment_id
          AND (survivor_id = :subject_id OR absorbed_id = :subject_id)
    ) braided
    ORDER BY decided_at, event_id
    """
)

_KPAGE_TRANSCRIPT = text(
    """
    -- a K page's provenance is its compile history: each recompilation, what
    -- it cited, and the writer that produced it (S35)
    SELECT 'k_page' AS subject_kind,
           'compiled' AS outcome, writer_version AS method,
           NULL::real AS confidence, artifact_id AS related_id,
           'writer'::text AS decided_by, compiled_at AS decided_at,
           jsonb_build_object('cited', cited_count, 'uncited', uncited_count,
               'evidence_added', evidence_added,
               'evidence_removed', evidence_removed) AS features
    FROM knowledge_compilations
    WHERE deployment_id = :deployment_id AND artifact_id = :subject_id
    ORDER BY compiled_at, compilation_id
    """
)

_TRANSCRIPT_BY_KIND = {
    "relation": _RELATION_TRANSCRIPT,
    "observation": _OBSERVATION_TRANSCRIPT,
    "entity": _ENTITY_TRANSCRIPT,
    "k_page": _KPAGE_TRANSCRIPT,
}


_DELTA_FEED = text(
    """
    -- the change feed: one timestamped row per change, unioned across the
    -- evidence kinds and K pages, filtered by :since and an optional :kinds
    -- subset. Every branch dates its change on a real column, so a follow-up
    -- delta resumes deterministically from the oldest `at` returned.
    WITH feed AS (
        SELECT 'relation' AS kind, 'new' AS change, relation_id AS id,
               coalesce(fact_label, predicate) AS label, ingested_at AS at
        FROM relations
        WHERE deployment_id = :deployment_id AND ingested_at > :since
        UNION ALL
        SELECT 'relation', 'invalidated', relation_id,
               coalesce(fact_label, predicate), invalidated_at
        FROM relations
        WHERE deployment_id = :deployment_id AND invalidated_at > :since
        UNION ALL
        -- a supersede caps the OLD relation's window (ra.relation_id), dated
        -- by the adjudication that closed it
        SELECT 'relation', 'capped', r.relation_id,
               coalesce(r.fact_label, r.predicate), ra.decided_at
        FROM relation_adjudications ra
        JOIN relations r ON r.deployment_id = ra.deployment_id
                        AND r.relation_id = ra.relation_id
        WHERE ra.deployment_id = :deployment_id
          AND ra.outcome = 'supersede' AND ra.decided_at > :since
        UNION ALL
        SELECT 'observation', 'new', observation_id, statement, ingested_at
        FROM observations
        WHERE deployment_id = :deployment_id AND ingested_at > :since
        UNION ALL
        SELECT 'observation', 'invalidated', observation_id, statement,
               invalidated_at
        FROM observations
        WHERE deployment_id = :deployment_id AND invalidated_at > :since
        UNION ALL
        -- an observation supersede caps the OLD observation's window, dated
        -- by the adjudication (symmetric with the relation cap above)
        SELECT 'observation', 'capped', o.observation_id, o.statement,
               oa.decided_at
        FROM observation_adjudications oa
        JOIN observations o ON o.deployment_id = oa.deployment_id
                           AND o.observation_id = oa.observation_id
        WHERE oa.deployment_id = :deployment_id
          AND oa.outcome = 'supersede' AND oa.decided_at > :since
        UNION ALL
        SELECT 'claim', 'new', claim_id, left(claim_text, 80), ingested_at
        FROM claims
        WHERE deployment_id = :deployment_id AND ingested_at > :since
        UNION ALL
        SELECT 'page', 'recompiled', artifact_id, NULL, compiled_at
        FROM knowledge_compilations
        WHERE deployment_id = :deployment_id AND compiled_at > :since
    )
    SELECT kind, change, id, label, at
    FROM feed
    WHERE (CAST(:kinds AS text[]) IS NULL OR kind = ANY(:kinds))
      -- resume strictly before the cursor over the FULL (at, id) order, so a
      -- page boundary that splits rows sharing a timestamp never drops the
      -- tied remainder
      AND (
          CAST(:cursor_at AS timestamptz) IS NULL
          OR at < :cursor_at
          OR (at = :cursor_at AND id < CAST(:cursor_id AS uuid))
      )
    ORDER BY at DESC, id DESC
    LIMIT :fetch
    """
)

_PAGES_ABOUT = text(
    """
    -- the rule-key inverted index read backwards: which artifacts route on
    -- (:key_kind, :key_value). One row per artifact (a page may hold several
    -- matching rules), each carrying its compile state and a stale flag —
    -- a page whose refresh is still queued has not caught up to its inputs.
    SELECT * FROM (
        SELECT DISTINCT ON (a.artifact_id)
               a.artifact_id, a.page_kind::text AS page_kind, a.git_path,
               a.page_summary, a.last_compiled_at, a.status::text AS status,
               (a.page_kind = 'compiled' AND (
                 a.status::text = 'stale' OR EXISTS (
                    SELECT 1 FROM knowledge_refresh_queue q
                    WHERE q.deployment_id = a.deployment_id
                      AND q.artifact_id = a.artifact_id
                      AND q.processed_at IS NULL
               ))) AS stale,
               CASE WHEN a.page_kind = 'authored' THEN (
                 SELECT count(*) FROM knowledge_refresh_queue q
                 WHERE q.deployment_id = a.deployment_id
                   AND q.artifact_id = a.artifact_id
                   AND q.trigger = 'authored_review'
                   AND q.processed_at IS NULL
               ) ELSE 0 END AS open_review_flags,
               CASE WHEN a.page_kind = 'authored' THEN COALESCE((
                 SELECT bool_or(
                   COALESCE((q.payload ->> 'redaction_required')::boolean, false)
                 )
                 FROM knowledge_refresh_queue q
                 WHERE q.deployment_id = a.deployment_id
                   AND q.artifact_id = a.artifact_id
                   AND q.trigger = 'authored_review'
                   AND q.processed_at IS NULL
               ), false) ELSE false END AS redaction_required
        FROM knowledge_rule_keys rk
        JOIN knowledge_page_rules pr ON pr.deployment_id = rk.deployment_id
                                    AND pr.rule_id = rk.rule_id
        JOIN knowledge_artifacts a ON a.deployment_id = pr.deployment_id
                                  AND a.artifact_id = pr.artifact_id
        WHERE rk.deployment_id = :deployment_id
          AND rk.key_kind = CAST(:key_kind AS rule_key_kind)
          AND rk.key_value = :key_value
          AND pr.status = 'active'  -- a deprecated rule no longer routes
          AND a.status::text <> 'tombstoned'
        ORDER BY a.artifact_id
    ) page
    ORDER BY page.last_compiled_at DESC NULLS LAST, page.artifact_id
    """
)

_AGG_COUNT = text(
    """
    SELECT NULL::text AS key, count(*) AS count, NULL::uuid AS entity_id
    FROM relations
    WHERE deployment_id = :deployment_id AND invalidated_at IS NULL
      AND (CAST(:subject_entity_id AS uuid) IS NULL
           OR subject_entity_id = :subject_entity_id)
      AND (CAST(:predicate AS text) IS NULL OR predicate = :predicate)
    """
)

_AGG_GROUP_BY_PREDICATE = text(
    """
    SELECT predicate AS key, count(*) AS count, NULL::uuid AS entity_id
    FROM relations
    WHERE deployment_id = :deployment_id AND invalidated_at IS NULL
      AND subject_entity_id = :subject_entity_id
    GROUP BY predicate
    ORDER BY count DESC, predicate
    LIMIT :fetch
    """
)

_AGG_GROUP_BY_OBJECT = text(
    """
    SELECT e.canonical_name AS key, count(*) AS count,
           r.object_entity_id AS entity_id
    FROM relations r
    JOIN entities e ON e.deployment_id = r.deployment_id
                   AND e.entity_id = r.object_entity_id
    WHERE r.deployment_id = :deployment_id AND r.invalidated_at IS NULL
      AND r.subject_entity_id = :subject_entity_id
      AND (CAST(:predicate AS text) IS NULL OR r.predicate = :predicate)
    GROUP BY e.canonical_name, r.object_entity_id
    ORDER BY count DESC, e.canonical_name
    LIMIT :fetch
    """
)

_AGG_TIMELINE = text(
    """
    -- an entity's facts by year — relations it is either end of AND the
    -- observations about it, so the timeline is the whole fact evolution,
    -- not just relations
    SELECT to_char(date_trunc('year', ts), 'YYYY') AS key,
           count(*) AS count, NULL::uuid AS entity_id
    FROM (
        SELECT coalesce(valid_from, ingested_at) AS ts
        FROM relations
        WHERE deployment_id = :deployment_id AND invalidated_at IS NULL
          AND (subject_entity_id = :subject_entity_id
               OR object_entity_id = :subject_entity_id)
        UNION ALL
        SELECT coalesce(valid_from, ingested_at) AS ts
        FROM observations
        WHERE deployment_id = :deployment_id AND invalidated_at IS NULL
          AND subject_entity_id = :subject_entity_id
    ) facts
    GROUP BY 1
    ORDER BY 1
    """
)

_AGG_DELTA_TOP_ENTITIES = text(
    """
    -- facts gained since T, grouped by the subject entity, bounded by the
    -- delta window (S30): a leaderboard of what moved, over relations AND
    -- observations, not a full-history scan
    SELECT e.canonical_name AS key, sum(gained.cnt) AS count,
           gained.entity_id AS entity_id
    FROM (
        SELECT subject_entity_id AS entity_id, count(*) AS cnt
        FROM relations
        WHERE deployment_id = :deployment_id AND ingested_at > :since
        GROUP BY subject_entity_id
        UNION ALL
        SELECT subject_entity_id AS entity_id, count(*) AS cnt
        FROM observations
        WHERE deployment_id = :deployment_id AND ingested_at > :since
        GROUP BY subject_entity_id
    ) gained
    JOIN entities e ON e.deployment_id = :deployment_id
                   AND e.entity_id = gained.entity_id
    GROUP BY e.canonical_name, gained.entity_id
    ORDER BY count DESC, e.canonical_name
    LIMIT :fetch
    """
)

_AGG_TYPED_ABSENCE = text(
    """
    -- entities of a type with NO live relation of a predicate (S40): an
    -- anti-join, answerable because the ontology types entities. Each bucket
    -- IS one absent entity (count 1), so the total is how many lack it.
    SELECT e.canonical_name AS key, 1 AS count, e.entity_id AS entity_id
    FROM entities e
    WHERE e.deployment_id = :deployment_id AND e.status = 'active'
      AND e.type = :entity_type
      AND NOT EXISTS (
          SELECT 1 FROM relations r
          WHERE r.deployment_id = e.deployment_id
            AND r.subject_entity_id = e.entity_id
            AND r.predicate = :predicate
            AND r.invalidated_at IS NULL
      )
    ORDER BY e.canonical_name
    LIMIT :fetch
    """
)

_AGGREGATE_FORMS: dict[str, tuple[TextClause, frozenset[str]]] = {
    "count": (_AGG_COUNT, frozenset()),
    "group_by_predicate": (_AGG_GROUP_BY_PREDICATE, frozenset({"subject_entity_id"})),
    "group_by_object": (_AGG_GROUP_BY_OBJECT, frozenset({"subject_entity_id"})),
    "timeline": (_AGG_TIMELINE, frozenset({"subject_entity_id"})),
    "delta_top_entities": (_AGG_DELTA_TOP_ENTITIES, frozenset({"since"})),
    "typed_absence": (_AGG_TYPED_ABSENCE, frozenset({"entity_type", "predicate"})),
}

_SCAN_EXPORTS = {
    "relation": text(
        """
        SELECT relation_id AS id, coalesce(fact_label, predicate) AS label,
               ingested_at AS at
        FROM relations
        WHERE deployment_id = :deployment_id
        ORDER BY ingested_at, relation_id
        """
    ),
    "observation": text(
        """
        SELECT observation_id AS id, statement AS label, ingested_at AS at
        FROM observations
        WHERE deployment_id = :deployment_id
        ORDER BY ingested_at, observation_id
        """
    ),
    "claim": text(
        """
        SELECT claim_id AS id, left(claim_text, 120) AS label,
               ingested_at AS at
        FROM claims
        WHERE deployment_id = :deployment_id
        ORDER BY ingested_at, claim_id
        """
    ),
}


_CONTRADICTION_MEMBERS_RELATIONS = text(
    """
    SELECT contradiction_group, relation_id AS fact_id,
           coalesce(fact_label, predicate) AS label, evidence_count,
           valid_from, valid_until, ingested_at, invalidated_at
    FROM relations
    WHERE deployment_id = :deployment_id
      AND contradiction_group = ANY(:groups)
      AND invalidated_at IS NULL
    ORDER BY contradiction_group, ingested_at, relation_id
    """
)

_CONTRADICTION_MEMBERS_OBSERVATIONS = text(
    """
    SELECT contradiction_group, observation_id AS fact_id,
           statement AS label, evidence_count,
           valid_from, valid_until, ingested_at, invalidated_at
    FROM observations
    WHERE deployment_id = :deployment_id
      AND contradiction_group = ANY(:groups)
      AND invalidated_at IS NULL
    ORDER BY contradiction_group, ingested_at, observation_id
    """
)

_CONTRADICTION_MEMBERS = {
    "relation": _CONTRADICTION_MEMBERS_RELATIONS,
    "observation": _CONTRADICTION_MEMBERS_OBSERVATIONS,
}

_OPEN_SUPPORT_FLAGS = text(
    """
    -- a fact under an OPEN support_withdrawn review carries support=withdrawn
    -- in the envelope (D54: flagged, not vanished). "Open" is pending OR
    -- deferred — an 'uncertain' verdict defers but leaves the flag standing,
    -- matching review._SELECT_OPEN_FLAG and the lifecycle reconciler.
    SELECT (candidate ->> 'fact_id')::uuid AS fact_id
    FROM review_queue
    WHERE deployment_id = :deployment_id
      AND item_kind = 'support_withdrawn'
      AND status IN ('pending', 'deferred')
      AND (candidate ->> 'fact_id') = ANY(:fact_ids)
    """
)
