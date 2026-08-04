"""The pinned canonical AST serialization of a `memory_v1` view definition.

**What is serialized.** PostgreSQL's own SQL parser is run over the *authored*
`CREATE VIEW` statement — the DDL string the migration executes, which is the
canonical source of the public surface — and the resulting parse tree is
canonicalized into JSON. The parser is not a re-implementation: `pglast` is a
binding to `libpg_query`, which is PostgreSQL's real grammar and parser
compiled as a library, so what this module hashes is the same tree the server
builds when it creates the view.

**Why not the deparsed text.** The obvious alternative — asking a running
server for `pg_get_viewdef()` and hashing that — is forbidden by the binding
design, and for a good reason: that string is the output of PostgreSQL's *view
printer*, an implementation detail that has changed spelling between releases
(how it parenthesizes, when it qualifies a name, how it renders a cast). A hash
taken over printer output would move when the printer changes even though the
view did not, and it would need a database to compute at all. Hashing the parse
tree of the authored statement has neither problem: the same source produces
the same tree on any machine with no server running, which is what makes
`surface_manifest_hash` reproducible and PostgreSQL-minor-version independent.

**What canonicalization removes.** Two things in the raw parse tree are not
semantics and must not reach the hash:

- ``location`` fields, which are byte offsets into the input text — inserting a
  space or a comment would move every one of them;
- the numeric ``value`` of an enumerated node field, which is an ordinal in a C
  header rather than a meaning; the enum's ``name`` is kept, so a change of
  meaning still changes the hash while a renumbering upstream does not.

Everything else is kept exactly as the parser produced it. Comments and
whitespace never appear at all — the parser discards them before a tree exists,
which is why reformatting a definition or rewriting its comments cannot roll
the hash, while changing a predicate, a join kind, a cast, or a column order
must.

**Why it is versioned.** The serialization is a hash input, so a change in how
a node is canonicalized would silently move every hash. `SERIALIZER_VERSION` is
therefore pinned, recorded in the manifest, and pinned again by checked-in
golden vectors that must keep producing byte-identical output — which is also
what catches a `pglast` upgrade that changes the tree.
"""

from pathlib import Path
from typing import Final

from pglast import parse_sql

from rememberstack.spine.query_space.canonical import CanonicalValue

#: Identifier of this serialization. A change here rolls every manifest hash.
SERIALIZER_VERSION: Final = "memory_v1.pglast_ast/1"

#: Checked-in vectors pinning the serializer's exact output.
GOLDEN_VECTORS_PATH: Final = Path(__file__).with_name("ast_golden_vectors.json")

#: Parse-tree fields that carry a position in the input rather than meaning.
_POSITION_FIELDS: Final = frozenset({"location", "stmt_location", "stmt_len"})

#: Key `pglast` uses for an enumerated field's own type name.
_ENUM_TAG: Final = "#"


class AstSerializationError(ValueError):
    """Raised when a statement cannot be parsed or canonicalized."""


def serialize_definition(*, authored_definition: str) -> CanonicalValue:
    """Canonicalize one authored SQL statement into its hashable parse tree.

    The argument is a single complete statement — for the manifest, one
    ``CREATE VIEW`` including its output-column list, because the public column
    names are part of the contract the hash pins.
    """
    try:
        statements = parse_sql(authored_definition)
    except Exception as error:  # noqa: BLE001 -- any parse failure is one failure
        raise AstSerializationError(
            f"could not parse the definition: {error}"
        ) from error
    if len(statements) != 1:
        raise AstSerializationError(
            f"expected exactly one statement, parsed {len(statements)}"
        )
    return canonicalize_node(node=statements[0].stmt(skip_none=True))


def canonicalize_node(*, node: object) -> CanonicalValue:
    """Strip positions and enum ordinals from one parse-tree value."""
    if node is None or isinstance(node, bool | int | str):
        return node
    if isinstance(node, list | tuple):
        return [canonicalize_node(node=item) for item in node]
    if isinstance(node, dict):
        if _ENUM_TAG in node:
            return {_ENUM_TAG: str(node[_ENUM_TAG]), "name": str(node["name"])}
        return {
            str(key): canonicalize_node(node=value)
            for key, value in node.items()
            if key not in _POSITION_FIELDS
        }
    raise AstSerializationError(
        f"{type(node).__name__} is not a canonicalizable parse-tree value"
    )
