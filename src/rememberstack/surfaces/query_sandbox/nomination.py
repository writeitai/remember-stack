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
            "location_header",
        ),
        (23, 701, 25, 2950, 2950, 2950, 2950, 2950, 25, 25, 25),
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


def validate_filters(*, target: str, filters: object) -> dict[str, Any]:
    """Check a filter object against its target's allowlist (§3.4).

    Unknown keys, wrong shapes, and caller-authored predicates are rejected:
    the filter surface is a fixed vocabulary, not an expression language.
    """
    if filters is None:
        return {}
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
        " c.embedding_text_hash, c.location_header"
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


def confirm(
    *,
    connection: psycopg.Connection,
    target: str,
    nominations: Sequence[P1Nomination],
    deployment_id: UUID,
    filters: dict[str, Any],
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
        # a join against these columns and it must still type-check.
        columns, oids = EMPTY_CONTRACTS[target]
        return Confirmation(columns=columns, rows=[], type_oids=oids)
    ids = [nomination.item_id for nomination in nominations]
    statement = _CONFIRM_SQL[target]
    authorization_filters = {
        key: value
        for key, value in filters.items()
        if key not in PROJECTION_ONLY_FILTERS
    }
    predicates, parameters = _filter_predicates(
        target=target, filters=authorization_filters
    )
    # The filters are asked as a question, not used to hide rows: a row that
    # fails one is a filter drop, while a row the view no longer publishes at
    # all is a staleness drop, and calling both "stale" would misreport why the
    # projection's memory and PostgreSQL disagree.
    extra = f", ({' AND '.join(predicates)}) AS filter_pass" if predicates else ""
    statement = statement.replace("{extra}", extra)
    cursor = connection.execute(
        statement.encode(), {"deployment": str(deployment_id), "ids": ids, **parameters}
    )
    description = cursor.description or ()
    columns = tuple(column.name for column in description)
    type_oids = tuple(column.type_code for column in description)
    by_id = {str(row[0]): row for row in cursor.fetchall()}

    payload = 2 if predicates else 1
    rows: list[tuple[Any, ...]] = []
    dropped_stale = 0
    dropped_filtered = 0
    for nomination in nominations:
        confirmed = by_id.get(nomination.item_id)
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
    """Confirmed bodies, and the count of ids each rejection category ate."""

    rows: list[tuple[Any, ...]]
    requested: int
    absent: int = 0
    mismatched: int = 0


def chunk_id_list(value: object) -> tuple[str, ...]:
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
    ordered: list[str] = []
    for item in items:
        try:
            identifier = str(UUID(str(item)))
        except (ValueError, AttributeError, TypeError) as error:
            raise SandboxRejection(
                code=QueryErrorCode.INVALID_PARAMETER,
                message="chunk_ids must contain only UUIDs",
            ) from error
        if identifier not in ordered:
            ordered.append(identifier)
    if len(ordered) > CHUNK_IDS_MAX:
        raise SandboxRejection(
            code=QueryErrorCode.INVALID_PARAMETER,
            message=f"fetch_chunk_bodies takes at most {CHUNK_IDS_MAX} chunk ids",
        )
    return tuple(ordered)


def confirm_bodies(
    *,
    connection: psycopg.Connection,
    chunk_ids: Sequence[str],
    deployment_id: UUID,
    texts: dict[str, Any],
) -> BodyConfirmation:
    """Confirm each id's current coordinate, then verify the bytes against it.

    PostgreSQL decides which chunks still exist and what their current
    coordinate, hashes, and D80 header are; only then is projection text
    admitted, and only if it hashes to what the spine recorded and still
    carries exactly the header the spine generated. Text that fails either
    check is dropped rather than returned with a caveat.
    """
    if not chunk_ids:
        return BodyConfirmation(rows=[], requested=0)
    cursor = connection.execute(
        _BODY_SQL.encode(), {"deployment": str(deployment_id), "ids": list(chunk_ids)}
    )
    by_id = {str(row[0]): row for row in cursor.fetchall()}
    generations = {
        (row[10], row[11]) for row in by_id.values()
    }  # (policy_generation, embedder_generation)
    if len(generations) > 1:
        raise SandboxRejection(
            code=QueryErrorCode.INVALID_PARAMETER,
            message="the requested chunks span more than one D80 generation",
        )

    rows: list[tuple[Any, ...]] = []
    absent = 0
    mismatched = 0
    for ordinal, chunk_id in enumerate(chunk_ids):
        confirmed = by_id.get(chunk_id)
        text = texts.get(chunk_id)
        if confirmed is None or text is None:
            absent += 1
            continue
        indexed = getattr(text, "indexed_text", None)
        header = confirmed[8] or ""
        if not isinstance(indexed, str) or not indexed.startswith(header):
            mismatched += 1
            continue
        expected = confirmed[7]
        if expected and embedding_text_hash(indexed) != expected:
            mismatched += 1
            continue
        rows.append((ordinal, *confirmed[1:8], indexed[len(header) :], *confirmed[8:]))
    return BodyConfirmation(
        rows=rows, requested=len(chunk_ids), absent=absent, mismatched=mismatched
    )
