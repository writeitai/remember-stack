"""The Cypher pre-engine deny-scan: what must never reach the graph engine.

`Database(..., read_only=True)` already refuses every mutation form the pinned
engine implements — SET, DELETE, CREATE, MERGE — with a connection exception,
so this gate does not re-detect writes. What `read_only=True` does NOT stop is
everything else the engine will happily run: session control
(`BEGIN TRANSACTION READ ONLY` leaves the shared connection in an open
transaction across requests), maintenance (`ANALYZE`, `CHECKPOINT`), database
switching (`USE`, `DETACH`), extension management (`INSTALL`, `UPDATE`), and
the file and attachment family (`COPY`, `LOAD`, `ATTACH`).

Two successive deny-lists failed to name all of that — the first missed
`UPDATE`, the second missed the whole session and maintenance family — so the
gate no longer denies by name. A read statement can only BEGIN one of five
ways, and requiring that refuses everything else at once, including the
constructs nobody here has thought of. It also stops refusing ordinary reads
that merely use one of those words as an alias.

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
#: How a read statement may begin. Everything else — session control, engine
#: maintenance, extension management, file and attachment constructs, and any
#: form nobody here has considered — is refused by not being on this list,
#: which is the only way to refuse what has not been thought of.
READ_OPENINGS: Final = frozenset({"match", "optional", "with", "unwind", "return"})

#: Retained for the surface manifest, which publishes what this gate refuses by
#: name. Enforcement is by `READ_OPENINGS` above.
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
    opening, statements = _scan(text)
    if statements > 1:
        raise SandboxRejection(
            code=QueryErrorCode.CYPHER_NOT_ALLOWED,
            message="one statement per request; a script is not a query",
        )
    # Default-deny on the OPENING, rather than a list of denied keywords.
    #
    # A deny list only refuses what somebody remembered. The first version
    # missed `UPDATE`; the second still missed `BEGIN TRANSACTION READ ONLY`
    # (which leaves the shared connection in an open transaction across
    # requests), `ANALYZE`, `CHECKPOINT`, `USE` and `DETACH` — all of which the
    # engine runs. It also refused ordinary reads that merely USED a denied
    # word as an alias, like `RETURN 1 AS update`.
    #
    # A read statement can only begin one of five ways. Requiring that refuses
    # every session, maintenance, extension and file construct at once —
    # including the ones nobody has thought of — and stops punishing a caller
    # for their choice of alias.
    if opening not in READ_OPENINGS:
        raise SandboxRejection(
            code=QueryErrorCode.CYPHER_NOT_ALLOWED,
            message=(
                f"a read statement begins with {', '.join(sorted(READ_OPENINGS))};"
                f" {opening or 'this'} is not something this surface runs"
            ),
        )
    return CypherStatement(text=text.strip())


def _scan(text: str) -> tuple[str, int]:
    """The statement's opening word, lowercased, and how many statements it is.

    String literals, backtick-quoted identifiers, and comments contribute
    nothing: a keyword inside quoted prose is data, and a keyword inside a
    comment is not part of the query, so neither can masquerade as the opening.

    The comment forms recognised here are exactly the ones the pinned engine
    recognises — `//` and `/* */`, and NOT `--`. Skipping a form the engine
    does not skip would make this scan blind to text the engine goes on to
    parse, which is the one direction a gate must never be wrong in.
    """
    opening = ""
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
            opening = opening or word.lower()
            word = ""
        if character == _STATEMENT_SEPARATOR and _has_statement_after(text, index + 1):
            statements += 1
        index += 1
    if word:
        opening = opening or word.lower()
    return opening, statements


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
