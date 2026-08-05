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
from typing import Any
from typing import Final
from uuid import UUID

import psycopg

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
        checked[key] = value
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
        "SELECT claim_id::text AS id, claim_text, source_handle, asserted_at,"
        " claim_valid_from, claim_valid_until"
        " FROM memory_v1.claims_live"
        " WHERE deployment_id = %(deployment)s AND claim_id = ANY(%(ids)s::uuid[])"
    ),
    "chunks": (
        "SELECT chunk_id::text AS id, doc_id, version_id, representation_id,"
        " section_id, chunk_content_hash, embedding_text_hash"
        " FROM memory_v1.chunks_live"
        " WHERE deployment_id = %(deployment)s AND chunk_id = ANY(%(ids)s::uuid[])"
    ),
    "facts": (
        "SELECT fact_id::text AS id, fact_kind, fact_label, predicate,"
        " subject_entity_id, object_entity_id, evidence_count, contradict_count,"
        " support_state, evaluated_at"
        " FROM memory_v1.facts_current"
        " WHERE deployment_id = %(deployment)s AND fact_id = ANY(%(ids)s::uuid[])"
    ),
    "entities": (
        "SELECT entity_id::text AS id, entity_type, canonical_name,"
        " profile_summary, live_mention_count, live_document_count"
        " FROM memory_v1.entities_current"
        " WHERE deployment_id = %(deployment)s AND entity_id = ANY(%(ids)s::uuid[])"
    ),
}


def confirm(
    *,
    connection: psycopg.Connection,
    target: str,
    nominations: Sequence[P1Nomination],
    deployment_id: UUID,
    filters: dict[str, Any],
) -> tuple[tuple[str, ...], list[tuple[Any, ...]], int]:
    """Confirm nominated ids against the invariant views; drop what fails.

    Everything the projection proposed is checked here, in the same views an
    ordinary query reads, so a row that the spine no longer publishes — a
    tombstoned lineage, a superseded fact — cannot reach a caller through the
    projection's memory of it. Rank order is preserved; ranks keep their gaps,
    because hiding a drop would misreport the channel.
    """
    if not nominations:
        return (), [], 0
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
    if predicates:
        statement = f"{statement} AND {' AND '.join(predicates)}"
    cursor = connection.execute(
        statement.encode(), {"deployment": str(deployment_id), "ids": ids, **parameters}
    )
    description = cursor.description or ()
    columns = tuple(column.name for column in description)
    by_id = {str(row[0]): row for row in cursor.fetchall()}

    rows: list[tuple[Any, ...]] = []
    for nomination in nominations:
        confirmed = by_id.get(nomination.item_id)
        if confirmed is None:
            continue
        rows.append(
            (nomination.rank, nomination.score, nomination.channel, *confirmed[1:])
        )
    result_columns = ("rank", "score", "channel", *columns[1:])
    return result_columns, rows, len(nominations) - len(rows)


def _filter_predicates(
    *, target: str, filters: dict[str, Any]
) -> tuple[list[str], dict[str, Any]]:
    """Repeat every authorization-relevant filter in PostgreSQL (§3.4)."""
    predicates: list[str] = []
    parameters: dict[str, Any] = {}
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
        elif key == "doc_id":
            predicates.append(f"doc_id = %({parameter})s::uuid")
        elif key == "entity_type":
            predicates.append(f"entity_type = %({parameter})s")
        else:
            predicates.append(f"{key} = %({parameter})s")
        parameters[parameter] = value
    return predicates, parameters
