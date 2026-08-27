"""The zero-LLM query engine (retrieval §2-§3): resolve, lookup, search, hydrate.

The one correctness rule is D48: ranked PostgreSQL P1 statements nominate only
through the invariant-bearing authority joins, and every returned record then
passes by-ID hydration against the live spine. A superseded fact can never be
served as current, and hydration rejects are counted in
`dropped_by_hydration` so results are honest about their denominator. No
primitive calls an LLM; reads never trigger anything.
"""

import base64
import binascii
from collections import Counter
from collections.abc import Callable
from collections.abc import Iterator
from collections.abc import Sequence
from datetime import datetime
from datetime import UTC
from functools import wraps
from itertools import batched
import math
from time import monotonic
from typing import cast
from typing import Final
from typing import Literal
import unicodedata
from uuid import UUID

from sqlalchemy import text
from sqlalchemy import TextClause
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine
from sqlalchemy.engine import RowMapping

from rememberstack.core.embedding_input_policy import EMBEDDING_INPUT_POLICY_VERSION
from rememberstack.core.ranking import DEFAULT_RRF_K
from rememberstack.core.ranking import reciprocal_rank_fusion
from rememberstack.core.ranking import rerank_by_signal
from rememberstack.core.ranking import rerank_by_weighted_signals
from rememberstack.model import AggregateBucket
from rememberstack.model import AggregateReport
from rememberstack.model import AtTemporalScope
from rememberstack.model import ChangeRecord
from rememberstack.model import ChunkEvidenceResult
from rememberstack.model import CoMember
from rememberstack.model import Contradiction
from rememberstack.model import current_temporal_scope
from rememberstack.model import CurrentTemporalScope
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
from rememberstack.model import HistoryTemporalScope
from rememberstack.model import Negative
from rememberstack.model import NegativeKind
from rememberstack.model import OverlapTemporalScope
from rememberstack.model import PageRef
from rememberstack.model import ProviderCallError
from rememberstack.model import RankedItem
from rememberstack.model import ScanRow
from rememberstack.model import SourceRecord
from rememberstack.model import TranscriptEntry
from rememberstack.model import Truncation
from rememberstack.model import Validity
from rememberstack.model.assured_operations import AtFactTime
from rememberstack.model.assured_operations import CurrentFactTime
from rememberstack.model.assured_operations import FactTime
from rememberstack.model.assured_operations import OverlapFactTime
from rememberstack.ports.model_provider import ModelProviderPort
from rememberstack.ports.p1_index import ClaimVectorLookupPort
from rememberstack.ports.p1_index import P1_VECTOR_DIMENSIONS
from rememberstack.ports.p1_index import P1Nomination
from rememberstack.ports.p1_index import P1SearchPort
from rememberstack.spine.entity_registry import normalized_lemma
from rememberstack.spine.surface_cost import open_surface_scope
from rememberstack.spine.surface_cost import SqlSurfaceCostRecorder
from rememberstack.spine.surface_cost import SurfaceCallSite
from rememberstack.spine.surface_cost import SurfaceCostKind
from rememberstack.spine.surface_cost import SurfaceCostOutcome

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

This is candidate work, not the returned evidence budget: every bounded Batch B
composition returns at most 50 evidence records. Four hundred matches the existing
interactive nomination ceiling and keeps hub-entity/time-window vector reads
bounded without ever nominating globally and filtering afterward.
"""

FACT_CONTEXT_EVIDENCE_BUDGET: Final = 60
"""Hard maximum evidence associations in one fact-context envelope."""

FACT_CONTEXT_CANDIDATE_K: Final = 200
"""Descriptor-pinned fact nomination depth; deliberately not a public knob."""

FACT_CONTEXT_CONFIRMATION_BATCH_SIZE: Final = 30
"""Maximum fact nominations confirmed by PostgreSQL in one interactive query."""

FACT_CONTEXT_CONFIRMATION_MIN_BATCH_SIZE: Final = 16
"""Default-k plus one truncation sentinel, without forcing 30-row expansion."""

FACT_CONTEXT_DATABASE_BUDGET_SECONDS: Final = 25.0
"""Operation wall-clock budget; each PostgreSQL statement gets the remainder."""

TESTIMONY_CONTEXT_K: Final = 50
"""The testimony channel's default per-grain result cap."""

TESTIMONY_CONTEXT_CANDIDATE_K: Final = 200
"""The testimony channel's default per-channel nomination cap."""

CONTEXT_ENTITY_LIMIT: Final = 20
"""Maximum explicit survivor anchors on a context operation."""

_EVIDENCE_STANCES: Final = ("supports", "contradicts")
"""Stable two-stance order for selection and exact-total disclosure."""

_RERANK_SIGNALS = {"graph_distance": True, "evidence_count": False}
"""The inspectable rerank signals and whether each sorts ascending: nearer
the focal entity wins (ascending), more corroboration wins (descending)."""

_BOUNDED_AGGREGATE_FORMS = frozenset(
    {"group_by_predicate", "group_by_object", "delta_top_entities", "predicate_absence"}
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


def _with_surface[**P, T](
    surface: SurfaceCostKind,
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Open a D91 request scope around a public QueryEngine method."""

    def decorator(method: Callable[P, T]) -> Callable[P, T]:
        @wraps(method)
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
            with open_surface_scope(surface=surface):
                return method(*args, **kwargs)

        return wrapped

    return decorator


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
        surface_cost: SqlSurfaceCostRecorder | None = None,
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
        self._surface_cost = surface_cost

    def resolve(
        self,
        *,
        deployment_id: UUID,
        name: str,
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
        return _envelope(
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
        entity_id, resolution = self._resolve_context_entity(
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
        return _envelope(
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

    @_with_surface(SurfaceCostKind.LIBRARY)
    def claims_about(
        self, *, deployment_id: UUID, entity: str, query: str | None = None, k: int = 20
    ) -> Envelope:
        """Return current testimony from chunks mentioning one resolved entity."""
        _validate_batch_b_k(k=k)
        entity_id, resolution = self._resolve_context_entity(
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
            deployment_id=deployment_id,
            claim_ids=candidate_ids,
            query=query,
            k=k,
            call_site=SurfaceCallSite.CLAIMS_ABOUT,
        )
        evidence, dropped, _coverage = self._confirm_claims(
            deployment_id=deployment_id, claim_ids=ordered_ids
        )
        confirmed = {record.claim_id for record in evidence}
        return _envelope(
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

    @_with_surface(SurfaceCostKind.LIBRARY)
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
            deployment_id=deployment_id,
            claim_ids=candidate_ids,
            query=query,
            k=k,
            call_site=SurfaceCallSite.CLAIMS_AS_OF,
        )
        evidence, dropped, _coverage = self._confirm_claims(
            deployment_id=deployment_id, claim_ids=ordered_ids, current_only=False
        )
        confirmed = {record.claim_id for record in evidence}
        return _envelope(
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
            return _envelope(
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
        chunks, dropped, _coverage = self._confirm_chunks(
            deployment_id=deployment_id, chunk_ids=candidate_ids
        )
        requested = radius * 2 + 1
        edge_truncated = len(candidate_ids) < requested
        return _envelope(
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

    @_with_surface(SurfaceCostKind.OPERATION)
    def fact_context(
        self,
        *,
        deployment_id: UUID,
        query: str,
        entity_ids: tuple[UUID, ...] = (),
        k: int = 15,
        evidence_per_fact: int = 3,
        time: FactTime | None = None,
        evaluated_at: datetime | None = None,
    ) -> Envelope:
        """Return adjudicated facts under an explicit current-belief time scope."""
        _validate_fact_context_bounds(k=k, evidence_per_fact=evidence_per_fact)
        entity_ids = _validate_context_entity_ids(entity_ids=entity_ids)
        selected_time = time or CurrentFactTime()
        evaluation = evaluated_at or datetime.now(UTC)
        database_deadline = monotonic() + FACT_CONTEXT_DATABASE_BUDGET_SECONDS
        with self._engine.connect() as connection:
            if entity_ids:
                _configure_fact_context_connection(
                    connection=connection, deadline=database_deadline
                )
                if not _context_entities_are_current(
                    connection=connection,
                    deployment_id=deployment_id,
                    entity_ids=entity_ids,
                ):
                    return _unknown_context_entity(
                        grain=Grain.FACT,
                        evaluated_at=evaluation,
                        temporal_scope=_fact_temporal_scope(
                            time=selected_time, evaluated_at=evaluation
                        ),
                    )
        nominated = self._nominate_fact_context(
            deployment_id=deployment_id,
            query=query,
            entity_ids=entity_ids,
            time=selected_time,
            evaluated_at=evaluation,
        )
        candidate_keys = tuple(
            (item.qualifier, UUID(item.item_id))
            for item in nominated[:FACT_CONTEXT_CANDIDATE_K]
            if item.qualifier in {"relation", "observation"}
        )
        evidence_by_fact_stance: dict[tuple[str, UUID, str], list[RowMapping]] = {}
        totals: dict[tuple[str, UUID, str], int] = {}
        with self._engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as connection:
            confirmed_rows: list[RowMapping] = []
            visited_candidates = 0
            confirmation_batch_size = _fact_context_confirmation_batch_size(k=k)
            for batch in batched(candidate_keys, confirmation_batch_size):
                batch_rows = _confirm_fact_context(
                    connection=connection,
                    deployment_id=deployment_id,
                    candidate_keys=tuple(batch),
                    time=selected_time,
                    evaluated_at=evaluation,
                    entity_ids=entity_ids,
                    deadline=database_deadline,
                )
                visited_candidates += len(batch)
                confirmed_rows.extend(batch_rows)
                # Confirm one extra row so truncation stays truthful without
                # forcing all 200 semantic nominations through the deep gate.
                if len(confirmed_rows) > k:
                    break
            has_more_confirmed = len(confirmed_rows) > k
            fact_rows = tuple(confirmed_rows[:k])
            evidence_rows = (
                _fact_context_evidence(
                    connection=connection,
                    deployment_id=deployment_id,
                    fact_rows=fact_rows,
                    evidence_per_fact=evidence_per_fact,
                    deadline=database_deadline,
                )
                if fact_rows
                else []
            )
            for row in evidence_rows:
                key = (str(row["kind"]), row["fact_id"], str(row["stance"]))
                evidence_by_fact_stance.setdefault(key, []).append(row)
                totals[key] = int(row["evidence_total"])
            confirmed_facts = self._enrich_fact_context_facts(
                connection=connection,
                deployment_id=deployment_id,
                rows=fact_rows,
                time=selected_time,
                evaluated_at=evaluation,
                deadline=database_deadline,
            )
        facts = confirmed_facts[:k]
        selected = _select_fact_evidence(
            fact_keys=tuple((fact.kind, fact.fact_id) for fact in facts),
            evidence_by_fact_stance=evidence_by_fact_stance,
            evidence_per_fact=evidence_per_fact,
            budget=FACT_CONTEXT_EVIDENCE_BUDGET,
        )
        returned_counts = Counter(
            (str(row["kind"]), row["fact_id"], str(row["stance"])) for row in selected
        )
        associations = tuple(
            FactEvidence.model_validate(
                {
                    "fact_kind": str(row["kind"]),
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
                fact_kind=cast(Literal["relation", "observation"], fact.kind),
                fact_id=fact.fact_id,
                stance=stance,
                returned=returned_counts[(fact.kind, fact.fact_id, stance)],
                total=totals.get((fact.kind, fact.fact_id, stance), 0),
            )
            for fact in facts
            for stance in _EVIDENCE_STANCES
        )
        # Only candidates that reached PostgreSQL and failed confirmation are
        # hydration drops. Unvisited nominations are disclosed by truncation.
        dropped = visited_candidates - len(confirmed_rows)
        candidate_depth_exhausted = len(nominated) > FACT_CONTEXT_CANDIDATE_K
        confirmation_incomplete = visited_candidates < len(candidate_keys)
        return _envelope(
            grain=Grain.FACT,
            temporal_scope=_fact_temporal_scope(
                time=selected_time, evaluated_at=evaluation
            ),
            facts=facts,
            evidence=tuple(evidence_by_id.values()),
            fact_evidence=associations,
            evidence_totals=exact_totals,
            freshness=_freshness(at=evaluation),
            truncation=Truncation(
                truncated=(
                    has_more_confirmed
                    or confirmation_incomplete
                    or candidate_depth_exhausted
                ),
                returned=len(facts),
                estimated_total=len(confirmed_rows),
                total_is_exact=(
                    not candidate_depth_exhausted and not confirmation_incomplete
                ),
            ),
            dropped_by_hydration=dropped,
            negative=None
            if facts
            else Negative(
                kind=NegativeKind.KNOWN_EMPTY,
                explanation=f"no adjudicated facts match {query!r}",
                workaround=("broaden the query or inspect source testimony"),
            ),
        )

    @_with_surface(SurfaceCostKind.OPERATION)
    def testimony_context(
        self,
        *,
        deployment_id: UUID,
        query: str,
        entity_ids: tuple[UUID, ...] = (),
        k: int = 50,
        candidate_k: int = 200,
        evaluated_at: datetime | None = None,
    ) -> Envelope:
        """Return current claims and source passages, never facts or entities."""
        _validate_testimony_context_bounds(k=k, candidate_k=candidate_k)
        entity_ids = _validate_context_entity_ids(entity_ids=entity_ids)
        evaluation = evaluated_at or datetime.now(UTC)
        if entity_ids:
            with self._engine.connect() as connection:
                if not _context_entities_are_current(
                    connection=connection,
                    deployment_id=deployment_id,
                    entity_ids=entity_ids,
                ):
                    return _unknown_context_entity(
                        grain=Grain.EVIDENCE, evaluated_at=evaluation
                    )
        answer = self._testimony_context_retrieval(
            deployment_id=deployment_id,
            query=query,
            k=k,
            candidate_k=candidate_k,
            entity_ids=entity_ids,
        )
        return answer.model_copy(
            update={
                "temporal_scope": current_temporal_scope(evaluated_at=evaluation),
                "freshness": answer.freshness.model_copy(
                    update={"pg_live_ts": evaluation}
                ),
            }
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
        return _envelope(
            grain=Grain.FACT,
            temporal_scope=(
                AtTemporalScope(at=valid_at, evaluated_at=as_of, believed_at=as_of)
                if valid_at is not None
                else current_temporal_scope(evaluated_at=as_of)
            ),
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

    @_with_surface(SurfaceCostKind.LOOKUP)
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
                vector=self._embed(
                    query=property_query,
                    call_site=SurfaceCallSite.LOOKUP_OBSERVATIONS,
                    deployment_id=deployment_id,
                ),
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
        return _envelope(
            grain=Grain.FACT,
            temporal_scope=(
                AtTemporalScope(at=valid_at, evaluated_at=as_of, believed_at=as_of)
                if valid_at is not None
                else current_temporal_scope(evaluated_at=as_of)
            ),
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

    @_with_surface(SurfaceCostKind.SEARCH)
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
            deployment_id=deployment_id,
            query=query,
            k=k,
            channel=channel,
            call_site=SurfaceCallSite.SEARCH_CLAIMS,
        )
        evidence, dropped, _coverage = self._confirm_claims(
            deployment_id=deployment_id,
            claim_ids=tuple(UUID(item) for item in nominated),
        )
        return _envelope(
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

    @_with_surface(SurfaceCostKind.LIBRARY)
    def nominate_claims(
        self,
        *,
        deployment_id: UUID,
        query: str,
        k: int = 10,
        channel: Literal["semantic", "bm25"] = "semantic",
    ) -> Envelope:
        """Rank claim IDs without returning unconfirmed claim content.

        This is the cheap, projection-only half of D48 for operation composition:
        parallel channels can fuse their candidate orderings before one
        `hydrate_claims` confirmation. Candidate UUIDs and ranks are not facts.
        """
        nominated = self._nominate_claim_ids(
            deployment_id=deployment_id,
            query=query,
            k=k,
            channel=channel,
            call_site=SurfaceCallSite.NOMINATE_CLAIMS,
        )
        return _nomination_envelope(
            ids=nominated, empty_explanation="no claims were nominated"
        )

    @_with_surface(SurfaceCostKind.SEARCH)
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
            deployment_id=deployment_id,
            query=query,
            k=k,
            channel=channel,
            call_site=SurfaceCallSite.SEARCH_CHUNKS,
        )
        chunks, dropped, _coverage = self._confirm_chunks(
            deployment_id=deployment_id,
            chunk_ids=tuple(UUID(item) for item in nominated),
        )
        return _envelope(
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

    @_with_surface(SurfaceCostKind.LIBRARY)
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
            deployment_id=deployment_id,
            query=query,
            k=k,
            channel=channel,
            call_site=SurfaceCallSite.NOMINATE_CHUNKS,
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
        call_site: SurfaceCallSite,
    ) -> tuple[str, ...]:
        """Run exactly one validated P1 claim-nomination channel."""
        _validate_nomination_request(k=k, channel=channel)
        if channel == "semantic":
            return self._search_index.search_claims(
                deployment_id=str(deployment_id),
                vector=self._embed(
                    query=query, call_site=call_site, deployment_id=deployment_id
                ),
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
        call_site: SurfaceCallSite,
    ) -> tuple[str, ...]:
        """Run exactly one validated P1 source-chunk nomination channel."""
        _validate_nomination_request(k=k, channel=channel)
        if channel == "semantic":
            return self._search_index.search_chunks(
                deployment_id=str(deployment_id),
                vector=self._embed(
                    query=query, call_site=call_site, deployment_id=deployment_id
                ),
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
                return _envelope(
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
        return _envelope(
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
            return _envelope(
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
        return _envelope(
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
        arm (kept as the named surface the HTTP API and saved queries bind to)."""
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
        assured operation applies, exposed so an agent's ad-hoc channel set fuses
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
        return _envelope(
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
        entity_ids: tuple[UUID, ...] = (),
    ) -> Envelope:
        """Confirm claim ids into evidence rows, keeping any prior ranking.

        The D48 confirmation hop for an ordered claim-id list (typically the
        output of `fuse`/`rerank`): re-reads each claim from the spine and
        drops what no longer confirms. When a ranking is supplied, scores and
        order are preserved on the envelope for the confirmed ids so a fused
        result is usable without a second tool call. A caller may apply ``limit``
        after the complete supplied candidate pool has been confirmed, so a
        rejected head candidate is deterministically replaced from the supplied
        tail. Exact normalized-text duplicates may be grouped before that cut.
        """
        if limit is not None and limit < 1:
            raise ValueError("hydrate_claims limit must be at least 1")
        ordered_ids = tuple(claim_ids)
        evidence, dropped, coverage = self._confirm_claims(
            deployment_id=deployment_id, claim_ids=ordered_ids, entity_ids=entity_ids
        )
        positions = {claim_id: index for index, claim_id in enumerate(ordered_ids)}
        if entity_ids:
            evidence = tuple(
                sorted(
                    evidence,
                    key=lambda item: (
                        -coverage.get(item.claim_id, 0),
                        positions[item.claim_id],
                        item.claim_id.bytes,
                    ),
                )
            )
        if group_exact_text:
            evidence = _group_claim_evidence(evidence=evidence)
        if limit is not None:
            evidence = evidence[:limit]
        ranking_by_id = {item.item_id: item for item in ranking}
        kept_ranking = tuple(
            ranking_by_id[record.claim_id]
            for record in evidence
            if record.claim_id in ranking_by_id
        )
        return _envelope(
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
        entity_ids: tuple[UUID, ...] = (),
    ) -> Envelope:
        """Confirm chunk ids into live source evidence, preserving scores.

        A caller may apply ``limit`` after the complete supplied candidate pool,
        allowing confirmed tail candidates to refill head candidates that no
        longer pass D48 without another projection read.
        """
        if limit is not None and limit < 1:
            raise ValueError("hydrate_chunks limit must be at least 1")
        ordered_ids = tuple(chunk_ids)
        chunks, dropped, coverage = self._confirm_chunks(
            deployment_id=deployment_id, chunk_ids=ordered_ids, entity_ids=entity_ids
        )
        positions = {chunk_id: index for index, chunk_id in enumerate(ordered_ids)}
        if entity_ids:
            chunks = tuple(
                sorted(
                    chunks,
                    key=lambda item: (
                        -coverage.get(item.chunk_id, 0),
                        positions[item.chunk_id],
                        item.chunk_id.bytes,
                    ),
                )
            )
        if limit is not None:
            chunks = chunks[:limit]
        ranking_by_id = {item.item_id: item for item in ranking}
        kept_ranking = tuple(
            ranking_by_id[record.chunk_id]
            for record in chunks
            if record.chunk_id in ranking_by_id
        )
        return _envelope(
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
        continuations = tuple(
            truncation.continuation
            for truncation in truncations
            if truncation.continuation is not None
        )
        if len(continuations) > 1:
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
        truncation = None
        if truncations:
            estimated_total = sum(
                envelope.truncation.estimated_total
                if envelope.truncation is not None
                else len(envelope.evidence) + len(envelope.chunks)
                for envelope in inputs
            )
            truncation = Truncation(
                truncated=any(item.truncated for item in truncations),
                returned=len(evidence) + len(chunks),
                estimated_total=estimated_total,
                total_is_exact=all(item.total_is_exact for item in truncations),
                continuation=continuations[0] if continuations else None,
            )
        return _envelope(
            grain=Grain.EVIDENCE,
            evidence=evidence,
            chunks=chunks,
            freshness=_freshness(),
            truncation=truncation,
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
            return _envelope(
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
        return _envelope(grain=Grain.EVIDENCE, ranking=ranked, freshness=_freshness())

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
        return _envelope(
            grain=Grain.COMPOSITE,
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
        (`predicate`, `doc_source`). Each page reports its
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
        return _envelope(
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
        since: datetime | None = None,
        limit: int = 50,
    ) -> Envelope:
        """An enumerated aggregate — never a general GROUP BY (retrieval §9).

        Each `form` is a bounded SQL shape with a predictable cost, because
        an unbounded ad-hoc aggregation over 10⁸ rows is a denial of service
        against the spine (the escape hatch is `scan`). The forms: `count`,
        `group_by_predicate`, `group_by_object`, `timeline` (an entity's
        facts by year), `delta_top_entities` (facts gained since T, bounded
        by the delta window — S30), and `predicate_absence` (active entities
        with no live relation of a predicate — S40, with no type filter).
        A `limit`-bounded form that hits its cap
        sets an explicit truncation marker — the bucket total is then a
        floor, never a silent "this is all there is". An unknown form is a
        typed `boundary`.
        """
        if limit < 1:
            raise ValueError("limit must be at least 1")
        builder = _AGGREGATE_FORMS.get(form)
        if builder is None:
            return _envelope(
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
            "since": since,
            "fetch": limit + 1,  # one extra row reveals a truncation honestly
        }
        for required, value in (
            ("subject_entity_id", subject_entity_id),
            ("predicate", predicate),
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
        return _envelope(
            grain=Grain.FACT,
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

    def _testimony_context_retrieval(
        self,
        *,
        deployment_id: UUID,
        query: str,
        k: int = TESTIMONY_CONTEXT_K,
        candidate_k: int = TESTIMONY_CONTEXT_CANDIDATE_K,
        entity_ids: tuple[UUID, ...] = (),
    ) -> Envelope:
        """Run the testimony hybrid, optionally ranking inside an entity scope.

        This deliberately mirrors the registered operation's two independent
        semantic/BM25 nominations, RRF, one confirmation per grain, and typed
        claim/chunk union. Both the public testimony operation and the saved
        query examples reuse this private implementation.
        """

        def hydrate_claim_context() -> Envelope:
            semantic = self._nominate_testimony_claims(
                deployment_id=deployment_id,
                query=query,
                k=candidate_k,
                channel="semantic",
                entity_ids=entity_ids,
            )
            lexical = self._nominate_testimony_claims(
                deployment_id=deployment_id,
                query=query,
                k=candidate_k,
                channel="bm25",
                entity_ids=entity_ids,
            )
            fused = self.fuse(
                rankings=(
                    tuple(item.item_id for item in semantic.ranking),
                    tuple(item.item_id for item in lexical.ranking),
                ),
                k=DEFAULT_RRF_K,
            )
            return _bound_testimony_result(
                envelope=self.hydrate_claims(
                    deployment_id=deployment_id,
                    claim_ids=tuple(item.item_id for item in fused.ranking),
                    ranking=fused.ranking,
                    group_exact_text=True,
                    entity_ids=entity_ids,
                ),
                k=k,
                nomination_exhausted=(
                    len(semantic.ranking) >= candidate_k
                    or len(lexical.ranking) >= candidate_k
                ),
            )

        def hydrate_chunk_context() -> Envelope:
            semantic = self._nominate_testimony_chunks(
                deployment_id=deployment_id,
                query=query,
                k=candidate_k,
                channel="semantic",
                entity_ids=entity_ids,
            )
            lexical = self._nominate_testimony_chunks(
                deployment_id=deployment_id,
                query=query,
                k=candidate_k,
                channel="bm25",
                entity_ids=entity_ids,
            )
            fused = self.fuse(
                rankings=(
                    tuple(item.item_id for item in semantic.ranking),
                    tuple(item.item_id for item in lexical.ranking),
                ),
                k=DEFAULT_RRF_K,
            )
            return _bound_testimony_result(
                envelope=self.hydrate_chunks(
                    deployment_id=deployment_id,
                    chunk_ids=tuple(item.item_id for item in fused.ranking),
                    ranking=fused.ranking,
                    entity_ids=entity_ids,
                ),
                k=k,
                nomination_exhausted=(
                    len(semantic.ranking) >= candidate_k
                    or len(lexical.ranking) >= candidate_k
                ),
            )

        claim_context = hydrate_claim_context()
        chunk_context = hydrate_chunk_context()
        return self.combine_evidence(inputs=(claim_context, chunk_context))

    def _nominate_testimony_claims(
        self,
        *,
        deployment_id: UUID,
        query: str,
        k: int,
        channel: Literal["semantic", "bm25"],
        entity_ids: tuple[UUID, ...],
    ) -> Envelope:
        """Nominate claims globally or through one normalized entity join."""
        if not entity_ids:
            return self.nominate_claims(
                deployment_id=deployment_id, query=query, k=k, channel=channel
            )
        nomination_method = getattr(
            self._search_index, "nominate_testimony_scored", None
        )
        if callable(nomination_method):
            nominations = cast(
                "tuple[P1Nomination, ...]",
                nomination_method(
                    deployment_id=str(deployment_id),
                    grain="claim",
                    channel=channel,
                    k=k,
                    entity_ids=tuple(str(item) for item in entity_ids),
                    **(
                        {
                            "vector": self._embed(
                                query=query,
                                call_site=SurfaceCallSite.TESTIMONY_CLAIMS,
                                deployment_id=deployment_id,
                            )
                        }
                        if channel == "semantic"
                        else {"query": query}
                    ),
                ),
            )
            return _scored_nomination_envelope(
                nominations=nominations,
                empty_explanation="no claims were nominated inside the entity scope",
            )
        method_name = (
            "search_claims_scored"
            if channel == "semantic"
            else "search_claims_lexical_scored"
        )
        method = getattr(self._search_index, method_name, None)
        if not callable(method):
            raise RuntimeError("entity-scoped testimony requires scored P1 search")
        nominations = cast(
            "tuple[P1Nomination, ...]",
            method(
                deployment_id=str(deployment_id),
                **(
                    {
                        "vector": self._embed(
                            query=query,
                            call_site=SurfaceCallSite.TESTIMONY_CLAIMS,
                            deployment_id=deployment_id,
                        )
                    }
                    if channel == "semantic"
                    else {"query": query}
                ),
                k=k,
                current_only=True,
                entity_ids=tuple(str(item) for item in entity_ids),
            ),
        )
        return _scored_nomination_envelope(
            nominations=nominations,
            empty_explanation="no claims were nominated inside the entity scope",
        )

    def _nominate_testimony_chunks(
        self,
        *,
        deployment_id: UUID,
        query: str,
        k: int,
        channel: Literal["semantic", "bm25"],
        entity_ids: tuple[UUID, ...],
    ) -> Envelope:
        """Nominate passages globally or through one normalized entity join."""
        if not entity_ids:
            return self.nominate_chunks(
                deployment_id=deployment_id, query=query, k=k, channel=channel
            )
        nomination_method = getattr(
            self._search_index, "nominate_testimony_scored", None
        )
        if callable(nomination_method):
            nominations = cast(
                "tuple[P1Nomination, ...]",
                nomination_method(
                    deployment_id=str(deployment_id),
                    grain="chunk",
                    channel=channel,
                    k=k,
                    entity_ids=tuple(str(item) for item in entity_ids),
                    policy_generation=self._policy_generation,
                    embedder_generation=self._embedder_generation,
                    **(
                        {
                            "vector": self._embed(
                                query=query,
                                call_site=SurfaceCallSite.TESTIMONY_CHUNKS,
                                deployment_id=deployment_id,
                            )
                        }
                        if channel == "semantic"
                        else {"query": query}
                    ),
                ),
            )
            return _scored_nomination_envelope(
                nominations=nominations,
                empty_explanation="no passages were nominated inside the entity scope",
            )
        method_name = (
            "search_chunks_scored"
            if channel == "semantic"
            else "search_chunks_lexical_scored"
        )
        method = getattr(self._search_index, method_name, None)
        if not callable(method):
            raise RuntimeError("entity-scoped testimony requires scored P1 search")
        nominations = cast(
            "tuple[P1Nomination, ...]",
            method(
                deployment_id=str(deployment_id),
                **(
                    {
                        "vector": self._embed(
                            query=query,
                            call_site=SurfaceCallSite.TESTIMONY_CHUNKS,
                            deployment_id=deployment_id,
                        )
                    }
                    if channel == "semantic"
                    else {"query": query}
                ),
                k=k,
                policy_generation=self._policy_generation,
                embedder_generation=self._embedder_generation,
                entity_ids=tuple(str(item) for item in entity_ids),
            ),
        )
        return _scored_nomination_envelope(
            nominations=nominations,
            empty_explanation="no passages were nominated inside the entity scope",
        )

    def _nominate_fact_context(
        self,
        *,
        deployment_id: UUID,
        query: str,
        entity_ids: tuple[UUID, ...],
        time: FactTime,
        evaluated_at: datetime,
    ) -> tuple[P1Nomination, ...]:
        """Rank candidates, deferring final fact authority to the confirm gate."""
        nomination_method = getattr(self._search_index, "nominate_facts_scored", None)
        if callable(nomination_method):
            return cast(
                "tuple[P1Nomination, ...]",
                nomination_method(
                    deployment_id=str(deployment_id),
                    vector=self._embed(
                        query=query,
                        call_site=SurfaceCallSite.FACT_CONTEXT,
                        deployment_id=deployment_id,
                    ),
                    k=FACT_CONTEXT_CANDIDATE_K + 1,
                    kind=None,
                    time=time,
                    evaluated_at=evaluated_at,
                    entity_ids=tuple(str(item) for item in entity_ids),
                ),
            )
        method = getattr(self._search_index, "search_facts_scored", None)
        if callable(method):
            return cast(
                "tuple[P1Nomination, ...]",
                method(
                    deployment_id=str(deployment_id),
                    vector=self._embed(
                        query=query,
                        call_site=SurfaceCallSite.FACT_CONTEXT,
                        deployment_id=deployment_id,
                    ),
                    k=FACT_CONTEXT_CANDIDATE_K + 1,
                    kind=None,
                    time=time,
                    evaluated_at=evaluated_at,
                    entity_ids=tuple(str(item) for item in entity_ids),
                ),
            )
        raise RuntimeError("fact_context requires scored, time-filtered P1 search")

    def _resolve_context_entity(
        self, *, deployment_id: UUID, entity: str, grain: Grain
    ) -> tuple[UUID | None, Envelope | None]:
        """Apply principle 9 to one string entity parameter.

        The T0 ladder may return no candidate, exactly one, or an ambiguity.
        Context retrieval never silently takes the first ambiguity: candidates remain in
        ``entities[]`` and the negative names the boundary.
        """
        resolved = self.resolve(deployment_id=deployment_id, name=entity)
        if not resolved.entities:
            return None, _envelope(
                grain=grain, freshness=_freshness(), negative=resolved.negative
            )
        if len(resolved.entities) > 1:
            names = ", ".join(
                f"{candidate.canonical_name} ({candidate.entity_id})"
                for candidate in resolved.entities
            )
            return None, _envelope(
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
        call_site: SurfaceCallSite,
    ) -> tuple[tuple[UUID, ...], tuple[RankedItem, ...]]:
        """Optionally semantic-rank only a Postgres-bounded claim-id set."""
        if query is None:
            return claim_ids[:k], ()
        if self._claim_vector_index is None:
            raise RuntimeError(
                "bounded semantic claim reranking requires ClaimVectorLookupPort"
            )
        query_vector = self._embed(
            query=query, call_site=call_site, deployment_id=deployment_id
        )
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
        return _envelope(
            grain=Grain.EVIDENCE,
            freshness=_freshness(),
            negative=Negative(
                kind=NegativeKind.BOUNDARY,
                explanation=explanation,
                workaround=workaround,
            ),
        )

    def _enrich_facts(
        self,
        *,
        deployment_id: UUID,
        facts: tuple[FactResult, ...],
        kind: str,
        support_by_fact: dict[UUID, str] | None = None,
        connection: Connection | None = None,
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
        if connection is None:
            with self._engine.connect() as own_connection:
                return self._enrich_facts(
                    connection=own_connection,
                    deployment_id=deployment_id,
                    facts=facts,
                    kind=kind,
                    support_by_fact=support_by_fact,
                )
        groups = [
            fact.contradiction_group
            for fact in facts
            if fact.contradiction_group is not None
        ]
        members_by_group: dict[UUID, list[dict[str, object]]] = {}
        withdrawn = {
            fact_id
            for fact_id, support_state in (support_by_fact or {}).items()
            if support_state == "withdrawn"
        }
        if groups:
            member_rows = [
                dict(row)
                for row in connection.execute(
                    _CONTRADICTION_MEMBERS[kind],
                    {"deployment_id": deployment_id, "groups": groups},
                )
                .mappings()
                .all()
            ]
            missing_label_ids = [
                row["fact_id"] for row in member_rows if row["label"] is None
            ]
            if missing_label_ids:
                labels_by_fact = {
                    row["fact_id"]: row["label"]
                    for row in connection.execute(
                        _CURRENT_FACT_LABELS,
                        {
                            "deployment_id": deployment_id,
                            "fact_kind": kind,
                            "fact_ids": missing_label_ids,
                        },
                    )
                    .mappings()
                    .all()
                }
                for row in member_rows:
                    if row["label"] is None:
                        row["label"] = labels_by_fact.get(row["fact_id"])
                    if row["label"] is None:
                        raise RuntimeError(
                            "current contradiction member has no authoritative label"
                        )
            for row in member_rows:
                members_by_group.setdefault(row["contradiction_group"], []).append(row)
        if support_by_fact is None:
            withdrawn = {
                row["fact_id"]
                for row in connection.execute(
                    _OPEN_SUPPORT_FLAGS,
                    {
                        "deployment_id": deployment_id,
                        "fact_kind": kind,
                        "fact_ids": [str(fact.fact_id) for fact in facts],
                    },
                )
                .mappings()
                .all()
            }
        return tuple(
            self._enrich_one(
                fact=fact,
                members=(
                    members_by_group.get(fact.contradiction_group, [])
                    if fact.contradiction_group is not None
                    else []
                ),
                withdrawn=withdrawn,
            )
            for fact in facts
        )

    def _enrich_fact_context_facts(
        self,
        *,
        connection: Connection,
        deployment_id: UUID,
        rows: tuple[RowMapping, ...],
        time: FactTime,
        evaluated_at: datetime,
        deadline: float,
    ) -> tuple[FactResult, ...]:
        """Build and enrich a mixed fact nomination under the selected scope."""
        if not rows:
            return ()
        by_kind: dict[str, tuple[FactResult, ...]] = {
            kind: tuple(
                _fact_result(row=row, kind=kind) for row in rows if row["kind"] == kind
            )
            for kind in ("relation", "observation")
        }
        support_by_kind = {
            kind: {
                row["fact_id"]: str(row["support_state"])
                for row in rows
                if row["kind"] == kind
            }
            for kind in by_kind
        }
        groups = tuple(
            dict.fromkeys(
                fact.contradiction_group
                for facts in by_kind.values()
                for fact in facts
                if fact.contradiction_group is not None
            )
        )
        members_by_kind_group: dict[tuple[str, UUID], list[dict[str, object]]] = {}
        if groups:
            _configure_fact_context_connection(connection=connection, deadline=deadline)
            params = _fact_time_parameters(time=time, evaluated_at=evaluated_at)
            member_rows = connection.execute(
                _FACT_CONTEXT_CONTRADICTION_MEMBERS,
                {"deployment_id": deployment_id, "groups": list(groups), **params},
            ).mappings()
            for row in member_rows:
                key = (str(row["kind"]), row["contradiction_group"])
                members_by_kind_group.setdefault(key, []).append(dict(row))
        enriched = {
            (kind, fact.fact_id): self._enrich_one(
                fact=fact,
                members=(
                    members_by_kind_group.get((kind, fact.contradiction_group), [])
                    if fact.contradiction_group is not None
                    else []
                ),
                withdrawn={
                    fact_id
                    for fact_id, support in support_by_kind[kind].items()
                    if support == "withdrawn"
                },
            )
            for kind, facts in by_kind.items()
            for fact in facts
        }
        return tuple(enriched[(str(row["kind"]), row["fact_id"])] for row in rows)

    def _enrich_one(
        self,
        *,
        fact: FactResult,
        members: list[dict[str, object]],
        withdrawn: set[UUID],
    ) -> FactResult:
        """One fact, with its contradiction block and support marker resolved."""
        update: dict[str, object] = {}
        if fact.fact_id in withdrawn:
            update["support"] = FactSupport.WITHDRAWN
        if fact.contradiction_group is not None:
            others = [member for member in members if member["fact_id"] != fact.fact_id]
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
        entity_ids: tuple[UUID, ...] = (),
    ) -> tuple[tuple[EvidenceResult, ...], int, dict[UUID, int]]:
        """Confirm claim content and any entity scope in one PostgreSQL read."""
        if not claim_ids:
            return (), 0, {}
        if entity_ids and not current_only:
            raise ValueError("entity-scoped historical claim hydration is unsupported")
        rows: list[RowMapping] = []
        # Multiple chunks are one answer, so they must observe one database
        # snapshot rather than mixing currency states across round trips.
        with self._engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as connection:
            for batch in batched(claim_ids, INTERACTIVE_HYDRATION_BATCH_SIZE):
                rows.extend(
                    connection.execute(
                        (
                            _CONFIRM_CLAIMS_CURRENT_SCOPED
                            if entity_ids
                            else _CONFIRM_CLAIMS_CURRENT
                            if current_only
                            else _CONFIRM_CLAIMS_HISTORY
                        ),
                        {
                            "deployment_id": deployment_id,
                            "claim_ids": list(batch),
                            "entity_ids": list(entity_ids),
                        },
                    )
                    .mappings()
                    .all()
                )
        confirmed = {row["claim_id"]: row for row in rows}
        coverage = {
            claim_id: int(row.get("coverage") or 0)
            for claim_id, row in confirmed.items()
        }
        results = tuple(
            EvidenceResult.model_validate(
                {
                    key: value
                    for key, value in dict(confirmed[claim_id]).items()
                    if key != "coverage"
                }
            )
            for claim_id in claim_ids
            if claim_id in confirmed
        )
        return results, len(claim_ids) - len(results), coverage

    def _confirm_chunks(
        self,
        *,
        deployment_id: UUID,
        chunk_ids: tuple[UUID, ...],
        entity_ids: tuple[UUID, ...] = (),
    ) -> tuple[tuple[ChunkEvidenceResult, ...], int, dict[UUID, int]]:
        """Confirm chunk content and any entity scope, then hydrate P1 bodies."""
        if not chunk_ids:
            return (), 0, {}
        rows: list[RowMapping] = []
        with self._engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as connection:
            for batch in batched(chunk_ids, INTERACTIVE_HYDRATION_BATCH_SIZE):
                rows.extend(
                    connection.execute(
                        _CONFIRM_CHUNKS_SCOPED if entity_ids else _CONFIRM_CHUNKS,
                        {
                            "deployment_id": deployment_id,
                            "chunk_ids": list(batch),
                            "entity_ids": list(entity_ids),
                        },
                    )
                    .mappings()
                    .all()
                )
        confirmed = {row["chunk_id"]: row for row in rows}
        coverage = {
            chunk_id: int(row.get("coverage") or 0)
            for chunk_id, row in confirmed.items()
        }
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
            location_header = row.get("location_header") or row.get("context_prefix")
            chunk_text = projected.indexed_text
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
        return tuple(results), len(chunk_ids) - len(results), coverage

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

    def _embed(
        self, *, query: str, call_site: SurfaceCallSite, deployment_id: UUID
    ) -> tuple[float, ...]:
        """One query-string embedding through the configured port (D63)."""
        try:
            response = self._model_provider.embed(
                request=EmbeddingRequest(
                    model=self._embedding_model,
                    texts=(query,),
                    dimensions=P1_VECTOR_DIMENSIONS,
                )
            )
        except ProviderCallError as error:
            if error.usage is not None and self._surface_cost is not None:
                self._surface_cost.record(
                    usage=error.usage,
                    outcome=SurfaceCostOutcome.PROVIDER_ERROR,
                    call_site=call_site,
                    deployment_id=deployment_id,
                )
            raise
        if self._surface_cost is not None:
            self._surface_cost.record(
                usage=response.usage,
                outcome=SurfaceCostOutcome.OK,
                call_site=call_site,
                deployment_id=deployment_id,
            )
        return response.vectors[0]


def _validate_nomination_request(*, k: int, channel: str) -> None:
    """Reject unbounded or misspelled projection-search requests."""
    if not 1 <= k <= 400:
        raise ValueError("nomination k must be between 1 and 400")
    if channel not in {"semantic", "bm25"}:
        raise ValueError(
            f"unknown retrieval channel {channel!r}; use 'semantic' or 'bm25'"
        )


def _validate_batch_b_k(*, k: int) -> None:
    """Enforce the bounded legacy query helpers' shared result limit."""
    if not 1 <= k <= 50:
        raise ValueError("k must be between 1 and 50")


def _validate_fact_context_bounds(*, k: int, evidence_per_fact: int) -> None:
    """Enforce the fact-context result and per-stance evidence bounds."""
    if not 1 <= k <= 30:
        raise ValueError("fact_context k must be between 1 and 30")
    if not 1 <= evidence_per_fact <= 5:
        raise ValueError("evidence_per_fact must be between 1 and 5")


def _fact_context_confirmation_batch_size(*, k: int) -> int:
    """Confirm enough rows for k plus truncation without expanding all 30."""
    return min(
        FACT_CONTEXT_CONFIRMATION_BATCH_SIZE,
        max(FACT_CONTEXT_CONFIRMATION_MIN_BATCH_SIZE, k + 1),
    )


def _validate_testimony_context_bounds(*, k: int, candidate_k: int) -> None:
    """Enforce testimony final-list and per-channel nomination bounds."""
    if not 1 <= k <= 100:
        raise ValueError("testimony_context k must be between 1 and 100")
    if not 1 <= candidate_k <= 400:
        raise ValueError("testimony_context candidate_k must be between 1 and 400")
    if candidate_k < k:
        raise ValueError("testimony_context candidate_k cannot be smaller than k")


def _bound_testimony_result(
    *, envelope: Envelope, k: int, nomination_exhausted: bool
) -> Envelope:
    """Apply one testimony list's public cap with an explicit D49 marker."""
    if envelope.evidence and envelope.chunks:
        raise ValueError("one testimony result cannot mix claims and chunks")
    records = envelope.evidence or envelope.chunks
    selected = records[:k]
    selected_ids = {
        record.claim_id if isinstance(record, EvidenceResult) else record.chunk_id
        for record in selected
    }
    return envelope.model_copy(
        update={
            "evidence": selected if envelope.evidence else (),
            "chunks": selected if envelope.chunks else (),
            "ranking": tuple(
                item for item in envelope.ranking if item.item_id in selected_ids
            ),
            "truncation": Truncation(
                truncated=len(records) > k or nomination_exhausted,
                returned=len(selected),
                estimated_total=len(records),
                total_is_exact=not nomination_exhausted,
            ),
        }
    )


def _validate_context_entity_ids(*, entity_ids: tuple[UUID, ...]) -> tuple[UUID, ...]:
    """Validate the optional closed survivor-anchor list before retrieval."""
    if len(entity_ids) > CONTEXT_ENTITY_LIMIT:
        raise ValueError(f"entity_ids accepts at most {CONTEXT_ENTITY_LIMIT} UUIDs")
    if len(set(entity_ids)) != len(entity_ids):
        raise ValueError("entity_ids must contain unique UUIDs")
    return entity_ids


def _context_entities_are_current(
    *, connection: Connection, deployment_id: UUID, entity_ids: tuple[UUID, ...]
) -> bool:
    """Confirm every supplied ID as a current survivor in this deployment."""
    confirmed = connection.execute(
        _CONFIRM_CONTEXT_ENTITIES,
        {"deployment_id": deployment_id, "entity_ids": list(entity_ids)},
    ).mappings()
    return {row["entity_id"] for row in confirmed} == set(entity_ids)


def _fact_time_parameters(
    *, time: FactTime, evaluated_at: datetime
) -> dict[str, object]:
    """Render the discriminated time selector into fixed SQL parameters."""
    return {
        "time_mode": time.mode,
        "evaluated_at": evaluated_at,
        "at": time.at if isinstance(time, AtFactTime) else None,
        "from": time.from_ if isinstance(time, OverlapFactTime) else None,
        "to": time.to if isinstance(time, OverlapFactTime) else None,
    }


def _confirm_fact_context(
    *,
    connection: Connection,
    deployment_id: UUID,
    candidate_keys: tuple[tuple[str, UUID], ...],
    time: FactTime,
    evaluated_at: datetime,
    entity_ids: tuple[UUID, ...],
    deadline: float,
) -> tuple[RowMapping, ...]:
    """Re-confirm exact nominated identities, time, and anchors by fact kind."""
    if not candidate_keys:
        return ()
    nomination_rank = {key: rank for rank, key in enumerate(candidate_keys, start=1)}
    confirmed: list[RowMapping] = []
    for fact_kind in ("relation", "observation"):
        fact_ids = [fact_id for kind, fact_id in candidate_keys if kind == fact_kind]
        if not fact_ids:
            continue
        _configure_fact_context_connection(connection=connection, deadline=deadline)
        rows = connection.execute(
            _CONFIRM_FACT_CONTEXT_BY_KIND[fact_kind],
            {
                "deployment_id": deployment_id,
                "fact_ids": fact_ids,
                "entity_ids": list(entity_ids),
                **_fact_time_parameters(time=time, evaluated_at=evaluated_at),
            },
        ).mappings()
        confirmed.extend(rows)
    return tuple(
        sorted(
            confirmed,
            key=lambda row: (
                -int(row["coverage"]),
                nomination_rank[(str(row["kind"]), row["fact_id"])],
                str(row["kind"]),
                str(row["fact_id"]),
            ),
        )
    )


def _fact_context_evidence(
    *,
    connection: Connection,
    deployment_id: UUID,
    fact_rows: tuple[RowMapping, ...],
    evidence_per_fact: int,
    deadline: float,
) -> Sequence[RowMapping]:
    """Read representative D54 evidence within the shared operation budget."""
    _configure_fact_context_connection(connection=connection, deadline=deadline)
    return (
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
    )


def _configure_fact_context_connection(
    *, connection: Connection, deadline: float, now: float | None = None
) -> None:
    """Apply planner controls and the remaining whole-operation time budget."""
    remaining = deadline - (monotonic() if now is None else now)
    if remaining <= 0:
        raise TimeoutError("fact_context exhausted its PostgreSQL operation budget")
    timeout_ms = max(1, math.floor(remaining * 1_000))
    connection.exec_driver_sql(
        f"SET LOCAL statement_timeout = '{timeout_ms}ms'"  # noqa: S608
    )
    connection.exec_driver_sql("SET LOCAL jit = off")
    connection.exec_driver_sql("SET LOCAL join_collapse_limit = 1")
    connection.exec_driver_sql("SET LOCAL from_collapse_limit = 1")
    connection.exec_driver_sql("SET LOCAL max_parallel_workers_per_gather = 0")
    connection.exec_driver_sql("SET LOCAL enable_nestloop = off")


def _fact_temporal_scope(*, time: FactTime, evaluated_at: datetime):
    """Build the exact D87 temporal-scope variant for one fact response."""
    if isinstance(time, CurrentFactTime):
        return CurrentTemporalScope(evaluated_at=evaluated_at, believed_at=evaluated_at)
    if isinstance(time, AtFactTime):
        return AtTemporalScope(
            at=time.at, evaluated_at=evaluated_at, believed_at=evaluated_at
        )
    if isinstance(time, OverlapFactTime):
        return OverlapTemporalScope.model_validate(
            {
                "from": time.from_,
                "to": time.to,
                "evaluated_at": evaluated_at,
                "believed_at": evaluated_at,
            }
        )
    return HistoryTemporalScope(evaluated_at=evaluated_at, believed_at=evaluated_at)


def _unknown_context_entity(
    *, grain: Grain, evaluated_at: datetime, temporal_scope: object | None = None
) -> Envelope:
    """Return the same opaque negative for absent, retired, forgotten, or foreign IDs."""
    return _envelope(
        grain=grain,
        temporal_scope=temporal_scope
        or current_temporal_scope(evaluated_at=evaluated_at),
        freshness=_freshness(at=evaluated_at),
        negative=Negative(
            kind=NegativeKind.UNKNOWN_ENTITY,
            explanation="one or more entity IDs are not current survivor identities",
            workaround="call resolve_entity and retry with returned entity IDs",
        ),
    )


def _scored_nomination_envelope(
    *, nominations: tuple[P1Nomination, ...], empty_explanation: str
) -> Envelope:
    """Convert scored P1 nominations to the engine's ranking envelope."""
    ranking = tuple(
        RankedItem(
            item_id=UUID(item.item_id),
            score=item.score,
            signals={"source_rank": float(item.rank)},
        )
        for item in nominations
    )
    return _envelope(
        grain=Grain.EVIDENCE,
        temporal_scope=current_temporal_scope(),
        ranking=ranking,
        freshness=_freshness(),
        negative=None
        if ranking
        else Negative(
            kind=NegativeKind.KNOWN_EMPTY,
            explanation=empty_explanation,
            workaround="broaden the query or remove entity anchors",
        ),
    )


def _select_fact_evidence(
    *,
    fact_keys: Sequence[tuple[str, UUID]],
    evidence_by_fact_stance: dict[tuple[str, UUID, str], list[RowMapping]],
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
        for fact_kind, fact_id in fact_keys:
            for stance in _EVIDENCE_STANCES:
                candidates = evidence_by_fact_stance.get(
                    (fact_kind, fact_id, stance), []
                )
                if rank < len(candidates):
                    selected.append(candidates[rank])
                    if len(selected) == budget:
                        return tuple(selected)
    return tuple(selected)


def _bounded_truncation(*, returned: int, total: int, k: int) -> Truncation:
    """Disclose an exact list total and whether its public k cap elided rows."""
    return Truncation(
        truncated=total > k,
        returned=returned,
        estimated_total=total,
        total_is_exact=True,
    )


def _normalize_hybrid_text(*, value: str) -> str:
    """Batch E's operation-versioned exact-text grouping normalizer.

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
    return _envelope(
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


def _envelope(**values: object) -> Envelope:
    """Build an envelope, supplying the ordinary current scope when unstated."""
    values.setdefault("temporal_scope", current_temporal_scope())
    return Envelope.model_validate(values)


def _freshness(*, at: datetime | None = None) -> Freshness:
    """The skeleton's freshness stamps: PG is live; P1 is written inline.

    The `believed_at` horizons are null (unbounded): Postgres holds full
    belief history, and under D69 the live history view keeps every relation whose
    endpoints stay emitted. A channel that grows a real finite horizon fills
    these in, and `believed_at_boundary` turns a query before it into a typed
    boundary.
    """
    return Freshness(pg_live_ts=at or datetime.now(tz=UTC))


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
        support=FactSupport(mapping.get("support_state", FactSupport.CURRENT.value)),
        validity=Validity(
            valid_from=row["valid_from"],
            valid_until=row["valid_until"],
            ingested_at=row["ingested_at"],
            invalidated_at=mapping.get("invalidated_at"),
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

_RESOLVE_T0_SQL = """
    SELECT DISTINCT entity.entity_id, entity.canonical_name
    FROM memory_v1.entity_aliases_current AS alias
    JOIN memory_v1.entities_current AS entity
      ON entity.deployment_id = alias.deployment_id
     AND entity.entity_id = alias.entity_id
    WHERE alias.deployment_id = :deployment_id
      AND alias.normalized_lemma = :lemma
    """

_RESOLVE_T0 = text(_RESOLVE_T0_SQL)

_CONFIRM_CONTEXT_ENTITIES = text(
    """
    SELECT entity_id, canonical_name
    FROM memory_v1.entities_current
    WHERE deployment_id = :deployment_id
      AND entity_id = ANY(:entity_ids)
    """
)

_RESOLVE_CONTEXT_HITS = text(
    """
    SELECT candidate_id, count(DISTINCT context_entity_id) AS context_hits
    FROM (
        SELECT edge.subject_entity_id AS candidate_id,
               edge.object_entity_id AS context_entity_id
        FROM memory_v1.graph_edges_current AS edge
        WHERE deployment_id = :deployment_id
          AND edge.subject_entity_id = ANY(:candidate_ids)
          AND edge.object_entity_id = ANY(:context_entity_ids)
        UNION ALL
        SELECT edge.object_entity_id AS candidate_id,
               edge.subject_entity_id AS context_entity_id
        FROM memory_v1.graph_edges_current AS edge
        WHERE deployment_id = :deployment_id
          AND edge.object_entity_id = ANY(:candidate_ids)
          AND edge.subject_entity_id = ANY(:context_entity_ids)
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

_FACT_CONTEXT_TIME_PREDICATE = """
      AND fact.ingested_at <= :evaluated_at
      AND fact.invalidated_at IS NULL
      AND (
        (:time_mode = 'current'
          AND (fact.valid_from IS NULL OR fact.valid_from <= :evaluated_at)
          AND (fact.valid_until IS NULL OR fact.valid_until > :evaluated_at))
        OR (:time_mode = 'at'
          AND (fact.valid_from IS NULL OR fact.valid_from <= CAST(:at AS timestamptz))
          AND (fact.valid_until IS NULL OR fact.valid_until > CAST(:at AS timestamptz)))
        OR (:time_mode = 'overlap'
          AND (fact.valid_from IS NULL OR fact.valid_from <= CAST(:to AS timestamptz))
          AND (fact.valid_until IS NULL OR fact.valid_until > CAST(:from AS timestamptz)))
        OR (:time_mode = 'history'
          AND (fact.valid_from IS NULL OR fact.valid_from <= :evaluated_at))
      )
"""

_FACT_CONTEXT_ENTITY_PREDICATE = """
      AND (
        cardinality(CAST(:entity_ids AS uuid[])) = 0
        OR fact.subject_entity_id = ANY(CAST(:entity_ids AS uuid[]))
        OR fact.object_entity_id = ANY(CAST(:entity_ids AS uuid[]))
      )
"""

_FACT_CONTEXT_COVERAGE = """
      (SELECT count(DISTINCT anchor)::integer
       FROM unnest(CAST(:entity_ids AS uuid[])) AS requested(anchor)
       WHERE requested.anchor = fact.subject_entity_id
          OR requested.anchor = fact.object_entity_id)
"""


def _confirm_fact_context_statement(
    *, fact_kind: Literal["relation", "observation"]
) -> TextClause:
    """Build one fixed-kind public-authority confirmation statement."""
    return text(
        f"""
    WITH requested AS MATERIALIZED (
        SELECT fact_id, nomination_rank
        FROM unnest(CAST(:fact_ids AS uuid[]))
             WITH ORDINALITY AS nominated(fact_id, nomination_rank)
    )
    SELECT requested.nomination_rank, '{fact_kind}'::text AS kind, fact.fact_id,
           coalesce(fact.fact_label, fact.statement, fact.predicate) AS label,
           fact.evidence_count_current AS evidence_count,
           fact.valid_from, fact.valid_until, fact.ingested_at,
           fact.invalidated_at, fact.contradiction_group,
           fact.support_state_current AS support_state,
           {_FACT_CONTEXT_COVERAGE} AS coverage
    FROM requested
    JOIN memory_v1.facts_visible_history AS fact
      ON fact.deployment_id = :deployment_id
     AND fact.fact_kind = '{fact_kind}'
     AND fact.fact_id = requested.fact_id
    WHERE fact.fact_id = ANY(CAST(:fact_ids AS uuid[]))
      {_FACT_CONTEXT_TIME_PREDICATE} {_FACT_CONTEXT_ENTITY_PREDICATE}
    ORDER BY coverage DESC, requested.nomination_rank, kind, fact.fact_id
    """  # noqa: S608 -- interpolated fragments are module constants
    )


_CONFIRM_FACT_CONTEXT_BY_KIND: dict[Literal["relation", "observation"], TextClause] = {
    fact_kind: _confirm_fact_context_statement(fact_kind=fact_kind)
    for fact_kind in ("relation", "observation")
}

_FACT_CONTEXT_CONTRADICTION_MEMBERS = text(
    f"""
    SELECT fact.fact_kind AS kind, fact.contradiction_group, fact.fact_id,
           coalesce(fact.fact_label, fact.statement, fact.predicate) AS label,
           fact.evidence_count_current AS evidence_count,
           fact.valid_from, fact.valid_until, fact.ingested_at,
           fact.invalidated_at, fact.support_state_current AS support_state
    FROM memory_v1.facts_visible_history AS fact
    WHERE fact.deployment_id = :deployment_id
      AND fact.contradiction_group = ANY(CAST(:groups AS uuid[]))
      {_FACT_CONTEXT_TIME_PREDICATE}
    ORDER BY fact.contradiction_group, fact.ingested_at, fact.fact_kind, fact.fact_id
    """  # noqa: S608 -- interpolated fragment is a module constant
)

_CURRENT_FACT_EVIDENCE = text(
    """
    WITH requested AS (
        SELECT fact_id, kind, nomination_rank
        FROM unnest(
            CAST(:fact_ids AS uuid[]), CAST(:fact_kinds AS text[])
        ) WITH ORDINALITY AS confirmed(fact_id, kind, nomination_rank)
    ), representative AS MATERIALIZED (
        SELECT requested.fact_id, requested.kind,
               requested.nomination_rank, lineage.stance,
               count(*) OVER (
                   PARTITION BY requested.kind, requested.fact_id, lineage.stance
               )::bigint AS evidence_total,
               row_number() OVER (
                   PARTITION BY requested.kind, requested.fact_id, lineage.stance
                   ORDER BY lineage.asserted_to DESC NULLS LAST,
                            lineage.doc_id, lineage.representative_claim_id
               ) AS stance_rank,
               claim.claim_id, claim.doc_id, claim.chunk_id, claim.claim_text,
               claim.source_span, claim.char_start, claim.char_end,
               claim.is_attributed, true AS is_current_testimony,
               claim.asserted_at, claim.claim_valid_from,
               claim.claim_valid_until,
               claim.claim_valid_precision::text AS claim_valid_precision,
               claim.claim_valid_kind::text AS claim_valid_kind,
               claim.ingested_at AS evidence_ingested_at,
               document.title AS document_title, document.source_kind
        FROM requested
        -- Confirmation already proved these fact identities through
        -- memory_v1.facts_visible_history in this REPEATABLE READ snapshot.
        -- Hydrate their D54 lineage from the private authority helper so this
        -- step does not expand the complete fact-visibility tree a second time.
        JOIN v_memory_evidence_lineage_live AS lineage
          ON lineage.deployment_id = :deployment_id
         AND lineage.fact_kind = requested.kind
         AND lineage.fact_id = requested.fact_id
        JOIN memory_v1.claims_live AS claim
          ON claim.deployment_id = lineage.deployment_id
         AND claim.claim_id = lineage.representative_claim_id
         AND claim.doc_id = lineage.doc_id
        JOIN memory_v1.documents_live AS document
          ON document.deployment_id = claim.deployment_id
         AND document.doc_id = claim.doc_id
    )
    SELECT fact_id, kind, stance, evidence_total, stance_rank,
           claim_id, doc_id, chunk_id, claim_text, source_span,
           char_start, char_end, is_attributed, is_current_testimony,
           asserted_at, claim_valid_from, claim_valid_until,
           claim_valid_precision, claim_valid_kind, document_title, source_kind
    FROM representative
    WHERE stance_rank <= :per_stance_limit
    ORDER BY nomination_rank,
             CASE stance WHEN 'supports' THEN 0 ELSE 1 END,
             stance_rank, claim_id
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

_CONFIRM_CLAIMS_CURRENT = text(
    """
    SELECT c.claim_id, c.doc_id, c.chunk_id, c.claim_text, c.source_span,
           c.char_start, c.char_end, c.is_attributed,
           TRUE AS is_current_testimony,
           c.asserted_at, c.claim_valid_from, c.claim_valid_until,
           c.claim_valid_precision, c.claim_valid_kind,
           d.title AS document_title, d.source_kind
    FROM memory_v1.claims_live c
    JOIN memory_v1.documents_live d
      ON d.deployment_id = c.deployment_id AND d.doc_id = c.doc_id
    WHERE c.deployment_id = :deployment_id
      AND c.claim_id = ANY(:claim_ids)
    """
)

_CONFIRM_CLAIMS_CURRENT_SCOPED = text(
    """
    SELECT c.claim_id, c.doc_id, c.chunk_id, c.claim_text, c.source_span,
           c.char_start, c.char_end, c.is_attributed,
           TRUE AS is_current_testimony,
           c.asserted_at, c.claim_valid_from, c.claim_valid_until,
           c.claim_valid_precision, c.claim_valid_kind,
           d.title AS document_title, d.source_kind, scope.coverage
    FROM memory_v1.claims_live c
    JOIN memory_v1.documents_live d
      ON d.deployment_id = c.deployment_id AND d.doc_id = c.doc_id
    JOIN LATERAL (
        SELECT count(DISTINCT mention.resolved_entity_id)::integer AS coverage
        FROM memory_v1.mentions_live AS mention
        WHERE mention.deployment_id = c.deployment_id
          AND mention.claim_id = c.claim_id
          AND mention.resolved_entity_id = ANY(CAST(:entity_ids AS uuid[]))
    ) AS scope ON scope.coverage > 0
    WHERE c.deployment_id = :deployment_id
      AND c.claim_id = ANY(:claim_ids)
    """
)

_CONFIRM_CLAIMS_HISTORY = text(
    """
    SELECT c.claim_id, c.doc_id, c.chunk_id, c.claim_text, c.source_span,
           c.char_start, c.char_end, c.is_attributed, c.is_current_testimony,
           c.asserted_at, c.claim_valid_from, c.claim_valid_until,
           c.claim_valid_precision, c.claim_valid_kind,
           d.title AS document_title, d.source_kind
    FROM memory_v1.claims_visible_history c
    JOIN memory_v1.documents_live d
      ON d.deployment_id = c.deployment_id AND d.doc_id = c.doc_id
    WHERE c.deployment_id = :deployment_id
      AND c.claim_id = ANY(:claim_ids)
    """
)

_CONFIRM_CHUNKS = text(
    """
    SELECT ch.chunk_id, ch.doc_id, ch.version_id, ch.representation_id,
           ch.char_start, ch.char_end, NULL::text AS context_prefix,
           ch.location_header,
           ch.policy_generation, ch.embedding_input_policy_version,
           s.role::text AS section_role,
           d.title AS document_title, d.source_kind,
           d.source_modified_at, d.published_at
    FROM memory_v1.chunks_live ch
    JOIN memory_v1.documents_live d
      ON d.deployment_id = ch.deployment_id AND d.doc_id = ch.doc_id
    LEFT JOIN memory_v1.sections_live s
      ON s.deployment_id = ch.deployment_id AND s.section_id = ch.section_id
    WHERE ch.deployment_id = :deployment_id
      AND ch.chunk_id = ANY(:chunk_ids)
    """
)

_CONFIRM_CHUNKS_SCOPED = text(
    """
    SELECT ch.chunk_id, ch.doc_id, ch.version_id, ch.representation_id,
           ch.char_start, ch.char_end, NULL::text AS context_prefix,
           ch.location_header,
           ch.policy_generation, ch.embedding_input_policy_version,
           s.role::text AS section_role,
           d.title AS document_title, d.source_kind,
           d.source_modified_at, d.published_at, scope.coverage
    FROM memory_v1.chunks_live ch
    JOIN memory_v1.documents_live d
      ON d.deployment_id = ch.deployment_id AND d.doc_id = ch.doc_id
    LEFT JOIN memory_v1.sections_live s
      ON s.deployment_id = ch.deployment_id AND s.section_id = ch.section_id
    JOIN LATERAL (
        SELECT count(DISTINCT mention.resolved_entity_id)::integer AS coverage
        FROM memory_v1.mentions_live AS mention
        WHERE mention.deployment_id = ch.deployment_id
          AND mention.chunk_id = ch.chunk_id
          AND mention.resolved_entity_id = ANY(CAST(:entity_ids AS uuid[]))
    ) AS scope ON scope.coverage > 0
    WHERE ch.deployment_id = :deployment_id
      AND ch.chunk_id = ANY(:chunk_ids)
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

_AGG_PREDICATE_ABSENCE = text(
    """
    -- entities with NO live relation of a predicate (S40, D96: no type filter).
    -- Each bucket IS one absent entity (count 1).
    SELECT e.canonical_name AS key, 1 AS count, e.entity_id AS entity_id
    FROM entities e
    WHERE e.deployment_id = :deployment_id AND e.status = 'active'
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
    "predicate_absence": (_AGG_PREDICATE_ABSENCE, frozenset({"predicate"})),
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
    SELECT member.contradiction_group, member.fact_id,
           member.fact_label AS label,
           member.evidence_count,
           member.valid_from, member.valid_until, member.ingested_at,
           NULL::timestamptz AS invalidated_at, member.support_state
    FROM memory_v1.contradiction_members_current AS member
    WHERE member.deployment_id = :deployment_id
      AND member.fact_kind = 'relation'
      AND member.contradiction_group = ANY(CAST(:groups AS uuid[]))
    ORDER BY member.contradiction_group, member.ingested_at, member.fact_id
    """
)

_CONTRADICTION_MEMBERS_OBSERVATIONS = text(
    """
    SELECT member.contradiction_group, member.fact_id,
           member.fact_label AS label,
           member.evidence_count,
           member.valid_from, member.valid_until, member.ingested_at,
           NULL::timestamptz AS invalidated_at, member.support_state
    FROM memory_v1.contradiction_members_current AS member
    WHERE member.deployment_id = :deployment_id
      AND member.fact_kind = 'observation'
      AND member.contradiction_group = ANY(CAST(:groups AS uuid[]))
    ORDER BY member.contradiction_group, member.ingested_at, member.fact_id
    """
)

_CONTRADICTION_MEMBERS = {
    "relation": _CONTRADICTION_MEMBERS_RELATIONS,
    "observation": _CONTRADICTION_MEMBERS_OBSERVATIONS,
}

_CURRENT_FACT_LABELS = text(
    """
    SELECT fact.fact_id,
           coalesce(fact.fact_label, fact.statement, fact.predicate) AS label
    FROM memory_v1.facts_current AS fact
    WHERE fact.deployment_id = :deployment_id
      AND fact.fact_kind = :fact_kind
      AND fact.fact_id = ANY(CAST(:fact_ids AS uuid[]))
    ORDER BY fact.fact_id
    """
)

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
      AND candidate ->> 'fact_kind' = :fact_kind
      AND (candidate ->> 'fact_id') = ANY(:fact_ids)
    """
)
