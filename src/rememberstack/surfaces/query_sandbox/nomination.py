"""The semantic and lexical bridge behind the public functions (design §3.4).

An agent writes `FROM semantic_claims($1, 20)` and gets confirmed rows. What
happens between those two facts is here: the P1 index nominates candidates by
similarity or BM25, PostgreSQL confirms every nominated id against the same
invariant views the rest of the surface reads, and only confirmed rows are
exposed. Nomination proposes; PostgreSQL disposes (D48).

**Where this runs, and why.** The design describes these as in-database
`SECURITY DEFINER` functions. PostgreSQL cannot reach the Lance projection
without an untrusted procedural language, which this product does not install,
so the bridge runs in the executor instead: the grammar has already rewritten
each accepted invocation into its own `MATERIALIZED` CTE, and the executor
replaces that CTE with the confirmed rows before the statement is planned. The
caller-visible contract is unchanged — same call syntax, same columns, same
per-invocation caps, same confirmation before exposure — and the deviation is
recorded in `plan/implementation_notes/open_query_space_batch_c.md`.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
import json
from typing import Any
from typing import Final
from uuid import UUID

import psycopg

from rememberstack.core.embedding_input_policy import embedding_text_hash
from rememberstack.ports.p1_index import P1Nomination
from rememberstack.surfaces.query_sandbox.errors import QueryErrorCode
from rememberstack.surfaces.query_sandbox.errors import SandboxRejection

# §3.4: target-specific filter allowlists. A key outside its target's list is
# rejected rather than ignored — an ignored filter silently widens a result.
FILTER_ALLOWLISTS: Final[dict[str, frozenset[str]]] = {
    "claims": frozenset(
        {"doc_id", "source_kind", "entity_id", "asserted_from", "asserted_to"}
    ),
    "chunks": frozenset(
        {"doc_id", "source_kind", "source_shape", "section_role", "language"}
    ),
    "facts": frozenset(
        {
            "fact_kind",
            "predicate",
            "subject_entity_id",
            "object_entity_id",
            "support_state",
        }
    ),
    "entities": frozenset({"entity_type"}),
}

# `source_shape` is a D80 location fact held in the projection only: Lance can
# filter on it, PostgreSQL has no column to repeat it with, and it carries no
# authorization meaning (§3.4).
PROJECTION_ONLY_FILTERS: Final = frozenset({"source_shape"})

#: The column contract each target answers with, used when a search returns
#: nothing and there is no cursor to read a shape from. Kept beside the
#: confirmation statements so the two cannot drift apart unnoticed.
EMPTY_CONTRACTS: Final[dict[str, tuple[tuple[str, ...], tuple[int, ...]]]] = {
    "claims": (
        (
            "rank",
            "score",
            "channel",
            "claim_id",
            "doc_id",
            "claim_text",
            "source_handle",
            "source_kind",
            "asserted_at",
            "claim_valid_from",
            "claim_valid_until",
        ),
        (23, 701, 25, 2950, 2950, 25, 25, 25, 1184, 1184, 1184),
    ),
    "chunks": (
        (
            "rank",
            "score",
            "channel",
            "chunk_id",
            "doc_id",
            "version_id",
            "representation_id",
            "section_id",
            "chunk_content_hash",
            "embedding_text_hash",
            # `source_text` is spliced in by the body path, which is the one
            # place that decides whether a body may be published at all; a
            # second declaration here would put the column in twice.
            "location_header",
            "embedding_input_policy_version",
            "policy_generation",
            "embedder_generation",
            "created_at",
        ),
        (23, 701, 25, 2950, 2950, 2950, 2950, 2950, 25, 25, 25, 25, 25, 25, 1184),
    ),
    "facts": (
        (
            "rank",
            "score",
            "channel",
            "fact_id",
            "fact_kind",
            "fact_label",
            "predicate",
            "subject_entity_id",
            "object_entity_id",
            "evidence_count",
            "contradict_count",
            "support_state",
            "evaluated_at",
        ),
        (23, 701, 25, 2950, 25, 25, 25, 2950, 2950, 20, 20, 25, 1184),
    ),
    "entities": (
        (
            "rank",
            "score",
            "channel",
            "entity_id",
            "entity_type",
            "canonical_name",
            "profile_summary",
            "live_mention_count",
            "live_document_count",
        ),
        (23, 701, 25, 2950, 25, 25, 25, 20, 20),
    ),
}


def published_contract(target: str) -> tuple[tuple[str, ...], tuple[int, ...]]:
    """What a caller actually receives from a channel, columns and types.

    `EMPTY_CONTRACTS` is the shape PostgreSQL confirmation produces; the chunk
    channels then splice in `source_text` from the shared body path. This is
    the composed answer, so the manifest, the EXPLAIN placeholder, and the
    runtime result cannot describe the surface differently.
    """
    columns, oids = EMPTY_CONTRACTS[target]
    if target != "chunks":
        return columns, oids
    at = columns.index("location_header")
    return ((*columns[:at], "source_text", *columns[at:]), (*oids[:at], 25, *oids[at:]))


_FACT_KINDS: Final = frozenset({"relation", "observation"})
_SUPPORT_STATES: Final = frozenset({"current", "withdrawn"})


@dataclass(frozen=True)
class NominationOutcome:
    """One invocation's confirmed rows and its honest disclosure counts."""

    function: str
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    nominated: int
    confirmed: int
    dropped_stale: int
    channel: str
    generation: str | None = None
    pg_confirmed_at: datetime | None = None
    termination_reason: str = "completed"


@dataclass
class BridgeSettings:
    """What the bridge is allowed to do for one request."""

    deployment_id: UUID
    k_default: int = 20
    k_max: int = 100
    total_nominations_max: int = 200
    chunk_ids_max: int = 50
    nominations_used: int = field(default=0, init=False)
    chunk_text_bytes_used: int = field(default=0, init=False)


def validate_filters(*, target: str, filters: object) -> dict[str, Any]:
    """Check a filter object against its target's allowlist (§3.4).

    Unknown keys, wrong shapes, and caller-authored predicates are rejected:
    the filter surface is a fixed vocabulary, not an expression language.
    """
    if filters is None:
        return {}
    if type(filters).__name__ == "_Unrepresentable":
        raise SandboxRejection(
            code=QueryErrorCode.INVALID_PARAMETER,
            message="filters must be a JSON object",
        )
    if isinstance(filters, (str, bytes)):
        # The argument arrives as JSON text from a `::jsonb` literal or a
        # bound parameter; both spell the same object.
        try:
            filters = json.loads(filters)
        except ValueError as error:
            raise SandboxRejection(
                code=QueryErrorCode.INVALID_PARAMETER,
                message="filters must be valid JSON",
            ) from error
    if not isinstance(filters, dict):
        raise SandboxRejection(
            code=QueryErrorCode.INVALID_PARAMETER,
            message="filters must be a JSON object",
        )
    allowed = FILTER_ALLOWLISTS[target]
    checked: dict[str, Any] = {}
    for key, value in filters.items():
        if key not in allowed:
            raise SandboxRejection(
                code=QueryErrorCode.INVALID_PARAMETER,
                message=f"{key} is not a filter of the {target} channel",
            )
        if isinstance(value, (dict, list, tuple)):
            raise SandboxRejection(
                code=QueryErrorCode.INVALID_PARAMETER,
                message=f"filter {key} takes a scalar value",
            )
        checked[key] = _typed_filter_value(key=key, value=value)
    if "fact_kind" in checked and checked["fact_kind"] not in _FACT_KINDS:
        raise SandboxRejection(
            code=QueryErrorCode.INVALID_PARAMETER,
            message="fact_kind is exactly relation or observation",
        )
    if "support_state" in checked and checked["support_state"] not in _SUPPORT_STATES:
        raise SandboxRejection(
            code=QueryErrorCode.INVALID_PARAMETER,
            message="support_state is exactly current or withdrawn",
        )
    return checked


#: Filter keys whose value is an identifier, an instant, or a closed
#: vocabulary. A value that fails here is the caller's mistake and is named as
#: such, rather than surfacing later as a confirmation that mysteriously
#: matched nothing.
_UUID_FILTERS: Final = frozenset(
    {"doc_id", "entity_id", "subject_entity_id", "object_entity_id"}
)
_INSTANT_FILTERS: Final = frozenset({"asserted_from", "asserted_to"})
_SECTION_ROLES: Final = frozenset(
    {
        "body",
        "abstract",
        "introduction",
        "results",
        "methods",
        "discussion",
        "conclusion",
        "references",
        "appendix",
        "table",
        "figure_caption",
        "nav",
        "boilerplate",
        "legal",
    }
)


def _typed_filter_value(*, key: str, value: object) -> Any:
    """One filter value, checked against the type its column actually holds."""
    if key in _UUID_FILTERS:
        try:
            return str(UUID(str(value)))
        except (ValueError, AttributeError, TypeError) as error:
            raise SandboxRejection(
                code=QueryErrorCode.INVALID_PARAMETER, message=f"{key} must be a UUID"
            ) from error
    if key in _INSTANT_FILTERS:
        text = str(value)
        try:
            datetime.fromisoformat(text)
        except ValueError as error:
            raise SandboxRejection(
                code=QueryErrorCode.INVALID_PARAMETER,
                message=f"{key} must be an ISO-8601 instant",
            ) from error
        return text
    if key == "section_role" and value not in _SECTION_ROLES:
        raise SandboxRejection(
            code=QueryErrorCode.INVALID_PARAMETER,
            message="section_role is not a role this deployment publishes",
        )
    if not isinstance(value, str) or not value:
        raise SandboxRejection(
            code=QueryErrorCode.INVALID_PARAMETER,
            message=f"filter {key} takes a non-empty string",
        )
    return value


#: The filters the projection itself can apply, and the column each one lives
#: in there. Applying these before top-k is what makes a filtered search return
#: k matching rows instead of k rows that mostly get thrown away afterwards.
LANCE_FILTER_COLUMNS: Final[dict[str, dict[str, str]]] = {
    "claims": {"doc_id": "doc_id"},
    "chunks": {
        "doc_id": "doc_id",
        "source_kind": "source_kind",
        "source_shape": "source_shape",
        "section_role": "section_role",
    },
    "facts": {},
    "entities": {},
}


def projection_filters(*, target: str, filters: dict[str, Any]) -> dict[str, str]:
    """The subset of `filters` the projection can apply, keyed by its column."""
    columns = LANCE_FILTER_COLUMNS[target]
    return {
        columns[key]: str(value) for key, value in filters.items() if key in columns
    }


def bounded_k(*, requested: object, settings: BridgeSettings) -> int:
    """The effective k, clamped to the tier's cap and the request's budget."""
    if requested is None:
        k = settings.k_default
    elif isinstance(requested, bool) or not isinstance(requested, int):
        raise SandboxRejection(
            code=QueryErrorCode.INVALID_PARAMETER, message="k must be an integer"
        )
    else:
        k = requested
    if k < 1:
        raise SandboxRejection(
            code=QueryErrorCode.INVALID_PARAMETER, message="k must be at least 1"
        )
    k = min(k, settings.k_max)
    remaining = settings.total_nominations_max - settings.nominations_used
    if remaining <= 0:
        raise SandboxRejection(
            code=QueryErrorCode.QUOTA_EXCEEDED,
            message="the statement's nomination budget is spent",
        )
    return min(k, remaining)


# --- PostgreSQL confirmation ------------------------------------------------

_CONFIRM_SQL: Final[dict[str, str]] = {
    "claims": (
        "SELECT claim_id::text AS id{extra}, claim_id, doc_id, claim_text, source_handle,"
        " source_kind, asserted_at, claim_valid_from, claim_valid_until"
        " FROM memory_v1.claims_live"
        " WHERE deployment_id = %(deployment)s AND claim_id = ANY(%(ids)s::uuid[])"
    ),
    "chunks": (
        "SELECT c.chunk_id::text AS id{extra}, c.chunk_id, c.doc_id, c.version_id,"
        " c.representation_id, c.section_id, c.chunk_content_hash,"
        " c.embedding_text_hash, c.location_header,"
        " c.embedding_input_policy_version, c.policy_generation,"
        " c.embedder_generation, c.created_at"
        " FROM memory_v1.chunks_live AS c"
        " JOIN memory_v1.documents_live AS d"
        "   ON d.deployment_id = c.deployment_id AND d.doc_id = c.doc_id"
        " LEFT JOIN memory_v1.sections_live AS s"
        "   ON s.deployment_id = c.deployment_id AND s.section_id = c.section_id"
        " WHERE c.deployment_id = %(deployment)s"
        "   AND c.chunk_id = ANY(%(ids)s::uuid[])"
    ),
    "facts": (
        "SELECT fact_id::text AS id{extra}, fact_id, fact_kind, fact_label, predicate,"
        " subject_entity_id, object_entity_id, evidence_count, contradict_count,"
        " support_state, evaluated_at"
        " FROM memory_v1.facts_current"
        " WHERE deployment_id = %(deployment)s AND fact_id = ANY(%(ids)s::uuid[])"
    ),
    "entities": (
        "SELECT entity_id::text AS id{extra}, entity_id, entity_type, canonical_name,"
        " profile_summary, live_mention_count, live_document_count"
        " FROM memory_v1.entities_current"
        " WHERE deployment_id = %(deployment)s AND entity_id = ANY(%(ids)s::uuid[])"
    ),
}


@dataclass(frozen=True)
class Confirmation:
    """What PostgreSQL agreed to publish, and what it withheld and why."""

    columns: tuple[str, ...]
    rows: list[tuple[Any, ...]]
    type_oids: tuple[int, ...]
    dropped_stale: int = 0
    dropped_filtered: int = 0
    dropped_ambiguous: int = 0
    pg_confirmed_at: datetime | None = None


def payload_index(columns: Sequence[str], name: str) -> int:
    """Where a column sits in a confirmation row, whatever precedes it."""
    return list(columns).index(name)


def confirm(
    *,
    connection: psycopg.Connection,
    target: str,
    nominations: Sequence[P1Nomination],
    deployment_id: UUID,
    filters: dict[str, Any],
    generations: tuple[str | None, str | None] = (None, None),
) -> Confirmation:
    """Confirm nominated ids against the invariant views; drop what fails.

    Everything the projection proposed is checked here, in the same views an
    ordinary query reads, so a row that the spine no longer publishes — a
    tombstoned lineage, a superseded fact — cannot reach a caller through the
    projection's memory of it. Rank order is preserved; ranks keep their gaps,
    because hiding a drop would misreport the channel.
    """
    if not nominations:
        # A search that nominated nothing still has a shape: the caller wrote
        # a join against these columns and it must still type-check. It also
        # still consulted PostgreSQL, so it still reports when.
        columns, oids = EMPTY_CONTRACTS[target]
        empty_at = connection.execute("SELECT statement_timestamp()").fetchone()
        return Confirmation(
            columns=columns,
            rows=[],
            type_oids=oids,
            pg_confirmed_at=empty_at[0] if empty_at else None,
        )
    ids = [nomination.item_id for nomination in nominations]
    # Facts are identified by (kind, id). Confirming an id alone would let a
    # nomination the projection still remembers as a relation be answered with
    # a current observation that happens to share the id — the caller would get
    # a real row carrying a score that was computed for a different fact.
    qualified = target == "facts"
    statement = _CONFIRM_SQL[target]
    authorization_filters = {
        key: value
        for key, value in filters.items()
        if key not in PROJECTION_ONLY_FILTERS
    }
    predicates, parameters = _filter_predicates(
        target=target, filters=authorization_filters
    )
    statement = _CONFIRM_SQL[target]
    # The filters are asked as a question, not used to hide rows: a row that
    # fails one is a filter drop, while a row the view no longer publishes at
    # all is a staleness drop, and calling both "stale" would misreport why the
    # projection's memory and PostgreSQL disagree.
    # A nomination made under one generation is confirmed under that same one.
    # During a cutover the projection can still hold a chunk under the old
    # pair while PostgreSQL has moved it to the new one; confirming on id
    # alone would return current metadata carrying a score computed in a
    # different vector space.
    if target == "chunks" and generations[0] is not None:
        statement += (
            " AND c.policy_generation = %(g_policy)s"
            " AND c.embedder_generation = %(g_embedder)s"
        )
        parameters["g_policy"] = generations[0]
        parameters["g_embedder"] = generations[1]
    extra = f", ({' AND '.join(predicates)}) AS filter_pass" if predicates else ""
    statement = statement.replace("{extra}", extra)
    confirmed_at = connection.execute("SELECT statement_timestamp()").fetchone()
    cursor = connection.execute(
        statement.encode(), {"deployment": str(deployment_id), "ids": ids, **parameters}
    )
    description = cursor.description or ()
    columns = tuple(column.name for column in description)
    type_oids = tuple(column.type_code for column in description)
    # A fact's identity is (fact_kind, fact_id), so one id can name two rows.
    # Publishing either would be a guess about which one the projection meant,
    # and a guess is exactly what confirmation exists to avoid.
    candidates: dict[str, list[tuple[Any, ...]]] = {}
    for row in cursor.fetchall():
        candidates.setdefault(str(row[0]), []).append(row)
    if qualified:
        # `fact_kind` is the first payload column of the facts statement.
        kind = payload_index(columns, "fact_kind")
        candidates = {
            f"{row[kind]}:{identifier}": [row]
            for identifier, rows in candidates.items()
            for row in rows
        }
    by_id = {
        identifier: rows[0] for identifier, rows in candidates.items() if len(rows) == 1
    }
    ambiguous = {identifier for identifier, rows in candidates.items() if len(rows) > 1}

    payload = 2 if predicates else 1
    rows: list[tuple[Any, ...]] = []
    dropped_stale = 0
    dropped_filtered = 0
    dropped_ambiguous = 0
    for nomination in nominations:
        key = (
            f"{nomination.qualifier}:{nomination.item_id}"
            if qualified and nomination.qualifier
            else nomination.item_id
        )
        if key in ambiguous:
            dropped_ambiguous += 1
            continue
        confirmed = by_id.get(key)
        if confirmed is None:
            dropped_stale += 1
            continue
        if predicates and not confirmed[1]:
            dropped_filtered += 1
            continue
        rows.append(
            (
                nomination.rank,
                nomination.score,
                nomination.channel,
                *confirmed[payload:],
            )
        )
    result_columns = ("rank", "score", "channel", *columns[payload:])
    # `rank` is integer, `score` double precision, `channel` text; the rest
    # keep the types PostgreSQL just reported for them.
    result_types = (23, 701, 25, *type_oids[payload:])
    return Confirmation(
        columns=result_columns,
        rows=rows,
        type_oids=result_types,
        dropped_stale=dropped_stale,
        dropped_filtered=dropped_filtered,
        dropped_ambiguous=dropped_ambiguous,
        pg_confirmed_at=confirmed_at[0] if confirmed_at else None,
    )


#: Where each published filter actually lives, per target. A filter with no
#: column here cannot be repeated in PostgreSQL and must not be published as
#: though it could be.
_FILTER_COLUMNS: Final[dict[str, dict[str, str]]] = {
    "claims": {"doc_id": "doc_id", "source_kind": "source_kind"},
    "chunks": {
        "doc_id": "c.doc_id",
        "source_kind": "d.source_kind",
        "section_role": "s.role",
        "language": "d.language",
    },
    "facts": {
        "fact_kind": "fact_kind",
        "predicate": "predicate",
        "subject_entity_id": "subject_entity_id",
        "object_entity_id": "object_entity_id",
        "support_state": "support_state",
    },
    "entities": {"entity_type": "entity_type"},
}


def _filter_predicates(
    *, target: str, filters: dict[str, Any]
) -> tuple[list[str], dict[str, Any]]:
    """Repeat every authorization-relevant filter in PostgreSQL (§3.4)."""
    predicates: list[str] = []
    parameters: dict[str, Any] = {}
    columns = _FILTER_COLUMNS[target]
    for key, value in filters.items():
        parameter = f"f_{key}"
        if target == "claims" and key == "asserted_from":
            predicates.append(f"asserted_at >= %({parameter})s")
        elif target == "claims" and key == "asserted_to":
            predicates.append(f"asserted_at <= %({parameter})s")
        elif target == "claims" and key == "entity_id":
            predicates.append(
                "EXISTS (SELECT 1 FROM memory_v1.mentions_live m"
                " WHERE m.deployment_id = memory_v1.claims_live.deployment_id"
                "   AND m.claim_id = memory_v1.claims_live.claim_id"
                f"   AND m.resolved_entity_id = %({parameter})s::uuid)"
            )

        else:
            column = columns.get(key)
            if column is None:  # pragma: no cover - the allowlist prevents it
                raise SandboxRejection(
                    code=QueryErrorCode.INVALID_PARAMETER,
                    message=f"{key} cannot be confirmed in PostgreSQL",
                )
            suffix = "::uuid" if key.endswith("_id") else ""
            predicates.append(f"{column} = %({parameter})s{suffix}")
        parameters[parameter] = value
    return predicates, parameters


# --- confirmed body fetch (§3.4) --------------------------------------------

#: More than this many ids in one call fails before any store is read.
CHUNK_IDS_MAX: Final = 50

#: §4.3 chunk source-text byte caps. Bodies are the one part of a result whose
#: size the caller does not control by asking for fewer rows — one chunk can be
#: a whole page — so they are bounded on their own, per invocation and across
#: the statement.
CHUNK_TEXT_BYTES_PER_INVOCATION: Final = 512 * 1024
CHUNK_TEXT_BYTES_PER_STATEMENT: Final = 4 * 1024 * 1024

_BODY_SQL: Final = (
    "SELECT chunk_id::text AS id, chunk_id, doc_id, version_id,"
    " representation_id, section_id, chunk_content_hash, embedding_text_hash,"
    " location_header, embedding_input_policy_version, policy_generation,"
    " embedder_generation, created_at"
    " FROM memory_v1.chunks_live"
    " WHERE deployment_id = %(deployment)s AND chunk_id = ANY(%(ids)s::uuid[])"
)

#: The shape a body fetch answers with, in the order §3.4 lists it. `source_text`
#: is the body alone; the D80 header is a separate column because generated
#: orientation text is never asserted evidence.
BODY_COLUMNS: Final = (
    "input_ordinal",
    "chunk_id",
    "doc_id",
    "version_id",
    "representation_id",
    "section_id",
    "chunk_content_hash",
    "embedding_text_hash",
    "source_text",
    "location_header",
    "embedding_input_policy_version",
    "policy_generation",
    "embedder_generation",
    "created_at",
)
BODY_TYPE_OIDS: Final = (
    23,
    2950,
    2950,
    2950,
    2950,
    2950,
    25,
    25,
    25,
    25,
    25,
    25,
    25,
    1184,
)


@dataclass(frozen=True)
class BodyConfirmation:
    """Confirmed bodies, and the count of ids each rejection category ate.

    The categories are separate because they mean different things to whoever
    reads the disclosure: a chunk PostgreSQL no longer publishes is a deletion
    or a supersession, a chunk the projection has not got is a rebuild lag, and
    text that disagrees with the recorded hash or header is a corruption or a
    stale generation — three different things to go and look at.
    """

    rows: list[tuple[Any, ...]]
    requested: int
    pg_confirmed_at: datetime | None = None
    absent_current: int = 0
    absent_projection: int = 0
    mismatch_hash: int = 0

    @property
    def absent(self) -> int:
        """Every id that produced no row for want of one side or the other."""
        return self.absent_current + self.absent_projection

    @property
    def mismatched(self) -> int:
        """Every id whose text disagreed with what PostgreSQL recorded."""
        return self.mismatch_hash


def chunk_id_list(value: object) -> tuple[tuple[int, str], ...]:
    """The `uuid[]` argument, de-duplicated to first position (§3.4).

    The argument arrives either as a bound list or as a PostgreSQL array
    literal such as `'{a,b}'::uuid[]`; both spell the same set of ids.
    """
    if isinstance(value, str):
        text = value.strip()
        if not (text.startswith("{") and text.endswith("}")):
            raise SandboxRejection(
                code=QueryErrorCode.INVALID_PARAMETER,
                message="chunk_ids must be a uuid[] value",
            )
        items: list[object] = [
            part.strip().strip('"') for part in text[1:-1].split(",") if part.strip()
        ]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        raise SandboxRejection(
            code=QueryErrorCode.INVALID_PARAMETER,
            message="chunk_ids must be a uuid[] value",
        )
    # The cap counts what was ASKED, before de-duplication: fifty-one copies of
    # one id is still a fifty-one-id request, and letting it through because it
    # collapses to one would make the bound depend on the caller's repetition.
    if len(items) > CHUNK_IDS_MAX:
        raise SandboxRejection(
            code=QueryErrorCode.INVALID_PARAMETER,
            message=f"fetch_chunk_bodies takes at most {CHUNK_IDS_MAX} chunk ids",
        )
    ordered: list[tuple[int, str]] = []
    seen: set[str] = set()
    for position, item in enumerate(items):
        try:
            identifier = str(UUID(str(item)))
        except (ValueError, AttributeError, TypeError) as error:
            raise SandboxRejection(
                code=QueryErrorCode.INVALID_PARAMETER,
                message="chunk_ids must contain only UUIDs",
            ) from error
        if identifier not in seen:
            seen.add(identifier)
            # §3.4: a duplicate collapses to the position it FIRST occupied,
            # so `input_ordinal` still points into the caller's own list.
            ordered.append((position, identifier))
    return tuple(ordered)


_CURRENT_GENERATIONS_SQL: Final = (
    "SELECT embedding_input_policy_version, policy_generation, embedder_generation"
    " FROM memory_v1.chunks_live"
    " WHERE deployment_id = %(deployment)s"
    "   AND policy_generation IS NOT NULL"
    "   AND embedder_generation IS NOT NULL"
    " ORDER BY created_at DESC"
    " LIMIT 1"
)


def current_chunk_generations(
    *, connection: psycopg.Connection, deployment_id: UUID
) -> tuple[str | None, str | None, str | None]:
    """The policy version, policy generation, and embedder generation in force.

    The spine decides which generation is current, not the projection: asking
    the projection would mean reading whichever pair happens to sort highest
    among whatever rows it still holds, including generations the spine has
    already moved past.
    """
    row = connection.execute(
        _CURRENT_GENERATIONS_SQL.encode(), {"deployment": str(deployment_id)}
    ).fetchone()
    if row is None:
        return None, None, None
    # The POLICY VERSION is what §3.4 publishes as a pin
    # (`embedding_input_policy_version`); the POLICY GENERATION is the label the
    # projection indexes that application under. They are different values of
    # the same chunk, and matching a pin against the wrong one refuses every
    # caller who used the documented name.
    return row[0], row[1], row[2]


def confirm_chunk_coordinates(
    *, connection: psycopg.Connection, chunk_ids: Sequence[str], deployment_id: UUID
) -> tuple[dict[str, tuple[Any, ...]], datetime | None]:
    """What PostgreSQL currently publishes for each requested chunk id, and when.

    This runs BEFORE any projection read. PostgreSQL decides which chunks
    exist, what their current coordinate is, which D80 header belongs to them,
    and what the embedded text hashed to; the projection is then asked only
    about ids that survived, and only to supply bytes.
    """
    if not chunk_ids:
        # Nothing to confirm, but the decision was still made at an instant,
        # and an invocation that reports none reads as one that never ran.
        empty_at = connection.execute("SELECT statement_timestamp()").fetchone()
        return {}, (empty_at[0] if empty_at else None)
    confirmed_at = connection.execute("SELECT statement_timestamp()").fetchone()
    cursor = connection.execute(
        _BODY_SQL.encode(), {"deployment": str(deployment_id), "ids": list(chunk_ids)}
    )
    confirmed = {str(row[0]): row for row in cursor.fetchall()}
    generations = {(row[10], row[11]) for row in confirmed.values()}
    if len(generations) > 1:
        raise SandboxRejection(
            code=QueryErrorCode.INVALID_PARAMETER,
            message="the requested chunks span more than one D80 generation",
        )
    return confirmed, (confirmed_at[0] if confirmed_at else None)


def verify_bodies(
    *,
    requested: Sequence[tuple[int, str]],
    coordinates: dict[str, tuple[Any, ...]],
    texts: dict[str, Any],
    budget: BridgeSettings | None = None,
    pg_confirmed_at: datetime | None = None,
) -> BodyConfirmation:
    """Admit projection bytes only where they match what the spine recorded.

    P1 stores the chunk BODY; PostgreSQL stores the D80 header separately and
    the hash of the text that was actually embedded, which the policy composes
    as header, blank line, body. Recomposing it here and hashing the result is
    what proves the two halves belong together — it verifies the separation
    rather than assuming it, and it is why `source_text` can be returned as the
    body alone with the header in its own column.
    """
    rows: list[tuple[Any, ...]] = []
    spent = 0
    absent_current = 0
    absent_projection = 0
    mismatch_hash = 0
    for ordinal, chunk_id in requested:
        confirmed = coordinates.get(chunk_id)
        if confirmed is None:
            absent_current += 1
            continue
        body = getattr(texts.get(chunk_id), "indexed_text", None)
        if not isinstance(body, str):
            absent_projection += 1
            continue
        header = confirmed[8]
        embedded = f"{header}\n\n{body}" if header else body
        expected = confirmed[7]
        # A chunk with no recorded hash cannot have its body verified, and
        # unverifiable text is not returned as though it had been checked.
        if not expected or embedding_text_hash(embedded) != expected:
            mismatch_hash += 1
            continue
        spent += len(body.encode())
        if spent > CHUNK_TEXT_BYTES_PER_INVOCATION or (
            budget is not None
            and budget.chunk_text_bytes_used + spent > CHUNK_TEXT_BYTES_PER_STATEMENT
        ):
            raise SandboxRejection(
                code=QueryErrorCode.RESOURCE_LIMIT,
                message="the request asked for more chunk text than §4.3 allows",
            )
        rows.append((ordinal, *confirmed[1:8], body, *confirmed[8:]))
    if budget is not None:
        budget.chunk_text_bytes_used += spent
    return BodyConfirmation(
        rows=rows,
        requested=len(requested),
        pg_confirmed_at=pg_confirmed_at,
        absent_current=absent_current,
        absent_projection=absent_projection,
        mismatch_hash=mismatch_hash,
    )
