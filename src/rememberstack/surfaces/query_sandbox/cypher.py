"""The Cypher read gate: what the graph engine is allowed to be asked (§3.5).

Two controls, in this order, and the order is the whole point.

First, a token scan over the statement text rejects every mutation and
external-action construct — `CREATE`, `SET`, `DELETE`, `CALL`, `LOAD`,
`INSTALL`, `COPY`, `ATTACH`, and the rest — before the engine sees the text at
all. This is the MANDATORY control. The serving worker opens the snapshot with
``read_only=True``, which blocks writes but does NOT block file, network,
attachment, or extension actions, so a construct that reaches the engine is a
gate failure rather than something the engine will catch.

Second, the pinned dialect itself decides whether what remains is a query it
implements. That answer comes from the engine's own parser (a compile, never an
execution), so the surface never has to reimplement, or drift from, the
dialect: syntax the pinned engine does not implement fails
`cypher_parse_error` instead of being silently rewritten into some other query.

The scan is a scan, not a parser, and it is deliberately blunt: a rejected
keyword anywhere outside a string literal, a comment, or a backtick-quoted
identifier fails the statement. A caller who names a variable `create` is
inconvenienced; a caller who hides `CREATE` in a `UNION` arm, behind a comment
boundary, or inside a subquery is stopped. Blunt in that direction is correct.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from rememberstack.surfaces.query_sandbox.errors import QueryErrorCode
from rememberstack.surfaces.query_sandbox.errors import SandboxRejection

#: The clause keywords a read query may open with or contain. Everything else
#: that looks like a clause keyword is rejected by name below; this list exists
#: so the rejection message can say what IS allowed.
READ_CLAUSES: Final = frozenset(
    {
        "match",
        "optional",
        "where",
        "with",
        "return",
        "unwind",
        "union",
        "all",
        "distinct",
        "order",
        "by",
        "skip",
        "limit",
        "as",
        "and",
        "or",
        "xor",
        "not",
        "in",
        "is",
        "null",
        "true",
        "false",
        "case",
        "when",
        "then",
        "else",
        "end",
        "exists",
        "count",
        "starts",
        "ends",
        "contains",
        "asc",
        "ascending",
        "desc",
        "descending",
    }
)

#: Mutation. The snapshot is a published read projection; nothing writes to it
#: through this surface, and `read_only=True` beneath is defense, not the rule.
_MUTATION: Final = frozenset(
    {"create", "merge", "set", "delete", "detach", "remove", "insert"}
)

#: External action: file, network, attachment, extension, and procedure paths.
#: These are the constructs `read_only=True` does NOT stop, which is exactly
#: why they must die here.
_EXTERNAL: Final = frozenset(
    {
        "call",
        "load",
        "copy",
        "import",
        "export",
        "attach",
        "detach_database",
        "install",
        "force",
        "extension",
        "extract",
    }
)

#: Schema, catalog, and engine-maintenance statements.
_ADMINISTRATIVE: Final = frozenset(
    {
        "alter",
        "drop",
        "comment",
        "analyze",
        "checkpoint",
        "begin",
        "commit",
        "rollback",
        "transaction",
        "use",
        "profile",
        "explain",
        "macro",
        "sequence",
        "index",
        "constraint",
        "grant",
        "revoke",
        "prepare",
        "execute",
        "update",
    }
)

REJECTED_KEYWORDS: Final = _MUTATION | _EXTERNAL | _ADMINISTRATIVE

#: The engine's v0.18.2 recursive upper bound, and an executor hard cap: a
#: request for more hops is refused rather than quietly clamped, because a
#: silently shortened traversal answers a different question.
RECURSIVE_HOPS_MAX: Final = 30

#: One statement per request. A script is not a query.
_STATEMENT_SEPARATOR: Final = ";"


@dataclass(frozen=True)
class CypherStatement:
    """One accepted read statement and what the gate observed in it."""

    text: str
    keywords: frozenset[str]
    max_hops: int | None


def validate_cypher(text: str) -> CypherStatement:
    """Accept one read statement, or reject it by name.

    Raises `SandboxRejection` with `CYPHER_NOT_ALLOWED` for a construct the
    surface refuses to pass on, and `CYPHER_PARSE_ERROR` for text that is not
    one statement at all.
    """
    if not text or not text.strip():
        raise SandboxRejection(
            code=QueryErrorCode.CYPHER_PARSE_ERROR, message="the statement is empty"
        )
    words, statements, hops = _scan(text)
    if statements > 1:
        raise SandboxRejection(
            code=QueryErrorCode.CYPHER_NOT_ALLOWED,
            message="one statement per request; a script is not a query",
        )
    rejected = sorted(words & REJECTED_KEYWORDS)
    if rejected:
        raise SandboxRejection(
            code=QueryErrorCode.CYPHER_NOT_ALLOWED,
            message=(
                f"{rejected[0]} is not a read construct;"
                " this surface reads the published graph and never changes it,"
                " loads from it, or calls out of it"
            ),
        )
    if not ({"match", "unwind", "return"} & words):
        raise SandboxRejection(
            code=QueryErrorCode.CYPHER_PARSE_ERROR,
            message="a read statement opens with MATCH, UNWIND, or RETURN",
        )
    if hops is not None and hops > RECURSIVE_HOPS_MAX:
        raise SandboxRejection(
            code=QueryErrorCode.RESOURCE_LIMIT,
            message=(
                f"a variable-length pattern may span at most {RECURSIVE_HOPS_MAX}"
                " hops, which is the pinned engine's own bound"
            ),
        )
    return CypherStatement(text=text.strip(), keywords=frozenset(words), max_hops=hops)


def _scan(text: str) -> tuple[set[str], int, int | None]:
    """Split the statement into bare words, statement count, and hop bound.

    String literals, backtick-quoted identifiers, and comments contribute
    nothing: a keyword inside quoted prose is data, and a keyword inside a
    comment is not part of the query. Everything else contributes its words,
    lowercased, so the reject list can be applied by name.
    """
    words: set[str] = set()
    statements = 1
    max_hops: int | None = None
    word = ""
    digits = ""
    in_range = False
    index = 0
    length = len(text)
    while index < length:
        character = text[index]
        if character in ("'", '"', "`"):
            index = _skip_quoted(text, index)
            word = ""
            continue
        if character == "/" and text.startswith("/*", index):
            closing = text.find("*/", index + 2)
            index = length if closing < 0 else closing + 2
            continue
        if text.startswith("//", index) or text.startswith("--", index):
            newline = text.find("\n", index)
            index = length if newline < 0 else newline + 1
            continue
        if character.isalnum() or character == "_":
            if character.isdigit() and in_range:
                digits += character
            word += character
            index += 1
            continue
        if word:
            words.add(word.lower())
            word = ""
        if character == "*":
            # `*1..3` or `*..5`: the variable-length bound follows.
            in_range = True
            digits = ""
        elif in_range and character not in (".", " "):
            if digits:
                max_hops = max(max_hops or 0, int(digits))
            in_range = False
            digits = ""
        elif character == ".":
            if digits:
                max_hops = max(max_hops or 0, int(digits))
                digits = ""
        if character == _STATEMENT_SEPARATOR and text[index + 1 :].strip():
            statements += 1
        index += 1
    if word:
        words.add(word.lower())
    if in_range and digits:
        max_hops = max(max_hops or 0, int(digits))
    return words, statements, max_hops


def _skip_quoted(text: str, start: int) -> int:
    """The index just past the quoted run beginning at `start`."""
    quote = text[start]
    index = start + 1
    while index < len(text):
        character = text[index]
        if character == "\\" and quote != "`":
            index += 2
            continue
        if character == quote:
            return index + 1
        index += 1
    raise SandboxRejection(
        code=QueryErrorCode.CYPHER_PARSE_ERROR,
        message="the statement ends inside a quoted run",
    )
