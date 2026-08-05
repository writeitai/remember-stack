"""The Cypher pre-engine deny-scan: what must never reach the graph engine.

`Database(..., read_only=True)` already refuses every mutation form the pinned
engine implements — SET, DELETE, CREATE, MERGE — with a connection exception,
so this gate does not re-detect writes. What `read_only=True` does NOT stop is
everything else the engine will happily run: session control
(`BEGIN TRANSACTION READ ONLY` leaves the shared connection in an open
transaction across requests), maintenance (`ANALYZE`, `CHECKPOINT`), database
switching (`USE`, `DETACH`), extension management (`INSTALL`, `UPDATE`), and
the file and attachment family (`COPY`, `LOAD`, `ATTACH`).

Two controls divide that job. A read statement can only BEGIN one of five
ways, so the opening is default-deny. The external-action, session,
maintenance, attachment, and plan-control family is also rejected wherever a
token appears, so `MATCH ... CALL/LOAD/...` cannot hide a forbidden action
behind an accepted opening. This is deliberately lexical and conservative;
the pinned engine remains the syntax authority.

The scan is a token scan, not a parser. It ignores text inside single quotes,
double quotes, backticks, `//` line comments, and `/* */` block comments — and
NOT `--`, which the pinned engine does not treat as a comment. The sole
backtick exception is a quoted identifier immediately followed by `(`, because
the pinned engine accepts quoted function names. Anything beyond that (hop
bounds, relationship brackets, graph-reference walking) is left to the engine
and to the executor's runtime caps.
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

#: How a read statement may begin. Everything else — session control, engine
#: maintenance, extension management, file and attachment constructs, and any
#: form nobody here has considered — is refused by not being on this list,
#: which is the only way to refuse what has not been thought of.
READ_OPENINGS: Final = frozenset({"match", "optional", "with", "unwind", "return"})

# Mutations are deliberately left to LadybugDB's read-only authority. They are
# still part of the published dialect contract because callers need to know
# which accepted-opening statements the engine itself will refuse.
ENGINE_REJECTED_MUTATIONS: Final = frozenset({"create", "delete", "merge", "set"})

#: Constructs `read_only=True` does not reliably stop, rejected wherever they
#: appear outside data/identifier quotes and comments. Mutations are not listed:
#: the engine refuses them, and the executor maps that refusal to
#: `cypher_not_allowed`.
REJECTED_KEYWORDS: Final = frozenset(
    {
        "analyze",
        "attach",
        "begin",
        "call",
        "checkpoint",
        "commit",
        "copy",
        "detach",
        "explain",
        "export",
        "force",
        "import",
        "install",
        "load",
        "profile",
        "rollback",
        "transaction",
        "uninstall",
        # `UPDATE fts` updates an extension and RUNS on a read-only database —
        # verified against the pinned engine. It is the same family as INSTALL
        # and was missing from the first list.
        "update",
        "use",
    }
)

#: The engine's v0.18.2 recursive upper bound. Recorded in the surface
#: manifest; no longer enforced by syntax analysis. Runtime caps (timeout,
#: row, byte) bound cost instead — see the Batch D implementation note.
RECURSIVE_HOPS_MAX: Final = 30

#: Pinned-engine functions observed to expose, construct, derive, or erase the
#: type of physical graph addresses. Some are general conversions, but the
#: engine exposes no input-type AST with which to admit only their safe calls;
#: refusing these exact names is the narrow fail-closed boundary.
REJECTED_FUNCTIONS: Final = frozenset(
    {"cast", "hash", "id", "internal_id", "offset", "rowid", "string", "to_string"}
)

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
    opening, statements, tokens, function_calls = _scan(text)
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
    rejected = tokens & REJECTED_KEYWORDS
    if rejected:
        construct = sorted(rejected)[0]
        raise SandboxRejection(
            code=QueryErrorCode.CYPHER_NOT_ALLOWED,
            message=f"{construct} is not available on the read surface",
        )
    rejected_functions = function_calls & REJECTED_FUNCTIONS
    if rejected_functions:
        function = sorted(rejected_functions)[0]
        raise SandboxRejection(
            code=QueryErrorCode.CYPHER_NOT_ALLOWED,
            message=(
                f"{function}(...) is unavailable because it can expose"
                " engine-internal graph identifiers"
            ),
        )
    return CypherStatement(text=text.strip())


def _scan(text: str) -> tuple[str, int, frozenset[str], frozenset[str]]:
    """Return the opening, statement count, tokens, and unquoted function calls.

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
    tokens: set[str] = set()
    function_calls: set[str] = set()
    pending_word = ""
    word = ""
    index = 0
    length = len(text)
    while index < length:
        character = text[index]
        if character in ("'", '"', "`"):
            closing = _skip_quoted(text, index)
            pending_word = (
                text[index + 1 : closing - 1].lower() if character == "`" else ""
            )
            index = closing
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
            token = word.lower()
            opening = opening or token
            tokens.add(token)
            pending_word = token
            word = ""
        if character == "(":
            if pending_word:
                function_calls.add(pending_word)
            pending_word = ""
        elif not character.isspace():
            pending_word = ""
        if character == _STATEMENT_SEPARATOR and _has_statement_after(text, index + 1):
            statements += 1
        index += 1
    if word:
        token = word.lower()
        opening = opening or token
        tokens.add(token)
    return opening, statements, frozenset(tokens), frozenset(function_calls)


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
