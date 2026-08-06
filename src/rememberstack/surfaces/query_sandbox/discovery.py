"""Schema discovery for the open query space (design §3.1, §6).

Both entry points read the checked-in manifest — never `pg_catalog`, never
tenant content — so discovery output is byte-stable for a given surface
version and cannot leak data. The first-call resource leads with the bound
two-layer retrieval headline, the three neutral choices, honesty warnings,
and the shared worked-example set, then exposes every already-loaded
authoritative hash member without a hand-selected shortened subset.
"""

from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
import fnmatch
from typing import Any
from typing import cast

from rememberstack.core.open_query_prose import bound_worked_examples
from rememberstack.core.open_query_prose import HONESTY_WARNINGS
from rememberstack.core.open_query_prose import RETRIEVAL_CHOICES
from rememberstack.core.open_query_prose import TWO_LAYER_HEADLINE
from rememberstack.core.open_query_prose import TWO_LAYER_HEADLINE_FULL
from rememberstack.core.open_query_prose import TWO_LAYER_HEADLINE_NOTE
from rememberstack.spine.query_space.manifest import load_manifest
from rememberstack.surfaces.query_sandbox.errors import QueryErrorCode
from rememberstack.surfaces.query_sandbox.errors import SandboxRejection
from rememberstack.surfaces.query_sandbox.examples import EXAMPLE_QUERIES

# Re-export the shared authority so existing imports keep working.
__all__ = (
    "DiscoveryHit",
    "QuerySpaceDescription",
    "TWO_LAYER_HEADLINE",
    "TWO_LAYER_HEADLINE_FULL",
    "TWO_LAYER_HEADLINE_NOTE",
    "ViewDescription",
    "describe_query_space",
    "query_space_description_payload",
    "search_query_space",
)


@dataclass(frozen=True)
class ViewDescription:
    """One view's discoverable contract."""

    name: str
    grain: str
    row_key: tuple[str, ...]
    comment: str
    columns: tuple[tuple[str, str, bool], ...]  # (name, type, nullable)


@dataclass(frozen=True)
class DiscoveryHit:
    """One ranked hit over checked-in manifest text (design §6).

    `kind` is one of ``view``, ``function``, ``core_operation``, or
    ``example``. Hits never include tenant content or ``pg_catalog`` rows.
    """

    kind: str
    name: str
    score: float
    purpose: str
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class QuerySpaceDescription:
    """The complete `describe_query_space` payload (design §3.1, §6).

    Serialization paths MUST expose every field already loaded here — use
    :func:`query_space_description_payload` rather than a second hand-selected
    subset that can drift.
    """

    schema: str
    schema_major: int
    surface_manifest_hash: str
    headline: str
    retrieval_choices: tuple[str, ...]
    honesty_warnings: tuple[str, ...]
    worked_examples: tuple[dict[str, object], ...]
    views: tuple[ViewDescription, ...]
    functions: tuple[str, ...]
    limits: dict[str, dict[str, int]]
    core_operation_descriptors: dict[str, object]
    function_signatures: dict[str, object]
    sql_grammar: dict[str, object]
    cypher_dialect: dict[str, object]
    p2_projection: dict[str, object]
    examples: tuple[str, ...]


def query_space_description_payload(
    description: QuerySpaceDescription,
) -> dict[str, object]:
    """Serialize one :class:`QuerySpaceDescription` for HTTP and MCP.

    Uses ``dataclasses.asdict`` so every authoritative field is present and
    cannot drift from a second hand-maintained mapping.
    """
    return cast("dict[str, object]", asdict(description))


def describe_query_space(
    *, pattern: str | None = None, include_examples: bool = False
) -> QuerySpaceDescription:
    """Manifest-backed exact schema, comments, hash, limits, and examples (§3.1)."""
    manifest = load_manifest()
    members = cast("dict[str, Any]", manifest["hash_members"])
    views_schema = cast("dict[str, Any]", members["views_schema"])
    views = []
    for view in cast("list[dict[str, Any]]", views_schema["views"]):
        name = str(view["name"])
        if pattern is not None and not fnmatch.fnmatch(name, pattern):
            continue
        views.append(
            ViewDescription(
                name=name,
                grain=str(view["grain"]),
                row_key=tuple(str(key) for key in view["row_key"]),
                comment=str(view["comment"]),
                columns=tuple(
                    (str(column["name"]), str(column["type"]), bool(column["nullable"]))
                    for column in cast("list[dict[str, Any]]", view["columns"])
                ),
            )
        )
    signatures = cast("dict[str, Any]", members["function_signatures"])
    signature_entries = cast("list[dict[str, Any]]", signatures["functions"])
    limits_member = cast("dict[str, Any]", members["limits"])
    # Shipped examples are discoverable names under the examples namespace.
    # Customer saved queries are listed via list_saved_queries; drafts are not
    # included here and are excluded from that listing by default (§5).
    examples = (
        tuple(f"examples.{name}" for name in sorted(EXAMPLE_QUERIES))
        if include_examples
        else ()
    )
    return QuerySpaceDescription(
        schema=str(views_schema["schema"]),
        schema_major=int(views_schema["schema_major"]),
        surface_manifest_hash=str(manifest["surface_manifest_hash"]),
        headline=TWO_LAYER_HEADLINE,
        retrieval_choices=RETRIEVAL_CHOICES,
        honesty_warnings=HONESTY_WARNINGS,
        worked_examples=bound_worked_examples(),
        views=tuple(views),
        functions=tuple(
            sorted(
                str(entry["name"])
                for entry in signature_entries
                if entry.get("channel") != "cypher"
            )
        ),
        # Every named field of each tier's authoritative limit record (§6).
        limits=cast("dict[str, dict[str, int]]", limits_member["resource_limits"]),
        core_operation_descriptors=cast(
            "dict[str, object]", members["core_operation_descriptors"]
        ),
        function_signatures=cast("dict[str, object]", signatures),
        # Already-loaded authoritative limits fields — never omit one.
        sql_grammar=cast("dict[str, object]", limits_member["sql_grammar"]),
        cypher_dialect=cast("dict[str, object]", limits_member["cypher_dialect"]),
        p2_projection=cast("dict[str, object]", limits_member["p2_projection"]),
        examples=examples,
    )


def search_query_space(*, query: str, k: int = 10) -> tuple[DiscoveryHit, ...]:
    """Rank checked-in manifest text against a free-text query (§3.1, §6).

    Searches names, comments, tags, and examples across views, function
    signatures, core operation descriptors, and shipped example
    names/purposes. Deterministic term-overlap scoring; ``k`` in 1..25.
    Never reads ``pg_catalog`` or tenant content.
    """
    if not 1 <= k <= 25:
        raise SandboxRejection(
            code=QueryErrorCode.INVALID_PARAMETER, message="k must be in 1..25"
        )
    terms = {term for term in query.lower().split() if term}
    if not terms:
        raise SandboxRejection(
            code=QueryErrorCode.INVALID_PARAMETER, message="query must be non-empty"
        )
    scored: list[DiscoveryHit] = []
    for kind, name, purpose, tags in _search_corpus():
        name_l = name.lower()
        prose = " ".join((purpose, " ".join(tags))).lower()
        # A term in the identity's own name is the strongest signal an agent
        # can give: "current facts" must find `facts_current`, not merely the
        # rows whose prose mentions both words.
        score = sum(4.0 for term in terms if term in name_l)
        score += sum(1.0 for term in terms if term in prose)
        if name_l in terms or name_l.split(".")[-1] in terms:
            score += 8.0
        if score > 0:
            scored.append(
                DiscoveryHit(
                    kind=kind, name=name, score=score, purpose=purpose, tags=tags
                )
            )
    scored.sort(key=lambda hit: (-hit.score, hit.kind, hit.name))
    return tuple(scored[:k])


def _search_corpus() -> list[tuple[str, str, str, tuple[str, ...]]]:
    """Build the content-free searchable corpus from the checked-in manifest."""
    description = describe_query_space()
    rows: list[tuple[str, str, str, tuple[str, ...]]] = []
    for view in description.views:
        tags = (view.grain, *view.row_key)
        rows.append(("view", view.name, view.comment, tags))
    signatures = cast(
        "list[dict[str, Any]]", description.function_signatures["functions"]
    )
    for entry in signatures:
        name = str(entry["name"])
        comment = str(entry.get("comment") or entry.get("channel") or "")
        tags = tuple(
            str(value)
            for value in (entry.get("channel"), entry.get("target"), entry.get("grade"))
            if value is not None
        )
        rows.append(("function", name, comment, tags))
    core = cast("dict[str, Any]", description.core_operation_descriptors)
    operations = cast("list[dict[str, Any]]", core.get("operations", []))
    for entry in operations:
        name = str(entry.get("name") or entry.get("operation") or "")
        if not name:
            continue
        purpose = str(
            entry.get("description")
            or entry.get("comment")
            or entry.get("contract")
            or ""
        )
        tags = tuple(
            str(value)
            for value in (entry.get("version"), entry.get("grain"))
            if value is not None
        )
        rows.append(("core_operation", name, purpose, tags))
    for example_name, (purpose, _sql) in EXAMPLE_QUERIES.items():
        rows.append(
            (
                "example",
                f"examples.{example_name}",
                purpose,
                ("shipped_example", "examples"),
            )
        )
    return rows
