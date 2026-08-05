"""The Cypher pre-engine deny-scan: what must never reach the graph engine.

`Database(..., read_only=True)` already refuses every mutation form the pinned
engine implements — SET, DELETE, CREATE, MERGE — with a connection exception.
So this gate does not re-detect writes. What `read_only=True` does NOT stop is
the file, network, attachment, and extension family: those constructs must die
here, by name, before the engine sees the text.

The scan is a token scan, not a parser. It ignores text inside single quotes,
double quotes, backticks, `//` line comments, and `/* */` block comments — and
NOT `--`, which the pinned engine does not treat as a comment. Anything beyond
that (hop bounds, relationship brackets, graph-reference walking) is left to
the engine and to the executor's runtime caps.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from rememberstack.surfaces.query_sandbox.errors import QueryErrorCode
from rememberstack.surfaces.query_sandbox.errors import SandboxRejection

#: The clause keywords a read query may open with or contain. Reported in the
#: surface manifest so an agent can see what this surface is for; the scan
#: itself is a deny-list, not an allow-list.
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

#: File, network, attachment, and extension constructs. These are the only
#: keywords the gate refuses by name: they are exactly the family
#: `read_only=True` does not block. Mutations are not listed — the engine
#: refuses them itself, and the executor maps that refusal to
#: `cypher_not_allowed`.
REJECTED_KEYWORDS: Final = frozenset(
    {
        "copy",
        "load",
        "install",
        "uninstall",
        # `UPDATE fts` updates an extension and RUNS on a read-only database —
        # verified against the pinned engine. It is the same family as INSTALL
        # and was missing from the first list.
        "update",
        "attach",
        "import",
        "export",
        "call",
    }
)

#: The engine's v0.18.2 recursive upper bound. Recorded in the surface
#: manifest; no longer enforced by syntax analysis. Runtime caps (timeout,
#: row, byte) bound cost instead — see the Batch D implementation note.
RECURSIVE_HOPS_MAX: Final = 30

#: One statement per request. A script is not a query.
_STATEMENT_SEPARATOR: Final = ";"


@dataclass(frozen=True)
class CypherStatement:
    """One accepted statement the surface is willing to pass to the engine."""

    text: str


def validate_cypher(text: str) -> CypherStatement:
    """Accept one statement that does not name a denied construct.

    Raises `SandboxRejection` with `CYPHER_NOT_ALLOWED` for a construct the
    surface refuses to pass on, and `CYPHER_PARSE_ERROR` for text that is not
    one statement at all (empty, or cut off inside a quoted run).
    """
    if not text or not text.strip():
        raise SandboxRejection(
            code=QueryErrorCode.CYPHER_PARSE_ERROR, message="the statement is empty"
        )
    words, statements = _scan(text)
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
    return CypherStatement(text=text.strip())


def _scan(text: str) -> tuple[set[str], int]:
    """Split the statement into lowercased words and a statement count.

    String literals, backtick-quoted identifiers, and comments contribute
    nothing: a keyword inside quoted prose is data, and a keyword inside a
    comment is not part of the query. Everything else contributes its words so
    the deny-list can be applied by name.

    The comment forms recognised here are exactly the ones the pinned engine
    recognises — `//` and `/* */`, and NOT `--`. Skipping a form the engine
    does not skip would make this scan blind to text the engine goes on to
    parse, which is the one direction a gate must never be wrong in.
    """
    words: set[str] = set()
    statements = 1
    word = ""
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
        if text.startswith("//", index):
            newline = text.find("\n", index)
            index = length if newline < 0 else newline + 1
            continue
        if character.isalnum() or character == "_":
            word += character
            index += 1
            continue
        if word:
            words.add(word.lower())
            word = ""
        if character == _STATEMENT_SEPARATOR and _has_statement_after(text, index + 1):
            statements += 1
        index += 1
    if word:
        words.add(word.lower())
    return words, statements


def _has_statement_after(text: str, start: int) -> bool:
    """Whether anything but whitespace and comments follows a `;`.

    A terminal semicolon with a trailing comment is one statement, and the
    engine reads it as one. Counting the comment as a second statement refused
    a legal query.
    """
    index = start
    length = len(text)
    while index < length:
        character = text[index]
        if character.isspace():
            index += 1
            continue
        if text.startswith("/*", index):
            closing = text.find("*/", index + 2)
            index = length if closing < 0 else closing + 2
            continue
        if text.startswith("//", index):
            newline = text.find("\n", index)
            index = length if newline < 0 else newline + 1
            continue
        return True
    return False


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
