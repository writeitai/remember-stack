"""Generate, load, and hash the checked-in `memory_v1` schema manifest.

The manifest is the machine-readable answer to "what exactly is public, and
what does each column mean". It is built from two sources and nothing else:
the authored DDL in the migration supplies the ordered column names, the
relation and column comments, and the canonical definition AST; `catalog.py`
supplies the facts the DDL cannot state — column types, nullability, row and
join keys, grain, clock semantics, and the bound vocabularies behind the `text`
columns.

**No database is involved in building it, deliberately.** A hash that can only
be reproduced by starting a server, applying migrations, and asking that server
how it renders a stored view is a hash with a dependency on the server's
version and printer. Building from source instead means any checkout can
recompute `surface_manifest_hash` and get the same 64 characters.

The live database's role is the other half of the contract, and it is a
*comparison* rather than an input: `live_schema_differences()` reads the
deployed relation set, ordered columns, canonical type names, and comments from
`pg_catalog` alone — never from `catalog.py` — and reports every disagreement
with the checked-in manifest. That is what makes the check meaningful: the two
sides come from independent places, so a wrong declaration fails rather than
being compared with itself.

**Shape is not enough, and the second comparison is why.** Names, columns,
types, and comments describe the *interface* of a view, not what it returns.
`CREATE OR REPLACE VIEW` can keep every one of them identical while replacing
the body — the extreme case being a definition with `WHERE false`, which
publishes the declared contract and no rows, and which a shape check reports as
clean. The manifest hash cannot see it either, by design: it is taken over the
*authored* DDL in the repository, so it says what the checkout intends and
nothing about what a particular server is actually running. So a second comparison exists beside the shape check:
`deployed_definition_differences()` takes the running connection and a
connection to a scratch database on the **same server**, migrated from this
repository, deparses every `memory_v1` view and every private helper in both
with `pg_get_viewdef()`, and compares them pairwise. Same server means the same
printer on both sides, so the comparison is exact without the deparser ever
becoming a hash input — the drift is caught, and `surface_manifest_hash` stays
reproducible from source with no server at all. `live_schema_differences()`
remains the shape gate; a caller that wants both runs both.

The file has two halves, and the split is deliberate.

- ``hash_members`` is exactly the four members the binding design hashes:
  ``views_schema``, ``function_signatures``, ``core_operation_descriptors``,
  and ``limits``. Its canonical JSON serialization is the input to
  ``surface_manifest_hash``. Three of those members are structurally bound and
  carry no entries yet: the SQL-callable functions, the assured-operation
  descriptors, and the grammar and resource limits are contributed by the
  later query-space work, and binding their shape now means adding them cannot
  reshape the hashed document, only fill it.
- ``annotations`` carries everything a reviewer wants and the hash must ignore:
  the indexes each definition's authorization chain relies on, and the positive
  and negative fixture case each view's gate executes. Physical indexes are
  explicitly excluded from the hash, so a new index must not roll it.
"""

import hashlib
import json
from pathlib import Path
from typing import Any
from typing import cast
from typing import Final

from pydantic import BaseModel
from pydantic import ConfigDict
from sqlalchemy import Connection
from sqlalchemy import text

from rememberstack.spine.query_space.ast_serializer import SERIALIZER_VERSION
from rememberstack.spine.query_space.canonical import canonical_json_bytes
from rememberstack.spine.query_space.canonical import CanonicalValue
from rememberstack.spine.query_space.canonical import surface_manifest_hash
from rememberstack.spine.query_space.catalog import POSTGRESQL_MAJOR
from rememberstack.spine.query_space.catalog import QUERY_SPACE_SCHEMA
from rememberstack.spine.query_space.catalog import QUERY_SPACE_SCHEMA_MAJOR
from rememberstack.spine.query_space.catalog import VIEW_CONTRACTS
from rememberstack.spine.query_space.catalog import VIEW_CONTRACTS_BY_NAME
from rememberstack.spine.query_space.source_definitions import (
    AUTHORED_AUTHORIZATION_HELPERS,
)
from rememberstack.spine.query_space.source_definitions import AUTHORED_VIEWS

#: Identifier of this manifest document's own layout.
MANIFEST_CONTRACT: Final = "memory_v1.manifest/2"

#: The checked-in manifest that discovery serves and the schema gate compares.
MANIFEST_PATH: Final = Path(__file__).with_name("memory_v1_manifest.json")


class SchemaManifestError(RuntimeError):
    """Raised when the authored DDL and the declared contract disagree."""


class LiveColumn(BaseModel):
    """One column exactly as the running server's catalogs report it."""

    model_config = ConfigDict(frozen=True)

    name: str
    type: str
    comment: str | None


class LiveView(BaseModel):
    """One deployed relation exactly as the running server reports it."""

    model_config = ConfigDict(frozen=True)

    name: str
    comment: str | None
    columns: tuple[LiveColumn, ...]


class LiveSchema(BaseModel):
    """The deployed query space, read from `pg_catalog` with no declarations."""

    model_config = ConfigDict(frozen=True)

    postgresql_major: int
    schema_name: str
    views: tuple[LiveView, ...]


class ManifestColumn(BaseModel):
    """One documented output column of a public view."""

    model_config = ConfigDict(frozen=True)

    name: str
    type: str
    nullable: bool
    enum_values: tuple[str, ...] | None
    comment: str


class ManifestView(BaseModel):
    """One documented public relation, as declared and authored."""

    model_config = ConfigDict(frozen=True)

    name: str
    qualified_name: str
    grain: str
    grain_tag: str
    clock_semantics: str
    row_key: tuple[str, ...]
    join_keys: tuple[dict[str, Any], ...]
    comment: str
    columns: tuple[ManifestColumn, ...]
    definition_ast: CanonicalValue


def stub_function_signatures() -> dict[str, CanonicalValue]:
    """Return the bound, still-unpopulated SQL-function member.

    Superseded by `_bridge_function_signatures()` once the sandbox exists;
    kept for a checkout that has the schema but not yet the surfaces package.
    """
    return {"contract": "memory_v1.functions/1", "functions": []}


#: PostgreSQL type OIDs the query space publishes, by name. A column list
#: without types tells a caller what it will get called but not what it is.
_TYPE_NAMES: Final[dict[int, str]] = {
    16: "boolean",
    20: "bigint",
    23: "integer",
    25: "text",
    701: "double precision",
    1184: "timestamptz",
    2950: "uuid",
}


def _bridge_function_signatures() -> dict[str, CanonicalValue]:
    """The §6 `function_signatures` member: what each public function accepts
    and answers with, taken from the implementation rather than restated.

    A caller reads this to know the arity, the filter vocabulary, and the
    columns a function returns without executing anything, and any change to
    those rolls `surface_manifest_hash` — the point of putting them here.
    """
    from rememberstack.surfaces.query_sandbox import nomination
    from rememberstack.surfaces.query_sandbox.bridge import FACTS_AS_OF_COLUMN_TYPES
    from rememberstack.surfaces.query_sandbox.bridge import FACTS_AS_OF_COLUMNS
    from rememberstack.surfaces.query_sandbox.bridge import FACTS_AS_OF_ROWS_MAX
    from rememberstack.surfaces.query_sandbox.bridge import FUNCTION_TARGETS
    from rememberstack.surfaces.query_sandbox.bridge import SIGNATURES

    #: The declared parameter list of each nomination function, in order.
    #: Arity alone does not tell a caller what the third argument means.
    nomination_arguments: list[CanonicalValue] = [
        {"name": "query", "type": "text", "required": True},
        {"name": "k", "type": "integer", "required": True},
        {"name": "filters", "type": "jsonb", "required": False, "default": "{}"},
        {
            "name": "embedding_input_policy_version",
            "type": "text",
            "required": False,
            "default": None,
        },
        {
            "name": "embedder_generation",
            "type": "text",
            "required": False,
            "default": None,
        },
    ]

    functions: list[CanonicalValue] = []
    for name in sorted(SIGNATURES):
        target, channel = FUNCTION_TARGETS[name]
        least, most = SIGNATURES[name]
        columns, oids = nomination.published_contract(target)
        functions.append(
            {
                "name": name,
                "target": target,
                "channel": channel,
                "arguments_min": least,
                "arguments_max": most,
                "arguments": nomination_arguments[:most],
                "security": "invoker",
                "filters": list(sorted(nomination.FILTER_ALLOWLISTS[target])),
                "projection_filters": list(
                    sorted(nomination.LANCE_FILTER_COLUMNS[target])
                ),
                "columns": list(columns),
                "column_types": [_TYPE_NAMES.get(oid, "unknown") for oid in oids],
                # These call an external projection, so repeated evaluation in
                # one statement need not agree: they are volatile, whatever the
                # PostgreSQL-side helpers are.
                "volatility": "volatile",
                "parallel": "unsafe",
            }
        )
    functions.append(
        {
            # Pure SQL: it needs no projection, so PostgreSQL runs it directly
            # and its arity is enforced by the function itself.
            "name": "facts_as_of",
            "target": "facts",
            "channel": "bitemporal",
            "arguments_min": 2,
            "arguments_max": 3,
            "arguments": [
                {"name": "valid_at", "type": "timestamptz", "required": True},
                {"name": "believed_at", "type": "timestamptz", "required": True},
                {
                    "name": "max_rows",
                    "type": "integer",
                    "required": False,
                    "default": 200,
                },
            ],
            "volatility": "stable",
            "security": "invoker",
            "filters": [],
            "projection_filters": [],
            "columns": list(FACTS_AS_OF_COLUMNS),
            "column_types": list(FACTS_AS_OF_COLUMN_TYPES),
            "max_rows_hard_cap": FACTS_AS_OF_ROWS_MAX,
            "parallel": "safe",
        }
    )
    functions.append(
        {
            "name": "fetch_chunk_bodies",
            "target": "chunks",
            "channel": "body",
            "arguments_min": 1,
            "arguments_max": 1,
            "arguments": [{"name": "chunk_ids", "type": "uuid[]", "required": True}],
            "security": "invoker",
            "filters": [],
            "projection_filters": [],
            "columns": list(nomination.BODY_COLUMNS),
            "column_types": [
                _TYPE_NAMES.get(oid, "unknown") for oid in nomination.BODY_TYPE_OIDS
            ],
            "chunk_ids_max": nomination.CHUNK_IDS_MAX,
            "volatility": "volatile",
            "parallel": "unsafe",
        }
    )
    graph_edge_columns = [
        "relation_id",
        "subject_entity_id",
        "object_entity_id",
        "predicate",
        "fact_label",
        "valid_from",
        "valid_until",
        "ingested_at",
        "invalidated_at",
        "contradiction_group",
        "confidence",
        "evidence_count_current",
        "contradict_count_current",
        "support_state_current",
    ]
    graph_edge_types = [
        "uuid",
        "uuid",
        "uuid",
        "text",
        "text",
        "timestamptz",
        "timestamptz",
        "timestamptz",
        "timestamptz",
        "uuid",
        "real",
        "bigint",
        "bigint",
        "text",
    ]
    functions.extend(
        [
            {
                "name": "graph_neighborhood",
                "target": "graph",
                "channel": "postgresql",
                "arguments_min": 1,
                "arguments_max": 6,
                "arguments": [
                    {"name": "start_entity_id", "type": "uuid", "required": True},
                    {
                        "name": "max_depth",
                        "type": "integer",
                        "required": False,
                        "default": 2,
                        "hard_cap": 4,
                    },
                    {
                        "name": "predicates",
                        "type": "text[]",
                        "required": False,
                        "default": None,
                    },
                    {
                        "name": "valid_at",
                        "type": "timestamptz",
                        "required": False,
                        "default": None,
                    },
                    {
                        "name": "believed_at",
                        "type": "timestamptz",
                        "required": False,
                        "default": None,
                    },
                    {
                        "name": "max_edges",
                        "type": "integer",
                        "required": False,
                        "default": 100,
                        "hard_cap": 500,
                    },
                ],
                "volatility": "stable",
                "security": "invoker",
                "parallel": "safe",
                "comment": (
                    "Traverse the live graph with statement_timestamp() applied to"
                    " both clocks when both are omitted, or the half-open historical"
                    " graph when both valid_at and believed_at are supplied. Supplying"
                    " exactly one clock fails with invalid_parameter_value."
                ),
                "example": (
                    "SELECT * FROM memory_v1.graph_neighborhood("
                    "$1, 2, NULL, $2, $3, 100)"
                ),
                "columns": [
                    "path_id",
                    "hop",
                    "path_position",
                    "from_entity_id",
                    "to_entity_id",
                    *graph_edge_columns,
                    "applied_valid_at",
                    "applied_believed_at",
                ],
                "column_types": [
                    "bigint",
                    "integer",
                    "integer",
                    "uuid",
                    "uuid",
                    *graph_edge_types,
                    "timestamptz",
                    "timestamptz",
                ],
            },
            {
                "name": "graph_path",
                "target": "graph",
                "channel": "postgresql",
                "arguments_min": 2,
                "arguments_max": 8,
                "arguments": [
                    {"name": "from_entity_id", "type": "uuid", "required": True},
                    {"name": "to_entity_id", "type": "uuid", "required": True},
                    {
                        "name": "max_depth",
                        "type": "integer",
                        "required": False,
                        "default": 4,
                        "hard_cap": 6,
                    },
                    {
                        "name": "predicates",
                        "type": "text[]",
                        "required": False,
                        "default": None,
                    },
                    {
                        "name": "valid_at",
                        "type": "timestamptz",
                        "required": False,
                        "default": None,
                    },
                    {
                        "name": "believed_at",
                        "type": "timestamptz",
                        "required": False,
                        "default": None,
                    },
                    {
                        "name": "max_paths",
                        "type": "integer",
                        "required": False,
                        "default": 3,
                        "hard_cap": 10,
                    },
                    {
                        "name": "max_edges",
                        "type": "integer",
                        "required": False,
                        "default": 100,
                        "hard_cap": 500,
                    },
                ],
                "volatility": "stable",
                "security": "invoker",
                "parallel": "safe",
                "comment": (
                    "Return bounded simple paths over the live graph with"
                    " statement_timestamp() applied to both clocks when both are"
                    " omitted, or the half-open historical graph when both valid_at"
                    " and believed_at are supplied. Supplying exactly one clock fails"
                    " with invalid_parameter_value."
                ),
                "example": (
                    "SELECT * FROM memory_v1.graph_path("
                    "$1, $2, 4, NULL, $3, $4, 3, 100)"
                ),
                "columns": [
                    "path_id",
                    "path_length",
                    "path_position",
                    "step_from_entity_id",
                    "step_to_entity_id",
                    *graph_edge_columns,
                    "applied_valid_at",
                    "applied_believed_at",
                ],
                "column_types": [
                    "bigint",
                    "integer",
                    "integer",
                    "uuid",
                    "uuid",
                    *graph_edge_types,
                    "timestamptz",
                    "timestamptz",
                ],
            },
            {
                "name": "query_cypher",
                "target": "graph",
                "channel": "cypher",
                "arguments_min": 1,
                "arguments_max": 3,
                "arguments": [
                    {"name": "cypher", "type": "text", "required": True},
                    {
                        "name": "parameters",
                        "type": "json",
                        "required": False,
                        "default": {},
                    },
                    {
                        "name": "max_rows",
                        "type": "integer",
                        "required": False,
                        "default": None,
                    },
                ],
                "execution_options": {"confirm": {"type": "boolean", "default": False}},
                "result_contract": "QueryResult/v1",
                "grade": "snapshot_graph",
                "comment": (
                    "Execute one bounded read against the disclosed P2 snapshot;"
                    " confirm defaults false and checks only top-level typed Entity"
                    " and RELATES values. The manifest's rejected_functions list"
                    " names the pinned physical-address origin and coercion functions"
                    " that are unavailable."
                ),
                "example": (
                    "MATCH (e:Entity) RETURN e.id, e.name ORDER BY e.name LIMIT 20"
                ),
                "columns": ["result"],
                "column_types": ["QueryResult/v1"],
            },
            {
                "name": "explain_cypher",
                "target": "graph",
                "channel": "cypher",
                "arguments_min": 1,
                "arguments_max": 2,
                "arguments": [
                    {"name": "cypher", "type": "text", "required": True},
                    {
                        "name": "parameters",
                        "type": "json",
                        "required": False,
                        "default": {},
                    },
                ],
                "result_contract": "QueryResult/v1",
                "grade": "snapshot_graph",
                "comment": (
                    "Return the bounded engine plan for one accepted read without"
                    " executing it. The same dialect gate applies, including refusal"
                    " of the manifest's physical-address functions."
                ),
                "example": "MATCH (e:Entity) RETURN e.name LIMIT 20",
                "columns": ["result"],
                "column_types": ["QueryResult/v1"],
            },
        ]
    )
    published = {entry["name"] for entry in functions}  # type: ignore[index]
    return {
        "contract": "memory_v1.functions/1",
        # A function the grammar admits but this build cannot resolve is named
        # here rather than left for a caller to discover by calling it.
        "declared_without_signatures": list(
            sorted(
                name
                for name in __import__(
                    "rememberstack.surfaces.query_sandbox.grammar",
                    fromlist=["PUBLIC_SRF_NAMES"],
                ).PUBLIC_SRF_NAMES
                if name not in published
            )
        ),
        # Globally sorted: a reader comparing two manifests should see a
        # difference only where the surface differs.
        "functions": sorted(functions, key=lambda entry: entry["name"]),  # type: ignore[index,arg-type]
    }


def stub_core_operation_descriptors() -> dict[str, CanonicalValue]:
    """Return the bound, still-unpopulated assured-operation member."""
    return {"contract": "memory_v1.core_operations/1", "operations": []}


def _core_operation_descriptors() -> dict[str, CanonicalValue]:
    """The three assured operations, derived from their canonical recipes."""
    from rememberstack.spine.recipes import CANONICAL_RECIPES

    assured = {"resolve_entity", "question_context", "current_context"}
    recipes = {recipe.name: recipe for recipe in CANONICAL_RECIPES}
    if set(recipes) & assured != assured:
        raise SchemaManifestError("the canonical recipe set lacks an assured operation")
    operations: list[CanonicalValue] = []
    for name in sorted(assured):
        recipe = recipes[name]
        properties = cast(
            "dict[str, CanonicalValue]",
            {key: value for key, value in recipe.parameters.items()},
        )
        required = sorted(
            key
            for key, value in recipe.parameters.items()
            if isinstance(value, dict) and value.get("required") is True
        )
        chain = cast(
            "CanonicalValue", [step.model_dump(mode="json") for step in recipe.chain]
        )
        descriptor: dict[str, CanonicalValue] = {
            "name": recipe.name,
            "version": recipe.version,
            "description": recipe.description,
            "input_schema": cast(
                "CanonicalValue",
                {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                    "additionalProperties": False,
                },
            ),
            "envelope_contract": "D49",
            "grain": recipe.output_grain.value,
            "intent": recipe.answer_intent.value,
            "implementation_chain_hash": hashlib.sha256(
                canonical_json_bytes(chain)
            ).hexdigest(),
        }
        if name == "question_context":
            descriptor["channels"] = {
                "claims": {"enabled": True, "grain": "evidence", "hybrid": True},
                "chunks": {"enabled": True, "grain": "evidence", "hybrid": True},
                "facts": {
                    "enabled_by": "include_facts",
                    "default": False,
                    "grain": "fact",
                    "nomination": "semantic",
                    "confirmed_in": "postgresql",
                    "max_facts": 30,
                    "evidence_per_fact": 3,
                    "evidence_budget": 60,
                },
                "entities": {
                    "enabled_by": "include_entities",
                    "default": False,
                    "grain": "fact",
                    "order": "exact_resolution_then_semantic",
                    "confirmed_in": "postgresql",
                    "max_candidates": 20,
                },
            }
        operations.append(descriptor)
    return {"contract": "memory_v1.core_operations/1", "operations": operations}


def stub_limits() -> dict[str, CanonicalValue]:
    """Return the bound, still-unpopulated grammar and resource-limit member.

    Superseded by `_sandbox_limits_member()` once the sandbox exists; kept for
    a checkout that has the schema but not yet the surfaces package.
    """
    return {
        "contract": "memory_v1.limits/1",
        "sql_grammar": {
            "operators": [],
            "pg_catalog_functions": [],
            "statement_node_classes": [],
        },
        "cypher_dialect": {"allowed_clauses": [], "rejected_constructs": []},
        "p2_projection": {"contract_version": None, "node_types": {}, "edge_types": {}},
        "resource_limits": {
            "default": {},
            "interactive_hard_cap": {},
            "analytical_hard_cap": {},
        },
    }


def _sandbox_limits_member() -> dict[str, CanonicalValue]:
    """The §6 `limits` member: the grammar and the caps, not a placeholder.

    A change to an allowlist entry or a tier cap changes the public surface,
    so it must roll `surface_manifest_hash` — that is what makes the hash an
    identity rather than a description of the views alone. The sandbox is
    imported lazily so the schema package keeps no import-time dependency on
    the surfaces package.
    """
    from rememberstack.surfaces.query_sandbox import grammar
    from rememberstack.surfaces.query_sandbox.limits import TIER_LIMITS
    from rememberstack.workers.p2 import P2_PROJECTION_SCHEMA

    sql_grammar: dict[str, CanonicalValue] = {
        "statement_node_classes": list(sorted(grammar.STATEMENT_NODE_ALLOWLIST)),
        "functions": list(sorted(grammar.FUNCTION_ALLOWLIST)),
        "operators": list(sorted(grammar.OPERATOR_ALLOWLIST)),
        "cast_types": list(sorted(grammar.CAST_TYPE_ALLOWLIST)),
        "public_functions": list(sorted(grammar.PUBLIC_SRF_NAMES)),
        # The cap is PER CATEGORY, not per statement: three nomination calls
        # and three body fetches are both inside it. Publishing one number
        # described a stricter surface than the one that ships.
        "srf_invocations_max_per_category": grammar.SRF_INVOCATIONS_MAX,
        "srf_categories": {
            name: category for name, category in sorted(grammar.SRF_CATEGORIES.items())
        },
        "recursion_depth_max": grammar.RECURSION_DEPTH_MAX,
    }
    resource_limits: dict[str, CanonicalValue] = {
        tier.value: {
            field: getattr(caps, field) for field in sorted(caps.__dataclass_fields__)
        }
        for tier, caps in TIER_LIMITS.items()
    }
    return {
        "contract": "memory_v1.limits/1",
        "sql_grammar": sql_grammar,
        "resource_limits": resource_limits,
        "cypher_dialect": _cypher_dialect(),
        "p2_projection": cast("dict[str, CanonicalValue]", P2_PROJECTION_SCHEMA),
    }


def _cypher_dialect() -> dict[str, CanonicalValue]:
    """The §3.5 Cypher read surface: what it accepts and what it refuses.

    The reject list is part of the public contract, not an implementation
    detail: an agent needs to know which constructs die before the engine sees
    them (the file/network/extension family). Mutations are refused by the
    engine's `read_only=True` and mapped to the same public code; a change to
    either path rolls the hash.
    """
    from rememberstack.surfaces.query_sandbox import cypher
    from rememberstack.surfaces.query_sandbox.cypher_executor import (
        CYPHER_TEXT_BYTES_MAX,
    )

    return {
        "contract": "memory_v1.cypher/1",
        "engine": "ladybug",
        "engine_version": "0.18.2",
        "read_clauses": list(sorted(cypher.READ_CLAUSES)),
        "read_openings": list(sorted(cypher.READ_OPENINGS)),
        "rejected_constructs": list(sorted(cypher.REJECTED_KEYWORDS)),
        "engine_rejected_mutations": list(sorted(cypher.ENGINE_REJECTED_MUTATIONS)),
        "rejected_functions": list(sorted(cypher.REJECTED_FUNCTIONS)),
        "text_bytes_max": CYPHER_TEXT_BYTES_MAX,
        # Pinned engine-native recursive upper bound. The executor does not
        # duplicate this grammar in a text walker.
        "recursive_hops_max": cypher.RECURSIVE_HOPS_MAX,
        "grade": "snapshot_graph",
        "process_isolated": False,
        "graph_reference_metadata": "unavailable",
        # `confirm=true` checks live membership of projected entity/relation
        # ids; it does not make any other part of the result live. Naming the
        # types alone read as a promise about any projection of them, which is
        # wider than what the code does — a scalar `RETURN e.id` is not
        # checked, and saying so here is the difference between a documented
        # limit and an overclaim.
        "confirmable_types": ["Entity", "RELATES"],
        "confirmable_projections": ["typed_node_value", "typed_relationship_value"],
        "unconfirmed_projections": ["scalar_id_projection"],
    }


def build_hash_members() -> dict[str, CanonicalValue]:
    """Build the exact document `surface_manifest_hash` is taken over."""
    return {
        "core_operation_descriptors": _core_operation_descriptors(),
        "function_signatures": _bridge_function_signatures(),
        "limits": _sandbox_limits_member(),
        "views_schema": _build_views_schema(),
    }


def build_manifest() -> dict[str, CanonicalValue]:
    """Build the complete manifest document, hash included, from source."""
    members = build_hash_members()
    return {
        "manifest_contract": MANIFEST_CONTRACT,
        "surface_manifest_hash": surface_manifest_hash(members),
        "hash_members": members,
        "annotations": _build_annotations(),
    }


def render_manifest(manifest: dict[str, CanonicalValue]) -> str:
    """Render the manifest as the reviewable checked-in file body.

    The file is indented for review; the hash is taken over the canonical
    serialization of ``hash_members``, so this rendering cannot influence it.
    """
    return json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def load_manifest() -> dict[str, Any]:
    """Read the checked-in manifest document."""
    loaded: Any = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise SchemaManifestError("the checked-in manifest is not a JSON object")
    return loaded


def write_manifest(manifest: dict[str, CanonicalValue]) -> None:
    """Overwrite the checked-in manifest document."""
    MANIFEST_PATH.write_text(render_manifest(manifest), encoding="utf-8")


def declared_views() -> tuple[ManifestView, ...]:
    """Assemble every public relation from its authored DDL and contract."""
    authored_names = set(AUTHORED_VIEWS)
    declared_names = set(VIEW_CONTRACTS_BY_NAME)
    if authored_names != declared_names:
        raise SchemaManifestError(
            f"the authored {QUERY_SPACE_SCHEMA} DDL and the declared contract differ: "
            f"missing {sorted(declared_names - authored_names)}, "
            f"unexpected {sorted(authored_names - declared_names)}"
        )
    return tuple(_declared_view(name=name) for name in sorted(AUTHORED_VIEWS))


def introspect_live_schema(connection: Connection) -> LiveSchema:
    """Read the deployed query space from `pg_catalog` and nothing else.

    This is the independent side of the §9.1 identity check, so it deliberately
    reads no declaration: every value here comes from the running server's own
    catalogs, in the server's own ordering.
    """
    major = int(
        connection.execute(
            statement=text("SELECT current_setting('server_version_num')::int / 10000")
        ).scalar_one()
    )
    relations = connection.execute(
        statement=text(
            "SELECT c.relname, obj_description(c.oid, 'pg_class') AS comment "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = :schema AND c.relkind = 'v' ORDER BY c.relname"
        ),
        parameters={"schema": QUERY_SPACE_SCHEMA},
    ).all()
    columns = connection.execute(
        statement=text(
            "SELECT c.relname, a.attname, "
            "pg_catalog.format_type(a.atttypid, a.atttypmod) AS type, "
            "col_description(a.attrelid, a.attnum) AS comment "
            "FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = :schema AND c.relkind = 'v' "
            "AND a.attnum > 0 AND NOT a.attisdropped "
            "ORDER BY c.relname, a.attnum"
        ),
        parameters={"schema": QUERY_SPACE_SCHEMA},
    ).all()
    by_view: dict[str, list[LiveColumn]] = {}
    for view, column, column_type, comment in columns:
        by_view.setdefault(str(view), []).append(
            LiveColumn(
                name=str(column),
                type=str(column_type),
                comment=None if comment is None else str(comment),
            )
        )
    return LiveSchema(
        postgresql_major=major,
        schema_name=QUERY_SPACE_SCHEMA,
        views=tuple(
            LiveView(
                name=str(name),
                comment=None if comment is None else str(comment),
                columns=tuple(by_view.get(str(name), ())),
            )
            for name, comment in relations
        ),
    )


def live_schema_differences(
    *, connection: Connection, manifest: dict[str, Any] | None = None
) -> tuple[str, ...]:
    """Report every disagreement between the deployed schema and the manifest.

    An empty result is the §9.1 merge condition: the relation set, the ordered
    columns, the canonical type names, and every comment the running database
    reports are exactly what the checked-in manifest publishes.
    """
    published = manifest if manifest is not None else load_manifest()
    views_schema = published["hash_members"]["views_schema"]
    expected_views = {
        str(view["name"]): view
        for view in views_schema["views"]
        if isinstance(view, dict)
    }
    live = introspect_live_schema(connection)
    live_views = {view.name: view for view in live.views}

    problems: list[str] = []
    if live.postgresql_major != views_schema["postgresql_major"]:
        problems.append(
            f"PostgreSQL major {live.postgresql_major} is not the declared "
            f"{views_schema['postgresql_major']}"
        )
    problems.extend(
        f"{missing} is published but not deployed"
        for missing in sorted(set(expected_views) - set(live_views))
    )
    problems.extend(
        f"{unexpected} is deployed but not published"
        for unexpected in sorted(set(live_views) - set(expected_views))
    )

    for name in sorted(set(expected_views) & set(live_views)):
        expected = expected_views[name]
        observed = live_views[name]
        if observed.comment != expected["comment"]:
            problems.append(f"{name} comment differs from the manifest")
        expected_columns = [
            column for column in expected["columns"] if isinstance(column, dict)
        ]
        expected_names = [str(column["name"]) for column in expected_columns]
        observed_names = [column.name for column in observed.columns]
        if expected_names != observed_names:
            problems.append(
                f"{name} deploys columns {observed_names}, publishes {expected_names}"
            )
            continue
        for expected_column, observed_column in zip(
            expected_columns, observed.columns, strict=True
        ):
            if observed_column.type != expected_column["type"]:
                problems.append(
                    f"{name}.{observed_column.name} deploys type "
                    f"{observed_column.type}, publishes {expected_column['type']}"
                )
            if observed_column.comment != expected_column["comment"]:
                problems.append(f"{name}.{observed_column.name} comment differs")
    return tuple(problems)


def _declared_view(*, name: str) -> ManifestView:
    """Assemble one relation from its authored DDL and its declared contract."""
    authored = AUTHORED_VIEWS[name]
    contract = VIEW_CONTRACTS_BY_NAME[name]
    published = set(authored.column_names)
    unknown_types = set(contract.column_types) ^ published
    if unknown_types:
        raise SchemaManifestError(
            f"{name} declares types for a different column set than it publishes: "
            f"{sorted(unknown_types)}"
        )
    for label, declared in (
        ("non-null columns", contract.not_null),
        ("vocabularies", set(contract.enum_values)),
        ("a row key", set(contract.row_key)),
    ):
        unknown = set(declared) - published
        if unknown:
            raise SchemaManifestError(
                f"{name} declares {label} over columns it does not publish: "
                f"{sorted(unknown)}"
            )
    return ManifestView(
        name=name,
        qualified_name=authored.qualified_name,
        grain=contract.grain,
        grain_tag=contract.grain_tag,
        clock_semantics=contract.clock_semantics,
        row_key=contract.row_key,
        join_keys=tuple(
            {"columns": list(join.columns), "target": join.target}
            for join in contract.join_keys
        ),
        comment=authored.comment,
        columns=tuple(
            ManifestColumn(
                name=column,
                type=contract.column_types[column],
                nullable=column not in contract.not_null,
                enum_values=contract.enum_values.get(column),
                comment=authored.column_comments[column],
            )
            for column in authored.column_names
        ),
        definition_ast=authored.definition_ast,
    )


def _build_views_schema() -> dict[str, CanonicalValue]:
    """Build the hashed view member from the authored DDL and the contract."""
    return {
        "postgresql_major": POSTGRESQL_MAJOR,
        "schema": QUERY_SPACE_SCHEMA,
        "schema_major": QUERY_SPACE_SCHEMA_MAJOR,
        "definition_ast_serializer": SERIALIZER_VERSION,
        "authorization_helpers": [
            {
                "qualified_name": helper.qualified_name,
                "definition_ast": helper.definition_ast,
            }
            for helper in AUTHORED_AUTHORIZATION_HELPERS.values()
        ],
        "views": [_view_member(view=view) for view in declared_views()],
    }


def _view_member(*, view: ManifestView) -> dict[str, CanonicalValue]:
    """Render one view into its hashed manifest member."""
    return {
        "name": view.name,
        "qualified_name": view.qualified_name,
        "grain": view.grain,
        "grain_tag": view.grain_tag,
        "clock_semantics": view.clock_semantics,
        "row_key": list(view.row_key),
        "join_keys": [
            {"columns": list(join["columns"]), "target": str(join["target"])}
            for join in view.join_keys
        ],
        "comment": view.comment,
        "columns": [
            {
                "name": column.name,
                "type": column.type,
                "nullable": column.nullable,
                "enum_values": (
                    list(column.enum_values) if column.enum_values is not None else None
                ),
                "comment": column.comment,
            }
            for column in view.columns
        ],
        "definition_ast": view.definition_ast,
    }


def _build_annotations() -> dict[str, CanonicalValue]:
    """Build the non-hashed reviewer material: index usage and fixtures."""
    return {
        "note": (
            "Physical indexes and fixtures are deliberately outside the hashed "
            "members: adding an index or a fixture must not roll "
            "surface_manifest_hash."
        ),
        "views": {
            contract.name: {
                "indexes_used": list(contract.indexes_used),
                "fixtures": {
                    "positive": contract.positive_fixture,
                    "negative": contract.negative_fixture,
                },
            }
            for contract in VIEW_CONTRACTS
        },
    }


def deployed_definitions(connection: Connection) -> dict[str, str]:
    """Deparse every public view and authorization helper now deployed.

    `pg_get_viewdef` is the server's own printer, so two databases on the same
    server print the same definition the same way. That is what makes the
    pairwise comparison in `deployed_definition_differences()` exact without
    the printer's output ever becoming a hash input.
    """
    rows = connection.execute(
        text(
            "SELECT n.nspname || '.' || c.relname AS name,"
            " pg_get_viewdef(c.oid, true) AS definition"
            " FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE c.relkind = 'v'"
            "   AND (n.nspname = :schema"
            "        OR (n.nspname = 'public' AND c.relname = ANY(:helpers)))"
        ),
        {"schema": QUERY_SPACE_SCHEMA, "helpers": list(AUTHORED_AUTHORIZATION_HELPERS)},
    ).mappings()
    return {str(row["name"]): " ".join(str(row["definition"]).split()) for row in rows}


def deployed_definition_differences(
    *, connection: Connection, reference: Connection
) -> tuple[str, ...]:
    """Compare what the database RUNS with an independently migrated build.

    Shape is not enough: `CREATE OR REPLACE VIEW` can keep every column, type,
    and comment identical while replacing the body — the extreme case being a
    definition with `WHERE false`, which publishes the declared contract and no
    rows. The manifest hash cannot see that either, by design: it is taken over
    the authored DDL in the repository, so it says what the checkout intends,
    not what a particular server is running. `reference` is a connection to a
    scratch database on the SAME server, migrated from this repository; a
    disagreement here is deployed drift.
    """
    running = deployed_definitions(connection)
    intended = deployed_definitions(reference)
    problems: list[str] = []
    for name in sorted(set(intended) | set(running)):
        if name not in running:
            problems.append(f"{name} is missing from the deployed schema")
        elif name not in intended:
            problems.append(f"{name} is deployed but not part of this build")
        elif running[name] != intended[name]:
            problems.append(f"{name} definition differs from the migrated build")
    return tuple(problems)
