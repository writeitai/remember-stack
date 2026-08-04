"""The pinned canonical AST serialization of a `memory_v1` view definition.

**What is serialized, and why not the SQL text.** PostgreSQL does not store the
SQL a migration typed. It parses a `CREATE VIEW` statement into a rewrite rule
and stores that parse tree; `pg_get_viewdef` prints the tree back out. The
printed form therefore already has every accident of authorship removed —
comments are gone, whitespace is normalized, implicit casts are made explicit,
type names are the canonical names `pg_catalog.format_type` produces, and
`SELECT *` is expanded to the columns it meant at creation time. That printed
parse tree, not the migration source, is this module's input, which is what
makes the rule "raw SQL text is never a hash input" true rather than aspirational.

**What this module adds.** A printed parse tree is still a string, and a string
comparison would be sensitive to the printer's line-breaking. So the printed
tree is lexed into a token tree and written back out as one canonical
s-expression: every token becomes ``kind:value``, every parenthesized group
becomes a bracketed list, and single spaces separate siblings. Two definitions
serialize identically exactly when they are the same tree of tokens, which
makes the serialization insensitive to line breaks, indentation, keyword case,
and comments, and sensitive to every semantic difference — a changed predicate,
a changed join, a changed cast, a reordered column.

**Why it is versioned.** The serialization is a hash input, so changing how a
token is spelled would silently move every hash. `SERIALIZER_VERSION` is
therefore pinned, recorded in the manifest, and pinned again by checked-in
golden vectors that must keep producing byte-identical output.

Token kinds:

- ``w`` — an unquoted word (keyword or identifier), folded to lower case
  because PostgreSQL folds unquoted identifiers that way;
- ``q`` — a double-quoted identifier, with its case preserved because
  PostgreSQL preserves it;
- ``n`` — a numeric literal, kept exactly as printed;
- ``s`` — a string literal, with doubled quotes decoded to one quote so that
  two spellings of the same literal agree;
- ``o`` — an operator or punctuation token, munched maximally so that ``::``,
  ``->>``, and ``<=`` are single tokens rather than character pairs.

A token's value is escaped so that the s-expression stays unambiguous: a
backslash, space, parenthesis, or control character in a string literal or a
quoted identifier is written as an escape rather than as itself, which is what
lets a reader split the serialization on spaces without ever splitting a value.
"""

from pathlib import Path
from typing import Final

#: Identifier of this serialization. A change here rolls every manifest hash.
SERIALIZER_VERSION: Final = "memory_v1.ast/1"

#: Checked-in vectors pinning the serializer's exact output.
GOLDEN_VECTORS_PATH: Final = Path(__file__).with_name("ast_golden_vectors.json")

_WORD_START: Final = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_")
_WORD_BODY: Final = _WORD_START | frozenset("0123456789$")
_DIGITS: Final = frozenset("0123456789")
#: Characters PostgreSQL allows inside a multi-character operator name.
_OPERATOR_CHARS: Final = frozenset("+-*/<>=~!@#%^&|`?")
_PUNCTUATION: Final = frozenset(",;.[]")

#: Value characters that would otherwise be structural in the s-expression.
_VALUE_ESCAPES: Final = {
    "\\": "\\\\",
    " ": "\\s",
    "(": "\\(",
    ")": "\\)",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}

type _Node = list["str | _Node"]


class AstSerializationError(ValueError):
    """Raised when a printed definition cannot be lexed deterministically."""


def serialize_definition(*, printed_definition: str) -> str:
    """Serialize one printed view definition into its canonical s-expression."""
    return _render(node=tokenize_definition(printed_definition=printed_definition))


def tokenize_definition(*, printed_definition: str) -> _Node:
    """Lex one printed view definition into its canonical token tree."""
    tokens, position = _read_group(text=printed_definition, position=0, depth=0)
    if position != len(printed_definition):
        raise AstSerializationError(
            f"unbalanced parenthesis at offset {position} of the printed definition"
        )
    return tokens


def _render(*, node: _Node) -> str:
    """Render one token-tree node as a bracketed, space-separated group."""
    parts = [item if isinstance(item, str) else _render(node=item) for item in node]
    return "(" + " ".join(parts) + ")"


def _token(*, kind: str, value: str) -> str:
    """Render one token, escaping the characters that carry structure."""
    escaped = "".join(
        _VALUE_ESCAPES.get(character)
        or (f"\\u{ord(character):04x}" if character < " " else character)
        for character in value
    )
    return f"{kind}:{escaped}"


def _read_group(*, text: str, position: int, depth: int) -> tuple[_Node, int]:
    """Lex tokens until the end of the input or the group's closing paren."""
    tokens: _Node = []
    while position < len(text):
        position = _skip_ignorable(text=text, position=position)
        if position >= len(text):
            break
        character = text[position]
        if character == "(":
            nested, position = _read_group(
                text=text, position=position + 1, depth=depth + 1
            )
            tokens.append(nested)
            continue
        if character == ")":
            if depth == 0:
                raise AstSerializationError(
                    f"unmatched closing parenthesis at offset {position}"
                )
            return tokens, position + 1
        token, position = _read_token(text=text, position=position)
        tokens.append(token)
    if depth != 0:
        raise AstSerializationError("unterminated parenthesised group")
    return tokens, position


def _read_token(*, text: str, position: int) -> tuple[str, int]:
    """Lex exactly one token starting at a non-ignorable character."""
    character = text[position]
    if character in _WORD_START:
        end = position + 1
        while end < len(text) and text[end] in _WORD_BODY:
            end += 1
        return _token(kind="w", value=text[position:end].lower()), end
    if character in _DIGITS:
        end = position + 1
        while end < len(text) and (text[end] in _DIGITS or text[end] in ".eE+-"):
            # an exponent sign only continues the number directly after e/E
            if text[end] in "+-" and text[end - 1] not in "eE":
                break
            end += 1
        return _token(kind="n", value=text[position:end]), end
    if character == "'":
        return _read_string(text=text, position=position)
    if character == '"':
        return _read_quoted_identifier(text=text, position=position)
    if character == "$":
        return _read_dollar_quoted(text=text, position=position)
    if character == ":":
        if text.startswith("::", position):
            return _token(kind="o", value="::"), position + 2
        return _token(kind="o", value=":"), position + 1
    if character in _OPERATOR_CHARS:
        end = position + 1
        while end < len(text) and text[end] in _OPERATOR_CHARS:
            end += 1
        return _token(kind="o", value=text[position:end]), end
    if character in _PUNCTUATION:
        return _token(kind="o", value=character), position + 1
    raise AstSerializationError(
        f"unexpected character {character!r} at offset {position}"
    )


def _read_string(*, text: str, position: int) -> tuple[str, int]:
    """Lex a single-quoted literal, decoding doubled quotes to one quote."""
    index = position + 1
    value: list[str] = []
    while index < len(text):
        character = text[index]
        if character == "'":
            if text.startswith("''", index):
                value.append("'")
                index += 2
                continue
            return _token(kind="s", value="".join(value)), index + 1
        value.append(character)
        index += 1
    raise AstSerializationError(f"unterminated string literal at offset {position}")


def _read_quoted_identifier(*, text: str, position: int) -> tuple[str, int]:
    """Lex a double-quoted identifier, preserving the case it declares."""
    index = position + 1
    value: list[str] = []
    while index < len(text):
        character = text[index]
        if character == '"':
            if text.startswith('""', index):
                value.append('"')
                index += 2
                continue
            return _token(kind="q", value="".join(value)), index + 1
        value.append(character)
        index += 1
    raise AstSerializationError(f"unterminated quoted identifier at offset {position}")


def _read_dollar_quoted(*, text: str, position: int) -> tuple[str, int]:
    """Lex a dollar-quoted literal, keeping its body exactly as written."""
    tag_end = text.find("$", position + 1)
    if tag_end < 0:
        raise AstSerializationError(f"unterminated dollar quote at offset {position}")
    tag = text[position : tag_end + 1]
    body_end = text.find(tag, tag_end + 1)
    if body_end < 0:
        raise AstSerializationError(f"unterminated dollar quote at offset {position}")
    return _token(kind="s", value=text[tag_end + 1 : body_end]), body_end + len(tag)


def _skip_ignorable(*, text: str, position: int) -> int:
    """Advance past whitespace and comments, which never reach the output."""
    while position < len(text):
        character = text[position]
        if character.isspace():
            position += 1
            continue
        if text.startswith("--", position):
            newline = text.find("\n", position)
            position = len(text) if newline < 0 else newline + 1
            continue
        if text.startswith("/*", position):
            position = _skip_block_comment(text=text, position=position)
            continue
        return position
    return position


def _skip_block_comment(*, text: str, position: int) -> int:
    """Advance past one block comment, honouring PostgreSQL's nesting rule."""
    depth = 0
    index = position
    while index < len(text):
        if text.startswith("/*", index):
            depth += 1
            index += 2
            continue
        if text.startswith("*/", index):
            depth -= 1
            index += 2
            if depth == 0:
                return index
            continue
        index += 1
    raise AstSerializationError(f"unterminated block comment at offset {position}")
