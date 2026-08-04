"""A restricted JCS subset, and the `surface_manifest_hash` it feeds.

The query space publishes one hash that identifies the exact public surface a
result was produced against. For that hash to be usable — pinned by a saved
query, compared across two builds, quoted in a benchmark trace — two
independent generator runs on two machines must produce the same bytes, and a
formatting change must not move it. That is what RFC 8785 (JSON
Canonicalization Scheme) specifies: object members are emitted in ascending
order of their UTF-16 code units, no insignificant whitespace is produced, and
strings use the shortest legal escaping.

**This module implements a deliberately restricted subset of that scheme, not
the whole of it.** The admitted types are exactly those the manifest contains:
objects, arrays, strings, integers within the exactly representable range,
booleans, and null. RFC 8785's number rule — the shortest ECMAScript
round-trip form of an IEEE-754 double — is *not* implemented: a non-integer
float raises `CanonicalizationError` instead. That is the honest trade. A
partial implementation of the number rule would produce bytes that agree with
the RFC on the values we happen to test and disagree on some value we do not,
which is the worst possible property for a hash; refusing the type instead
means the manifest can never contain a value this module would canonicalize
differently from a conforming implementation. Admitting floats later means
implementing the full number rule and rolling the hash deliberately, not
discovering a silent divergence.

Within the admitted types the output is RFC 8785 conformant, so a conforming
canonicalizer in another language reproduces these bytes exactly.

Python's `json` module is deliberately not used for the hashed bytes.
`json.dumps(sort_keys=True)` sorts by Unicode code point rather than UTF-16
code unit — the two orders disagree for keys outside the Basic Multilingual
Plane — and its escaping and separator defaults are configuration rather than
contract. Serializing here keeps the canonical form pinned in one readable
place.
"""

import hashlib
import json
from typing import Final

#: Escapes RFC 8785 requires in preference to a \\uXXXX sequence.
_SHORT_ESCAPES: Final = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}

#: Largest integer JSON round-trips exactly through an IEEE-754 double.
_MAX_SAFE_INTEGER: Final = 2**53 - 1

type CanonicalValue = (
    None | bool | int | str | list["CanonicalValue"] | dict[str, "CanonicalValue"]
)


class CanonicalizationError(ValueError):
    """Raised when a value is outside the admitted canonicalization subset."""


def canonical_json(value: CanonicalValue) -> str:
    """Serialize an admitted JSON value to its canonical form."""
    parts: list[str] = []
    _write(value=value, parts=parts)
    return "".join(parts)


def canonical_json_bytes(value: CanonicalValue) -> bytes:
    """Serialize to canonical JSON and encode it as the UTF-8 hash input."""
    return canonical_json(value).encode("utf-8")


def surface_manifest_hash(members: dict[str, CanonicalValue]) -> str:
    """Return the lowercase SHA-256 of the canonical hash-member document.

    The argument is the complete set of hashed manifest members. Raw SQL text
    is never part of it: a view definition reaches the hash only through its
    pinned canonical AST serialization, so reformatting a migration cannot roll
    the hash while any semantic change to a definition must.
    """
    return hashlib.sha256(canonical_json_bytes(members)).hexdigest()


def load_canonical_json(text: str) -> CanonicalValue:
    """Parse JSON text into the value types this module can canonicalize."""
    return json.loads(text)


def _write(*, value: object, parts: list[str]) -> None:
    """Append one value's canonical serialization to the output parts.

    The parameter is deliberately untyped: this is the boundary that turns a
    value that merely claims to be JSON into canonical bytes, so an unexpected
    type must fail loudly here rather than reach the hash.
    """
    if value is None:
        parts.append("null")
        return
    if isinstance(value, bool):
        parts.append("true" if value else "false")
        return
    if isinstance(value, int):
        _write_integer(value=value, parts=parts)
        return
    if isinstance(value, str):
        parts.append(_quote(value=value))
        return
    if isinstance(value, list):
        parts.append("[")
        for index, item in enumerate(value):
            if index:
                parts.append(",")
            _write(value=item, parts=parts)
        parts.append("]")
        return
    if isinstance(value, dict):
        parts.append("{")
        for index, key in enumerate(_sorted_keys(keys=value)):
            if index:
                parts.append(",")
            parts.append(_quote(value=key))
            parts.append(":")
            _write(value=value[key], parts=parts)
        parts.append("}")
        return
    raise CanonicalizationError(
        f"{type(value).__name__} is outside the admitted canonicalization subset"
    )


def _write_integer(*, value: int, parts: list[str]) -> None:
    """Append an integer, refusing values that cannot round-trip through JSON."""
    if abs(value) > _MAX_SAFE_INTEGER:
        raise CanonicalizationError(
            f"integer {value} exceeds the exactly representable JSON range"
        )
    parts.append(str(value))


def _sorted_keys(*, keys: dict[object, object]) -> list[str]:
    """Order member names by UTF-16 code unit, as RFC 8785 requires."""
    names: list[str] = []
    for key in keys:
        if not isinstance(key, str):
            raise CanonicalizationError("object member names must be strings")
        names.append(key)
    return sorted(names, key=lambda name: name.encode("utf-16-be"))


def _quote(*, value: str) -> str:
    """Escape a string with the shortest form RFC 8785 permits."""
    out = ['"']
    for character in value:
        short = _SHORT_ESCAPES.get(character)
        if short is not None:
            out.append(short)
        elif character < " ":
            out.append(f"\\u{ord(character):04x}")
        else:
            out.append(character)
    out.append('"')
    return "".join(out)
