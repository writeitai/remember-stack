"""Schema discovery for the open query space (design §3.1, §6).

Both entry points read the checked-in manifest — never `pg_catalog`, never
tenant content — so discovery output is byte-stable for a given surface
version and cannot leak data. The first-call resource leads with the bound
two-layer retrieval headline, verbatim from the design.
"""

from dataclasses import dataclass
import fnmatch
from typing import Any
from typing import cast
from typing import Final

from rememberstack.spine.query_space.manifest import load_manifest
from rememberstack.surfaces.query_sandbox.errors import QueryErrorCode
from rememberstack.surfaces.query_sandbox.errors import SandboxRejection

# Bound in the design's opening block ("Bound two-layer retrieval headline
# (reused verbatim)") — discovery, the consumption skill, and the OSS docs
# all open with this exact text.
TWO_LAYER_HEADLINE: Final = (
    "RememberStack has two deliberately separate truth layers. Claims are"
    " immutable source testimony (“what was asserted, by whom, when”);"
    " facts—relations and observations—are the adjudicated current"
    " worldview (“what the system currently holds true”):"
    " supersession-adjudicated, clocked on two time axes (when a fact held in"
    " the world, and when the system learned it), evidence-counted per"
    " distinct source—repetition is not corroboration—and"
    " contradiction-tracked. The fact_claim_evidence association is the"
    " auditable bridge between the layers, recording which claims support or"
    " contradict each fact. Query claims to inspect testimony; query facts to"
    " answer current-truth questions, then follow the bridge to see why the"
    " system believes the fact."
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
class QuerySpaceDescription:
    """The complete `describe_query_space` payload."""

    schema: str
    schema_major: int
    surface_manifest_hash: str
    headline: str
    views: tuple[ViewDescription, ...]
    functions: tuple[str, ...]
    limits: dict[str, dict[str, int]]
    core_operation_descriptors: dict[str, object]
    function_signatures: dict[str, object]
    cypher_dialect: dict[str, object]
    p2_projection: dict[str, object]
    examples: tuple[str, ...]


def describe_query_space(
    *, pattern: str | None = None, include_examples: bool = False
) -> QuerySpaceDescription:
    """Manifest-backed exact schema, comments, hash, and limits (§3.1)."""
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
    return QuerySpaceDescription(
        schema=str(views_schema["schema"]),
        schema_major=int(views_schema["schema_major"]),
        surface_manifest_hash=str(manifest["surface_manifest_hash"]),
        headline=TWO_LAYER_HEADLINE,
        views=tuple(views),
        functions=tuple(
            sorted(
                str(entry["name"])
                for entry in signature_entries
                if entry.get("channel") != "cypher"
            )
        ),
        limits=cast("dict[str, dict[str, int]]", limits_member["resource_limits"]),
        core_operation_descriptors=cast(
            "dict[str, object]", members["core_operation_descriptors"]
        ),
        function_signatures=cast("dict[str, object]", signatures),
        cypher_dialect=cast("dict[str, object]", limits_member["cypher_dialect"]),
        p2_projection=cast("dict[str, object]", limits_member["p2_projection"]),
        # The saved-query registry (Batch E) populates these; until then the
        # flag is honored with an empty set either way.
        examples=() if not include_examples else (),
    )


def search_query_space(*, query: str, k: int = 10) -> tuple[ViewDescription, ...]:
    """Rank manifest text (names, comments, grains) against a free-text query.

    Deterministic and content-free: a simple term-overlap score over the
    checked-in manifest only (§6 binds discovery to manifest text).
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
    scored: list[tuple[float, ViewDescription]] = []
    for view in describe_query_space().views:
        name = view.name.lower()
        prose = " ".join((view.grain, view.comment, " ".join(view.row_key))).lower()
        # A term in the relation's own name is the strongest signal an agent
        # can give: "current facts" must find `facts_current`, not merely the
        # views whose prose mentions both words.
        score = sum(4.0 for term in terms if term in name)
        score += sum(1.0 for term in terms if term in prose)
        if name in terms:
            score += 8.0
        if score > 0:
            scored.append((score, view))
    scored.sort(key=lambda pair: (-pair[0], pair[1].name))
    return tuple(view for _, view in scored[:k])
