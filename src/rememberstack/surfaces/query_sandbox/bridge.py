"""Resolving the public functions before the statement is planned (§3.4).

The grammar has already extracted each accepted invocation into its own
`MATERIALIZED` CTE and recorded which function it stands for. This module runs
those invocations — nomination in the projection, confirmation in PostgreSQL —
and rewrites each CTE body into the confirmed rows, so the statement the
planner sees contains data rather than a call.

Failure is total by design (§4.1): if a projection is unreachable, a
generation cannot be pinned, or confirmation fails, the whole statement fails.
A partially confirmed result would be a result the caller cannot tell from a
complete one.
"""

from collections.abc import Callable
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from typing import Final
from typing import Protocol

import psycopg

from rememberstack.surfaces.query_sandbox.errors import QueryErrorCode
from rememberstack.surfaces.query_sandbox.errors import SandboxRejection
from rememberstack.surfaces.query_sandbox.nomination import BODY_COLUMNS
from rememberstack.surfaces.query_sandbox.nomination import BODY_TYPE_OIDS
from rememberstack.surfaces.query_sandbox.nomination import BodyConfirmation
from rememberstack.surfaces.query_sandbox.nomination import bounded_k
from rememberstack.surfaces.query_sandbox.nomination import BridgeSettings
from rememberstack.surfaces.query_sandbox.nomination import chunk_id_list
from rememberstack.surfaces.query_sandbox.nomination import confirm
from rememberstack.surfaces.query_sandbox.nomination import confirm_chunk_coordinates
from rememberstack.surfaces.query_sandbox.nomination import Confirmation
from rememberstack.surfaces.query_sandbox.nomination import current_chunk_generations
from rememberstack.surfaces.query_sandbox.nomination import projection_filters
from rememberstack.surfaces.query_sandbox.nomination import published_contract
from rememberstack.surfaces.query_sandbox.nomination import validate_filters
from rememberstack.surfaces.query_sandbox.nomination import verify_bodies
from rememberstack.surfaces.query_sandbox.result import SemanticInvocation

#: Which confirmation target and channel each public function answers with.
FUNCTION_TARGETS: Final[dict[str, tuple[str, str]]] = {
    "semantic_claims": ("claims", "semantic"),
    "lexical_claims": ("claims", "bm25"),
    "semantic_chunks": ("chunks", "semantic"),
    "lexical_chunks": ("chunks", "bm25"),
    "semantic_facts": ("facts", "semantic"),
    "semantic_entities": ("entities", "semantic"),
    "fetch_chunk_bodies": ("chunks", "body"),
}

#: Public functions PostgreSQL runs itself. They need no projection, so the
#: executor leaves their invocation in place and the planner sees straight
#: through it; only the Lance-backed ones are resolved and substituted.
SQL_NATIVE_FUNCTIONS: Final = frozenset({"facts_as_of"})

#: Which adapter each public function actually needs. `facts_as_of` needs
#: neither, the lexical channels and the body fetch never embed anything, and
#: refusing those because an embedder is unconfigured would fail requests that
#: would have worked.
NEEDS_PROJECTION: Final = frozenset(FUNCTION_TARGETS)
NEEDS_EMBEDDER: Final = frozenset(
    name for name, (_, channel) in FUNCTION_TARGETS.items() if channel == "semantic"
)


def required_adapters(
    bindings: Sequence[tuple[str, str, tuple[object, ...]]],
) -> tuple[bool, bool]:
    """Whether this statement needs the projection and the embedder."""
    functions = {function for _, function, _ in bindings}
    return (bool(functions & NEEDS_PROJECTION), bool(functions & NEEDS_EMBEDDER))


#: What `facts_as_of` answers with, and the row bound its migration clamps to.
#: Declared here so the manifest can publish the whole public surface from one
#: place; the migration is the thing that enforces them.
FACTS_AS_OF_COLUMNS: Final = (
    "deployment_id",
    "fact_kind",
    "fact_id",
    "subject_entity_id",
    "predicate",
    "object_entity_id",
    "statement",
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
    "applied_valid_at",
    "applied_believed_at",
    "identity_regime",
)
FACTS_AS_OF_COLUMN_TYPES: Final = (
    "uuid",
    "text",
    "uuid",
    "uuid",
    "text",
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
    "timestamptz",
    "timestamptz",
    "text",
)
FACTS_AS_OF_ROWS_MAX: Final = 1000


class EmbeddingSource(Protocol):
    """Whatever turns a query string into the P1 vector space.

    `embedder_generation` is passed only when the caller pinned one. An
    implementation that cannot serve a named generation should not accept the
    argument: the bridge turns that into `generation_unavailable` rather than
    embedding the query in a different space from the stored vectors.
    """

    def __call__(
        self, *, query: str, embedder_generation: str | None = None
    ) -> tuple[float, ...]: ...


@dataclass(frozen=True)
class ResolvedInvocation:
    """One invocation's confirmed rows plus the disclosure it earned."""

    cte_name: str
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    invocation: SemanticInvocation
    #: The PostgreSQL type OID of each column, so the rewritten relation can
    #: cast its bound parameters back to the types confirmation reported.
    type_oids: tuple[int, ...] = ()


def resolve_invocations(
    *,
    bindings: Sequence[tuple[str, str, tuple[object, ...]]],
    parameters: Sequence[object],
    connection: psycopg.Connection,
    search: object,
    embed: EmbeddingSource | None,
    settings: BridgeSettings,
) -> tuple[ResolvedInvocation, ...]:
    """Run every public-function invocation the statement contains."""
    resolved: list[ResolvedInvocation] = []
    for cte_name, function, raw_arguments in bindings:
        if function in SQL_NATIVE_FUNCTIONS:
            continue
        arguments = tuple(_bound(value, parameters) for value in raw_arguments)
        resolved.append(
            _resolve_one(
                cte_name=cte_name,
                function=function,
                arguments=arguments,
                connection=connection,
                search=search,
                embed=embed,
                settings=settings,
            )
        )
    return tuple(resolved)


def explain_placeholders(
    bindings: Sequence[tuple[str, str, tuple[object, ...]]],
) -> tuple[ResolvedInvocation, ...]:
    """Planable, empty stand-ins for each bridged invocation (§3.1 EXPLAIN).

    EXPLAIN asks what the planner would do, so nothing is nominated and nothing
    is confirmed — but the statement still has to be planable, and a call to a
    function PostgreSQL does not have is not. Each invocation becomes an empty
    relation of the shape that function publishes, which is what the planner
    needs and all it needs.
    """
    placeholders: list[ResolvedInvocation] = []
    for cte_name, function, raw_arguments in bindings:
        if function in SQL_NATIVE_FUNCTIONS:
            continue
        if function not in FUNCTION_TARGETS:
            raise SandboxRejection(
                code=QueryErrorCode.FUNCTION_NOT_ALLOWED,
                message=f"{function} is not a resolvable public function",
            )
        # A plan for a call that could not run is not a useful answer: the
        # arity is checked here too, so EXPLAIN and execution agree about
        # which statements are legal.
        if function == "fetch_chunk_bodies":
            if len(raw_arguments) != 1:
                raise SandboxRejection(
                    code=QueryErrorCode.INVALID_PARAMETER,
                    message="fetch_chunk_bodies takes exactly one uuid[] argument",
                )
        else:
            _check_arity(function=function, arguments=tuple(raw_arguments))
        if function == "fetch_chunk_bodies":
            columns, oids = BODY_COLUMNS, BODY_TYPE_OIDS
        else:
            target, _ = FUNCTION_TARGETS[function]
            columns, oids = published_contract(target)
        placeholders.append(
            ResolvedInvocation(
                cte_name=cte_name,
                columns=columns,
                rows=(),
                type_oids=oids,
                invocation=SemanticInvocation(
                    function=function,
                    nominated=0,
                    confirmed=0,
                    dropped_stale=0,
                    termination_reason="explained",
                ),
            )
        )
    return tuple(placeholders)


def _bound(value: object, parameters: Sequence[object]) -> object:
    """One argument: a literal, or the bound value of a `$n`, cast as written."""
    if not (isinstance(value, tuple) and value and value[0] == "$"):
        return value
    bound = parameters[value[1] - 1]
    casts = value[2] if len(value) > 2 else ()
    if isinstance(casts, tuple) and casts:
        from rememberstack.surfaces.query_sandbox.grammar import apply_cast

        return apply_cast(bound, casts)
    return bound


def _resolve_one(
    *,
    cte_name: str,
    function: str,
    arguments: tuple[object, ...],
    connection: psycopg.Connection,
    search: object,
    embed: EmbeddingSource | None,
    settings: BridgeSettings,
) -> ResolvedInvocation:
    if function not in FUNCTION_TARGETS:
        raise SandboxRejection(
            code=QueryErrorCode.FUNCTION_NOT_ALLOWED,
            message=f"{function} is not a resolvable public function",
        )
    target, channel = FUNCTION_TARGETS[function]
    if function == "fetch_chunk_bodies":
        return _resolve_bodies(
            cte_name=cte_name,
            arguments=arguments,
            connection=connection,
            search=search,
            settings=settings,
        )
    _check_arity(function=function, arguments=arguments)
    query_text = arguments[0] if arguments else None
    if not isinstance(query_text, str) or not query_text:
        raise SandboxRejection(
            code=QueryErrorCode.INVALID_PARAMETER,
            message=f"{function} takes a non-empty query string",
        )
    k = bounded_k(requested=arguments[1], settings=settings)
    filters = validate_filters(
        target=target, filters=arguments[2] if len(arguments) > 2 else None
    )
    # §3.4's generation pins: a caller may name the embedding-input policy and
    # the embedder generation, and an unavailable one fails rather than
    # falling forward to whatever the projection happens to hold.
    policy_generation = _optional_text(arguments, 3, "embedding_input_policy_version")
    embedder_generation = _optional_text(arguments, 4, "embedder_generation")
    if (policy_generation or embedder_generation) and channel != "semantic":
        raise SandboxRejection(
            code=QueryErrorCode.INVALID_PARAMETER,
            message="generation pins apply to the semantic channels only",
        )
    if (policy_generation or embedder_generation) and target != "chunks":
        # Only the chunk projection is stamped with a D80 generation triple.
        # Accepting a pin on a channel that cannot honour it — and then
        # disclosing it as though it had been applied — would tell the caller
        # their query was pinned when it was not.
        raise SandboxRejection(
            code=QueryErrorCode.GENERATION_UNAVAILABLE,
            message=(
                f"the {target} channel carries no D80 generation stamp,"
                " so it cannot be pinned to one"
            ),
        )

    if target == "chunks":
        # Resolve the generation pair FIRST, then embed under it. Embedding
        # before the pair is known means the query vector can come from a
        # different vector space than the stored vectors that are about to be
        # searched — which is not a worse search, it is a meaningless one.
        policy_generation, embedder_generation = _pinned_generations(
            connection=connection,
            settings=settings,
            policy_generation=policy_generation,
            embedder_generation=embedder_generation,
        )

    nominate = _nominator(
        function=function,
        target=target,
        search=search,
        embed=embed,
        settings=settings,
        policy_generation=policy_generation,
        embedder_generation=embedder_generation,
    )
    try:
        nominations = nominate(query_text, k, filters)
    except SandboxRejection:
        raise
    except LookupError as error:  # a pin the projection cannot honour
        raise SandboxRejection(
            code=QueryErrorCode.GENERATION_UNAVAILABLE, message=str(error)
        ) from error
    except Exception as error:  # the projection is a separate process
        raise SandboxRejection(
            code=QueryErrorCode.LANCE_UNAVAILABLE,
            message="the projection could not be searched",
        ) from error
    # A projection that returns more than it was asked for does not get to
    # spend the statement's budget: the cap is enforced here, not trusted.
    if len(nominations) > k:
        nominations = tuple(nominations)[:k]
    settings.nominations_used += len(nominations)

    try:
        confirmation = confirm(
            connection=connection,
            target=target,
            nominations=nominations,
            deployment_id=settings.deployment_id,
            filters=filters,
        )
    except psycopg.Error as error:
        raise SandboxRejection(
            code=QueryErrorCode.CONFIRMATION_FAILED,
            message="nominated rows could not be confirmed",
        ) from error

    body: BodyConfirmation | None = None
    if target == "chunks":
        # §3.4: the chunk channels and the body fetch share one body path, so
        # a nominated chunk carries its verified source text out with it
        # rather than making the caller ask a second time.
        confirmation, body = _hydrate(
            confirmation=confirmation, search=search, settings=settings
        )

    return ResolvedInvocation(
        cte_name=cte_name,
        columns=confirmation.columns,
        rows=tuple(confirmation.rows),
        type_oids=confirmation.type_oids,
        invocation=SemanticInvocation(
            function=function,
            nominated=len(nominations),
            confirmed=len(confirmation.rows),
            dropped_stale=confirmation.dropped_stale,
            dropped_filtered=confirmation.dropped_filtered,
            dropped_ambiguous=confirmation.dropped_ambiguous,
            dropped_absent=body.absent if body else 0,
            dropped_body_mismatch=body.mismatched if body else 0,
            dropped_absent_current=body.absent_current if body else 0,
            dropped_absent_projection=body.absent_projection if body else 0,
            dropped_hash_mismatch=body.mismatch_hash if body else 0,
            policy_generation=policy_generation,
            embedder_generation=embedder_generation,
            generation=embedder_generation or policy_generation,
            pg_confirmed_at=confirmation.pg_confirmed_at,
            termination_reason="completed",
        ),
    )


def _hydrate(
    *, confirmation: Confirmation, search: object, settings: BridgeSettings
) -> tuple[Confirmation, BodyConfirmation]:
    """Attach verified source text to already-confirmed chunk rows (§3.4).

    The metadata in `confirmation` came from PostgreSQL a moment ago in this
    same transaction, so it IS the confirmation: asking PostgreSQL a second
    time would only open a window in which the coordinate a body is verified
    against differs from the coordinate the row reports.
    """
    identifier = confirmation.columns.index("chunk_id")
    header = confirmation.columns.index("location_header")
    chunk_ids = tuple(str(row[identifier]) for row in confirmation.rows)
    if not chunk_ids:
        # An empty answer still answers with the chunk contract. A result whose
        # columns depend on how many rows survived is not a contract, and a
        # caller selecting `source_text` would fail on exactly the queries that
        # found nothing.
        return _with_body_column(confirmation, header, []), BodyConfirmation(
            rows=[], requested=0
        )
    texts = _chunk_texts(search=search, settings=settings, chunk_ids=chunk_ids)
    # The nomination row already carries the columns the body path verifies
    # against, in the order `_BODY_SQL` publishes them.
    hashes = confirmation.columns.index("embedding_text_hash")
    coordinates = {
        str(row[identifier]): (
            str(row[identifier]),
            *((None,) * 6),
            row[hashes],
            row[header],
        )
        for row in confirmation.rows
    }
    body = verify_bodies(
        requested=[(index, chunk_id) for index, chunk_id in enumerate(chunk_ids)],
        coordinates=coordinates,
        texts=texts,
        budget=settings,
        pg_confirmed_at=confirmation.pg_confirmed_at,
    )
    bodies = {chunk_ids[row[0]]: row[8] for row in body.rows}
    rows = [
        (*row[:header], bodies[str(row[identifier])], *row[header:])
        for row in confirmation.rows
        if str(row[identifier]) in bodies
    ]
    return _with_body_column(confirmation, header, rows), body


def _chunk_texts(
    *, search: object, settings: BridgeSettings, chunk_ids: Sequence[str]
) -> dict[str, Any]:
    """Projection bodies for ids PostgreSQL has already confirmed."""
    try:
        texts = search.chunk_texts(  # type: ignore[attr-defined]
            deployment_id=str(settings.deployment_id), chunk_ids=tuple(chunk_ids)
        )
    except Exception as error:  # the projection is a separate process
        raise SandboxRejection(
            code=QueryErrorCode.LANCE_UNAVAILABLE,
            message="chunk bodies could not be read",
        ) from error
    return {str(key): value for key, value in texts.items()}


def _with_body_column(
    confirmation: Confirmation, header: int, rows: list[tuple[Any, ...]]
) -> Confirmation:
    """The same confirmation with `source_text` in front of the D80 header."""
    return Confirmation(
        columns=(
            *confirmation.columns[:header],
            "source_text",
            *confirmation.columns[header:],
        ),
        rows=rows,
        type_oids=(
            *confirmation.type_oids[:header],
            25,
            *confirmation.type_oids[header:],
        ),
        dropped_stale=confirmation.dropped_stale,
        dropped_filtered=confirmation.dropped_filtered,
    )


#: How many arguments each §3.4 function takes: `query` and `k` are required,
#: the filter object and the two generation pins are not. An extra argument is
#: a caller misunderstanding, and silently ignoring it would hide the fact that
#: the request did not mean what it appeared to mean.
SIGNATURES: Final[dict[str, tuple[int, int]]] = {
    "semantic_claims": (2, 5),
    "semantic_chunks": (2, 5),
    "semantic_facts": (2, 5),
    "semantic_entities": (2, 3),
    "lexical_claims": (2, 3),
    "lexical_chunks": (2, 3),
}


def _check_arity(*, function: str, arguments: tuple[object, ...]) -> None:
    least, most = SIGNATURES[function]
    if not least <= len(arguments) <= most:
        raise SandboxRejection(
            code=QueryErrorCode.INVALID_PARAMETER,
            message=(
                f"{function} takes between {least} and {most} arguments,"
                f" {len(arguments)} given"
            ),
        )


def _pinned_generations(
    *,
    connection: psycopg.Connection,
    settings: BridgeSettings,
    policy_generation: str | None,
    embedder_generation: str | None,
) -> tuple[str | None, str | None]:
    """The one generation pair this search will run against.

    A fully pinned request is taken as written. An unpinned one takes the pair
    the spine currently stamps, so a corpus that has been re-embedded is
    searched under one vector space rather than all of them at once. A partial
    pin is completed only when the spine's current pair carries the pinned
    half; otherwise the caller has named something this surface cannot resolve
    on its own, and guessing which half they meant would be worse than saying
    so.
    """
    if policy_generation is not None and embedder_generation is not None:
        return policy_generation, embedder_generation
    try:
        current_policy, current_embedder = current_chunk_generations(
            connection=connection, deployment_id=settings.deployment_id
        )
    except psycopg.Error as error:
        raise SandboxRejection(
            code=QueryErrorCode.CONFIRMATION_FAILED,
            message="the current chunk generation could not be read",
        ) from error
    if policy_generation is None and embedder_generation is None:
        return current_policy, current_embedder
    if policy_generation is not None and current_policy == policy_generation:
        return policy_generation, current_embedder
    if embedder_generation is not None and current_embedder == embedder_generation:
        return current_policy, embedder_generation
    raise SandboxRejection(
        code=QueryErrorCode.GENERATION_UNAVAILABLE,
        message=(
            "a partial generation pin can only be completed from the"
            " generation the spine currently stamps; name both halves"
        ),
    )


def _optional_text(arguments: tuple[object, ...], index: int, name: str) -> str | None:
    """One optional string argument, or None when it was not supplied."""
    if len(arguments) <= index or arguments[index] is None:
        return None
    value = arguments[index]
    if not isinstance(value, str) or not value:
        raise SandboxRejection(
            code=QueryErrorCode.INVALID_PARAMETER,
            message=f"{name} must be a non-empty string when supplied",
        )
    return value


def _nominator(
    *,
    function: str,
    target: str,
    search: object,
    embed: EmbeddingSource | None,
    settings: BridgeSettings,
    policy_generation: str | None = None,
    embedder_generation: str | None = None,
) -> Callable[[str, int, dict[str, Any]], Sequence[Any]]:
    """Bind one function to its channel on the scored search port."""
    deployment = str(settings.deployment_id)

    def vector(query: str) -> tuple[float, ...]:
        # The executor refuses a semantic statement without an embedder, so
        # the None case is unreachable; it is here so the impossible case still
        # fails as a stated refusal rather than as an attribute error.
        if embed is None:
            raise SandboxRejection(
                code=QueryErrorCode.LANCE_UNAVAILABLE,
                message="no embedder is configured for this deployment",
            )
        if embedder_generation is None:
            return embed(query=query)
        # A pin names the generation the stored vectors were produced under.
        # Embedding the query with a different generation would compare two
        # vector spaces, so an embedder that cannot serve the pinned one is a
        # refusal rather than a silent substitution.
        try:
            return embed(query=query, embedder_generation=embedder_generation)
        except TypeError as error:
            raise SandboxRejection(
                code=QueryErrorCode.GENERATION_UNAVAILABLE,
                message=(
                    "this deployment's embedder cannot produce a query vector"
                    f" for embedder generation {embedder_generation!r}"
                ),
            ) from error

    def narrow(filters: dict[str, Any]) -> dict[str, str]:
        # Filters the projection understands are applied there, before top-k,
        # so a narrow search still fills its k with rows that match.
        return projection_filters(target=target, filters=filters)

    def semantic_claims(query: str, k: int, filters: dict[str, Any]):  # noqa: ANN202
        return search.search_claims_scored(  # type: ignore[attr-defined]
            deployment_id=deployment,
            vector=vector(query),
            k=k,
            current_only=True,
            equality_filters=narrow(filters),
        )

    def lexical_claims(query: str, k: int, filters: dict[str, Any]):  # noqa: ANN202
        return search.search_claims_lexical_scored(  # type: ignore[attr-defined]
            deployment_id=deployment,
            query=query,
            k=k,
            current_only=True,
            equality_filters=narrow(filters),
        )

    def semantic_chunks(query: str, k: int, filters: dict[str, Any]):  # noqa: ANN202
        return search.search_chunks_scored(  # type: ignore[attr-defined]
            deployment_id=deployment,
            vector=vector(query),
            k=k,
            policy_generation=policy_generation,
            embedder_generation=embedder_generation,
            equality_filters=narrow(filters),
        )

    def lexical_chunks(query: str, k: int, filters: dict[str, Any]):  # noqa: ANN202
        return search.search_chunks_lexical_scored(  # type: ignore[attr-defined]
            deployment_id=deployment,
            query=query,
            k=k,
            policy_generation=policy_generation,
            embedder_generation=embedder_generation,
            equality_filters=narrow(filters),
        )

    def semantic_facts(query: str, k: int, filters: dict[str, Any]):  # noqa: ANN202
        return search.search_facts_scored(  # type: ignore[attr-defined]
            deployment_id=deployment,
            vector=vector(query),
            k=k,
            kind=filters.get("fact_kind"),
        )

    def semantic_entities(query: str, k: int, filters: dict[str, Any]):  # noqa: ANN202
        return search.search_entities_scored(  # type: ignore[attr-defined]
            deployment_id=deployment,
            vector=vector(query),
            k=k,
            entity_type=filters.get("entity_type"),
        )

    return {
        "semantic_claims": semantic_claims,
        "lexical_claims": lexical_claims,
        "semantic_chunks": semantic_chunks,
        "lexical_chunks": lexical_chunks,
        "semantic_facts": semantic_facts,
        "semantic_entities": semantic_entities,
    }[function]


def substitute(
    sql: str, resolutions: Sequence[ResolvedInvocation]
) -> tuple[str, dict[str, Any]]:
    """Replace each generated CTE body with the rows it resolved to.

    The CTE keeps its name and its position, so the caller's statement is
    unchanged around it; only the call becomes data. An invocation that
    confirmed nothing becomes an empty relation with the right columns rather
    than disappearing, so the surrounding joins still type-check.
    """
    rewritten = sql
    bound: dict[str, Any] = {}
    for resolution in resolutions:
        marker = f"{resolution.cte_name} AS MATERIALIZED ("
        start = rewritten.find(marker)
        if start < 0:
            continue
        body_start = start + len(marker)
        index = _matching_paren(rewritten, body_start)
        relation, parameters = _values_relation(resolution)
        bound.update(parameters)
        rewritten = rewritten[:body_start] + relation + rewritten[index - 1 :]
    return rewritten, bound


def _matching_paren(sql: str, start: int) -> int:
    """The index just past the parenthesis that closes the one before `start`.

    A parenthesis inside a string literal or a quoted identifier is text, not
    structure: a caller may legitimately write `semantic_claims(\')\', 10)` or
    alias a call `AS "x)"`, and counting either as a nesting level would cut
    the CTE body in the wrong place.
    """
    depth = 1
    index = start
    quote = ""
    while index < len(sql) and depth:
        character = sql[index]
        if quote:
            if character == quote:
                # A doubled quote is an escaped quote, not the end.
                if index + 1 < len(sql) and sql[index + 1] == quote:
                    index += 1
                else:
                    quote = ""
        elif character in ("'", '"'):
            # Both kinds are opaque: `AS "x)"` is a legal alias, and its
            # parenthesis is part of a name rather than structure.
            quote = character
        elif character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
        index += 1
    return index


def _values_relation(resolution: ResolvedInvocation) -> tuple[str, dict[str, Any]]:
    """The confirmed rows as a relation whose values are bound, not rendered.

    Rendering a confirmed value into SQL text would make the statement's
    structure depend on the value's content — claim text is arbitrary prose
    that came out of a document, so an apostrophe or a parenthesis in a source
    sentence would be reparsed as syntax. Every value is therefore a bound
    parameter, cast to the type confirmation reported for its column, and the
    only thing this writes into the statement is placeholders.
    """
    aliases = ", ".join(f'"{column}"' for column in resolution.columns)
    oids = resolution.type_oids or (0,) * len(resolution.columns)
    casts = [f"::{_type_name(oid)}" for oid in oids]
    if not resolution.rows:
        nulls = ", ".join(f"NULL{cast}" for cast in casts)
        return f"SELECT * FROM (VALUES ({nulls})) AS t({aliases}) WHERE false", {}

    parameters: dict[str, Any] = {}
    rendered_rows: list[str] = []
    prefix = resolution.cte_name.strip("_")
    for row_index, row in enumerate(resolution.rows):
        placeholders: list[str] = []
        for column_index, value in enumerate(row):
            name = f"{prefix}_r{row_index}_c{column_index}"
            parameters[name] = value
            placeholders.append(f"%({name})s{casts[column_index]}")
        rendered_rows.append("(" + ", ".join(placeholders) + ")")
    relation = f"SELECT * FROM (VALUES {', '.join(rendered_rows)}) AS t({aliases})"
    return relation, parameters


#: The types confirmation can actually report for these views.
_OID_TYPES: Final[dict[int, str]] = {
    16: "boolean",
    20: "bigint",
    21: "smallint",
    23: "integer",
    25: "text",
    701: "double precision",
    1043: "varchar",
    1082: "date",
    1114: "timestamp",
    1184: "timestamptz",
    1700: "numeric",
    2950: "uuid",
    3802: "jsonb",
}


def _type_name(oid: int) -> str:
    """The cast a bound parameter needs to match its confirmed column.

    Without it PostgreSQL infers `unknown` for a placeholder inside VALUES,
    and a later comparison or join against the same column fails to plan.
    """
    return _OID_TYPES.get(oid, "text")


def _resolve_bodies(
    *,
    cte_name: str,
    arguments: tuple[object, ...],
    connection: psycopg.Connection,
    search: object,
    settings: BridgeSettings,
) -> ResolvedInvocation:
    """`fetch_chunk_bodies(chunk_ids)`: the body path minus nomination (§3.4)."""
    if len(arguments) != 1:
        raise SandboxRejection(
            code=QueryErrorCode.INVALID_PARAMETER,
            message="fetch_chunk_bodies takes exactly one uuid[] argument",
        )
    requested = chunk_id_list(arguments[0])
    try:
        coordinates, confirmed_at = confirm_chunk_coordinates(
            connection=connection,
            chunk_ids=[chunk_id for _, chunk_id in requested],
            deployment_id=settings.deployment_id,
        )
    except SandboxRejection:
        raise
    except psycopg.Error as error:
        raise SandboxRejection(
            code=QueryErrorCode.CONFIRMATION_FAILED,
            message="chunk coordinates could not be confirmed",
        ) from error
    # Only ids PostgreSQL still publishes are ever asked of the projection.
    texts = (
        _chunk_texts(search=search, settings=settings, chunk_ids=tuple(coordinates))
        if coordinates
        else {}
    )
    confirmation = verify_bodies(
        requested=requested,
        coordinates=coordinates,
        texts=texts,
        budget=settings,
        pg_confirmed_at=confirmed_at,
    )
    return ResolvedInvocation(
        cte_name=cte_name,
        columns=BODY_COLUMNS,
        rows=tuple(confirmation.rows),
        type_oids=BODY_TYPE_OIDS,
        invocation=SemanticInvocation(
            function="fetch_chunk_bodies",
            # §3.4: requested ids occupy the nomination-count slot; no new
            # QueryResult field is added for the body path.
            nominated=confirmation.requested,
            confirmed=len(confirmation.rows),
            dropped_stale=0,
            dropped_absent=confirmation.absent,
            dropped_body_mismatch=confirmation.mismatched,
            dropped_absent_current=confirmation.absent_current,
            dropped_absent_projection=confirmation.absent_projection,
            dropped_hash_mismatch=confirmation.mismatch_hash,
            pg_confirmed_at=confirmation.pg_confirmed_at,
            termination_reason="completed",
        ),
    )
