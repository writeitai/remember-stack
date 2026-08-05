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
        "uninstall",
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

#: The engine's recursive traversal modes (§3.5). They appear between the `*`
#: and the hop range, and are stepped over when the bound is read.
_RECURSIVE_MODES: Final = frozenset(
    {"shortest", "all", "wshortest", "trail", "acyclic"}
)

#: One statement per request. A script is not a query.
_STATEMENT_SEPARATOR: Final = ";"


#: The graph types the published projection contract defines (§3.5). A label
#: the statement names that is not one of these is simply not in the graph, so
#: it is not reported as a reference to it.
PROJECTION_TYPES: Final = frozenset(
    {"Entity", "Document", "RELATES", "MENTIONED_IN", "DOC_CROSSREF", "IS_DOCUMENT"}
)


@dataclass(frozen=True)
class CypherStatement:
    """One accepted read statement and what the gate observed in it."""

    text: str
    keywords: frozenset[str]
    max_hops: int | None
    graph_types: tuple[str, ...] = ()
    graph_properties: tuple[str, ...] = ()


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
    words, statements, hops, unbounded = _scan(text)
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
    if unbounded:
        # The engine happily runs `*` and `*1..`; only an explicit upper bound
        # over its own limit is refused by its binder. A gate that claims a
        # hop cap has to refuse the forms that state no bound at all, or the
        # cap is advisory — and this runs in the API process.
        raise SandboxRejection(
            code=QueryErrorCode.RESOURCE_LIMIT,
            message=(
                "a variable-length pattern must state an upper bound of at"
                f" most {RECURSIVE_HOPS_MAX} hops"
            ),
        )
    if hops is not None and hops > RECURSIVE_HOPS_MAX:
        raise SandboxRejection(
            code=QueryErrorCode.RESOURCE_LIMIT,
            message=(
                f"a variable-length pattern may span at most {RECURSIVE_HOPS_MAX}"
                " hops, which is the pinned engine's own bound"
            ),
        )
    types, properties = _references(text)
    return CypherStatement(
        text=text.strip(),
        keywords=frozenset(words),
        max_hops=hops,
        graph_types=types,
        graph_properties=properties,
    )


def _references(text: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The projection types and properties this statement names (§4.4).

    Reported so a caller can see which part of the projection contract their
    answer depended on: a statement reading `RELATES.confidence` is tied to a
    property that contract may change, and that is worth seeing without
    re-reading the query.

    Read by walking the statement rather than by matching patterns over the raw
    text, because the raw text includes prose: `RETURN ':Entity .name'` names
    neither a type nor a property, and a property map's keys ARE properties
    even though no dot precedes them. A regex got both of those wrong.
    """
    labels: set[str] = set()
    properties: set[str] = set()
    word = ""
    index = 0
    length = len(text)
    pending = ""  # the symbol that introduced the word being read
    in_property_map = 0
    while index <= length:
        character = text[index] if index < length else " "
        if index < length and character in ("'", '"', "`"):
            index = _skip_quoted(text, index)
            word = ""
            pending = ""
            continue
        if index < length and text.startswith("/*", index):
            closing = text.find("*/", index + 2)
            index = length if closing < 0 else closing + 2
            continue
        if index < length and text.startswith("//", index):
            newline = text.find("\n", index)
            index = length if newline < 0 else newline + 1
            continue
        if character.isalnum() or character == "_":
            word += character
            index += 1
            continue
        if word:
            if pending == ":" and word in PROJECTION_TYPES:
                labels.add(word)
            elif pending == "." or (in_property_map and character == ":"):
                properties.add(word)
            word = ""
        if character == "{":
            in_property_map += 1
        elif character == "}":
            in_property_map = max(0, in_property_map - 1)
        if not character.isspace():
            pending = character
        index += 1
    return tuple(sorted(labels)), tuple(sorted(properties))


def _scan(text: str) -> tuple[set[str], int, int | None, bool]:
    """Split the statement into words, statement count, hop bound, and whether
    any variable-length pattern was left unbounded.

    String literals, backtick-quoted identifiers, and comments contribute
    nothing: a keyword inside quoted prose is data, and a keyword inside a
    comment is not part of the query. Everything else contributes its words,
    lowercased, so the reject list can be applied by name.

    The comment forms recognised here are exactly the ones the pinned engine
    recognises — `//` and `/* */`, and NOT `--`. Skipping a form the engine
    does not skip would make this scan blind to text the engine goes on to
    parse, which is the one direction a gate must never be wrong in. That every
    such statement happens to fail the engine's parser today is a property of
    this dialect, not a guarantee worth depending on.

    A `*` is only read as a variable-length bound inside a relationship
    pattern's brackets. Outside them it is `count(*)` or multiplication, and
    treating those as traversals would refuse ordinary queries.
    """
    words: set[str] = set()
    statements = 1
    max_hops: int | None = None
    unbounded = False
    word = ""
    brackets = 0
    # The last character that was part of the query, skipping whitespace,
    # comments, and quoted runs — which is what "the character before this one"
    # has to mean in a scan that steps over all three.
    last_symbol = ""
    # A relationship pattern can carry a property map `{...}` or an inline
    # recursive predicate `(r, n | WHERE ...)`, and inside those a `*` is
    # multiplication. Only a `*` in the pattern itself is a hop bound.
    nested = 0
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
            last_symbol = character
            index += 1
            continue
        if word:
            words.add(word.lower())
            word = ""
        # What came BEFORE this character, captured before it becomes the
        # previous one itself.
        preceding = last_symbol
        if not character.isspace():
            last_symbol = character
        if character == "[":
            # A relationship bracket sits INSIDE a pattern arrow: `-[r:T]->`,
            # `<-[r]-`. Both sides are required. The dash alone is not enough —
            # `1 - [2 * 31][1]` is subtraction of a list element — and looking
            # only at raw characters is not enough either, because a comment
            # can sit between the dash and the bracket. Every other `[` opens a
            # list, where `*` is multiplication.
            if _is_relationship_bracket(text, index, previous=preceding):
                brackets += 1
                nested = 0
            elif brackets:
                nested += 1
        elif character == "]":
            if nested:
                nested -= 1
            else:
                brackets = max(0, brackets - 1)
        elif brackets and character in ("{", "("):
            nested += 1
        elif brackets and character in ("}", ")"):
            nested = max(0, nested - 1)
        elif character == "*" and brackets and not nested:
            upper, index = _range_upper_bound(text, index + 1)
            if upper is None:
                unbounded = True
            else:
                max_hops = max(max_hops or 0, upper)
            continue
        if character == _STATEMENT_SEPARATOR and _has_statement_after(text, index + 1):
            statements += 1
        index += 1
    if word:
        words.add(word.lower())
    return words, statements, max_hops, unbounded


def _range_upper_bound(text: str, start: int) -> tuple[int | None, int]:
    """The upper bound of a variable-length range, and where it ends.

    `*3` is three, `*1..5` and `*..5` are five. `*` alone and `*1..` state no
    upper bound at all — and the engine runs those, so they cannot be read as
    "some small number". They return None, which the caller refuses.

    §3.5 allows the engine's recursive modes, which sit between the `*` and the
    range: `* SHORTEST 1..5`, `* ALL SHORTEST 1..5`, `* WSHORTEST(w) 1..5`,
    `* TRAIL 1..5`, `* ACYCLIC 1..5`. Those words are stepped over so the bound
    that follows is the one that gets read.
    """
    index = start
    length = len(text)

    def digits_at(position: int) -> tuple[int | None, int]:
        end = position
        while end < length and text[end].isdigit():
            end += 1
        return (int(text[position:end]) if end > position else None), end

    while True:
        while index < length:
            if text[index].isspace():
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
            break
        end = index
        while end < length and (text[end].isalpha() or text[end] == "_"):
            end += 1
        word = text[index:end].lower()
        if word not in _RECURSIVE_MODES:
            break
        index = end
        # `WSHORTEST(weight)` names the property it weights by.
        while index < length and text[index].isspace():
            index += 1
        if index < length and text[index] == "(":
            closing = text.find(")", index)
            index = length if closing < 0 else closing + 1
    lower, index = digits_at(index)
    if text.startswith("..", index):
        upper, index = digits_at(index + 2)
        return upper, index
    # No range operator: a bare count is its own bound, and a bare `*` is not.
    return lower, index


def _is_relationship_bracket(text: str, index: int, *, previous: str) -> bool:
    """Whether the `[` at `index` opens a relationship pattern.

    A relationship bracket is enclosed by the pattern's arrow on BOTH sides:
    `-[r:T]->`, `<-[r]-`. Requiring both is what separates it from subtraction
    followed by a list, and `previous` is the last significant character the
    scan saw rather than the raw preceding one, so a comment between the dash
    and the bracket cannot hide the pattern.
    """
    if previous != "-":
        return False
    closing = _matching_bracket(text, index)
    return _next_symbol(text, closing) == "-"


def _matching_bracket(text: str, start: int) -> int:
    """The index just past the `]` closing the `[` at `start`."""
    depth = 0
    index = start
    length = len(text)
    while index < length:
        character = text[index]
        if character in ("'", '"', "`"):
            index = _skip_quoted(text, index)
            continue
        if text.startswith("/*", index):
            closing = text.find("*/", index + 2)
            index = length if closing < 0 else closing + 2
            continue
        if character == "[":
            depth += 1
        elif character == "]":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return length


def _next_symbol(text: str, start: int) -> str:
    """The next character that is part of the query, skipping trivia."""
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
        return character
    return ""


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
