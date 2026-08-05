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
from datetime import datetime
from typing import Any
from typing import Final
from typing import Protocol
from uuid import UUID

import psycopg

from rememberstack.surfaces.query_sandbox.errors import QueryErrorCode
from rememberstack.surfaces.query_sandbox.errors import SandboxRejection
from rememberstack.surfaces.query_sandbox.nomination import bounded_k
from rememberstack.surfaces.query_sandbox.nomination import BridgeSettings
from rememberstack.surfaces.query_sandbox.nomination import confirm
from rememberstack.surfaces.query_sandbox.nomination import validate_filters
from rememberstack.surfaces.query_sandbox.result import SemanticInvocation

#: Which confirmation target and channel each public function answers with.
FUNCTION_TARGETS: Final[dict[str, tuple[str, str]]] = {
    "semantic_claims": ("claims", "semantic"),
    "lexical_claims": ("claims", "bm25"),
    "semantic_chunks": ("chunks", "semantic"),
    "lexical_chunks": ("chunks", "bm25"),
    "semantic_facts": ("facts", "semantic"),
    "semantic_entities": ("entities", "semantic"),
}


class EmbeddingSource(Protocol):
    """Whatever turns a query string into the P1 vector space."""

    def __call__(self, *, query: str) -> tuple[float, ...]: ...


@dataclass(frozen=True)
class ResolvedInvocation:
    """One invocation's confirmed rows plus the disclosure it earned."""

    cte_name: str
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    invocation: SemanticInvocation


def resolve_invocations(
    *,
    bindings: Sequence[tuple[str, str, tuple[object, ...]]],
    parameters: Sequence[object],
    connection: psycopg.Connection,
    search: object,
    embed: EmbeddingSource,
    settings: BridgeSettings,
) -> tuple[ResolvedInvocation, ...]:
    """Run every public-function invocation the statement contains."""
    resolved: list[ResolvedInvocation] = []
    for cte_name, function, raw_arguments in bindings:
        arguments = tuple(
            parameters[value[1] - 1]
            if isinstance(value, tuple) and value and value[0] == "$"
            else value
            for value in raw_arguments
        )
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


def _resolve_one(
    *,
    cte_name: str,
    function: str,
    arguments: tuple[object, ...],
    connection: psycopg.Connection,
    search: object,
    embed: EmbeddingSource,
    settings: BridgeSettings,
) -> ResolvedInvocation:
    if function not in FUNCTION_TARGETS:
        raise SandboxRejection(
            code=QueryErrorCode.FUNCTION_NOT_ALLOWED,
            message=f"{function} is not a resolvable public function",
        )
    target, channel = FUNCTION_TARGETS[function]
    query_text = arguments[0] if arguments else None
    if not isinstance(query_text, str) or not query_text:
        raise SandboxRejection(
            code=QueryErrorCode.INVALID_PARAMETER,
            message=f"{function} takes a non-empty query string",
        )
    k = bounded_k(
        requested=arguments[1] if len(arguments) > 1 else None, settings=settings
    )
    filters = validate_filters(
        target=target, filters=arguments[2] if len(arguments) > 2 else None
    )

    nominate = _nominator(
        function=function, search=search, embed=embed, settings=settings
    )
    try:
        nominations = nominate(query_text, k, filters)
    except SandboxRejection:
        raise
    except Exception as error:  # the projection is a separate process
        raise SandboxRejection(
            code=QueryErrorCode.LANCE_UNAVAILABLE,
            message="the projection could not be searched",
        ) from error
    settings.nominations_used += len(nominations)

    try:
        columns, rows, dropped = confirm(
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

    return ResolvedInvocation(
        cte_name=cte_name,
        columns=columns,
        rows=tuple(rows),
        invocation=SemanticInvocation(
            function=function,
            nominated=len(nominations),
            confirmed=len(rows),
            dropped_stale=dropped,
            generation=None,
            termination_reason="completed",
        ),
    )


def _nominator(
    *, function: str, search: object, embed: EmbeddingSource, settings: BridgeSettings
) -> Callable[[str, int, dict[str, Any]], Sequence[Any]]:
    """Bind one function to its channel on the scored search port."""
    deployment = str(settings.deployment_id)

    def semantic_claims(query: str, k: int, filters: dict[str, Any]):  # noqa: ANN202
        return search.search_claims_scored(  # type: ignore[attr-defined]
            deployment_id=deployment, vector=embed(query=query), k=k, current_only=True
        )

    def lexical_claims(query: str, k: int, filters: dict[str, Any]):  # noqa: ANN202
        return search.search_claims_lexical_scored(  # type: ignore[attr-defined]
            deployment_id=deployment, query=query, k=k, current_only=True
        )

    def semantic_chunks(query: str, k: int, filters: dict[str, Any]):  # noqa: ANN202
        return search.search_chunks_scored(  # type: ignore[attr-defined]
            deployment_id=deployment, vector=embed(query=query), k=k
        )

    def lexical_chunks(query: str, k: int, filters: dict[str, Any]):  # noqa: ANN202
        return search.search_chunks_lexical_scored(  # type: ignore[attr-defined]
            deployment_id=deployment, query=query, k=k
        )

    def semantic_facts(query: str, k: int, filters: dict[str, Any]):  # noqa: ANN202
        return search.search_facts_scored(  # type: ignore[attr-defined]
            deployment_id=deployment,
            vector=embed(query=query),
            k=k,
            kind=filters.get("fact_kind"),
        )

    def semantic_entities(query: str, k: int, filters: dict[str, Any]):  # noqa: ANN202
        return search.search_entities_scored(  # type: ignore[attr-defined]
            deployment_id=deployment,
            vector=embed(query=query),
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


def substitute(sql: str, resolutions: Sequence[ResolvedInvocation]) -> str:
    """Replace each generated CTE body with the rows it resolved to.

    The CTE keeps its name and its position, so the caller's statement is
    unchanged around it; only the call becomes data. An invocation that
    confirmed nothing becomes an empty relation with the right columns rather
    than disappearing, so the surrounding joins still type-check.
    """
    rewritten = sql
    for resolution in resolutions:
        marker = f"__srf_{resolution.cte_name.rsplit('_', 1)[-1]} AS MATERIALIZED ("
        start = rewritten.find(marker)
        if start < 0:
            continue
        body_start = start + len(marker)
        depth = 1
        index = body_start
        while index < len(rewritten) and depth:
            if rewritten[index] == "(":
                depth += 1
            elif rewritten[index] == ")":
                depth -= 1
            index += 1
        rewritten = (
            rewritten[:body_start]
            + _values_relation(resolution)
            + rewritten[index - 1 :]
        )
    return rewritten


def _values_relation(resolution: ResolvedInvocation) -> str:
    """The confirmed rows as an inline relation with the contract's columns."""
    aliases = ", ".join(f'"{column}"' for column in resolution.columns)
    if not resolution.rows:
        nulls = ", ".join("NULL" for _ in resolution.columns)
        return f"SELECT * FROM (VALUES ({nulls})) AS t({aliases}) WHERE false"
    literals = ", ".join(
        "(" + ", ".join(_literal(value) for value in row) + ")"
        for row in resolution.rows
    )
    return f"SELECT * FROM (VALUES {literals}) AS t({aliases})"


def _literal(value: object) -> str:
    """One confirmed value as SQL text.

    Every value here came out of PostgreSQL a moment ago in the same
    transaction; quoting is still explicit because the rewritten text is
    parsed again.
    """
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (UUID, datetime)):
        return "'" + str(value).replace("'", "''") + "'"
    return "'" + str(value).replace("'", "''") + "'"
