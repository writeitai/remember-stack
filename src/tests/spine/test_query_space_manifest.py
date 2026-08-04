"""Database-free proofs for the query-space canonicalizer and its artifacts.

These run everywhere, including where no PostgreSQL is configured, because a
manifest hash that only reproduces on one machine is not a contract. The
database-backed half of §9.1 — introspection equalling the checked-in manifest
— lives in `test_query_space_batch_a.py`.
"""

import json

import pytest

from rememberstack.spine.query_space import build_matrix
from rememberstack.spine.query_space import canonical_json
from rememberstack.spine.query_space import DELETION_TARGETS
from rememberstack.spine.query_space import GOLDEN_VECTORS_PATH
from rememberstack.spine.query_space import load_manifest
from rememberstack.spine.query_space import load_matrix
from rememberstack.spine.query_space import render_matrix
from rememberstack.spine.query_space import serialize_definition
from rememberstack.spine.query_space import SERIALIZER_VERSION
from rememberstack.spine.query_space import surface_manifest_hash
from rememberstack.spine.query_space import VIEW_CONTRACTS
from rememberstack.spine.query_space.canonical import CanonicalizationError
from rememberstack.spine.query_space.deletion_matrix import MATRIX_PATH


def _golden() -> dict[str, object]:
    """Read the checked-in serializer golden vectors."""
    loaded = json.loads(GOLDEN_VECTORS_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_canonical_json_matches_the_rfc_8785_reference_expectations() -> None:
    """Member order, escaping, and separators follow the canonicalization scheme."""
    assert canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'
    assert canonical_json({"ä": 1, "a": 2, "Z": 3}) == '{"Z":3,"a":2,"ä":1}'
    assert canonical_json([1, "two", True, False, None]) == '[1,"two",true,false,null]'
    assert canonical_json('quote" back\\ tab\t bell\x07') == (
        '"quote\\" back\\\\ tab\\t bell\\u0007"'
    )
    assert canonical_json({}) == "{}"
    assert canonical_json([]) == "[]"


def test_canonical_json_sorts_member_names_by_utf16_code_unit() -> None:
    """A supplementary-plane name sorts by code unit, not by code point.

    U+FFFD sorts after U+10000 in UTF-16 code-unit order (the latter is encoded
    as a surrogate pair beginning with U+D800) and before it in code-point
    order, so this is exactly where a naive `sort_keys=True` would disagree.
    """
    assert canonical_json({"\U00010000": 1, "�": 2}) == '{"\U00010000":1,"�":2}'


def test_canonical_json_refuses_values_it_cannot_pin() -> None:
    """A float or a non-string member name fails rather than being approximated."""
    with pytest.raises(CanonicalizationError):
        canonical_json(1.5)  # pyright: ignore[reportArgumentType]
    with pytest.raises(CanonicalizationError):
        canonical_json({1: "a"})  # pyright: ignore[reportArgumentType]


def test_surface_manifest_hash_is_stable_and_order_independent() -> None:
    """Two spellings of the same member document hash identically."""
    first = surface_manifest_hash({"a": {"x": 1, "y": [1, 2]}, "b": "text"})
    second = surface_manifest_hash({"b": "text", "a": {"y": [1, 2], "x": 1}})
    assert first == second
    assert len(first) == 64 and first == first.lower()
    assert surface_manifest_hash({"a": {"x": 2}, "b": "text"}) != first


def test_ast_serializer_reproduces_every_checked_in_golden_vector() -> None:
    """The pinned serializer still produces byte-identical output."""
    golden = _golden()
    assert golden["serializer"] == SERIALIZER_VERSION
    vectors = golden["vectors"]
    assert isinstance(vectors, list) and vectors
    for vector in vectors:
        assert isinstance(vector, dict)
        produced = serialize_definition(
            printed_definition=str(vector["printed_definition"])
        )
        assert produced == vector["serialization"], vector["name"]


def test_ast_serializer_ignores_formatting_and_notices_semantics() -> None:
    """Layout and comments cannot move the hash; a semantic change must."""
    golden = _golden()
    equivalences = golden["equivalences"]
    distinctions = golden["distinctions"]
    assert isinstance(equivalences, list) and isinstance(distinctions, list)
    for case in equivalences:
        assert isinstance(case, dict)
        assert serialize_definition(
            printed_definition=str(case["left"])
        ) == serialize_definition(printed_definition=str(case["right"])), case["name"]
    for case in distinctions:
        assert isinstance(case, dict)
        assert serialize_definition(
            printed_definition=str(case["left"])
        ) != serialize_definition(printed_definition=str(case["right"])), case["name"]


def test_checked_in_manifest_hash_recomputes_from_its_own_members() -> None:
    """The published hash is the canonical hash of the four bound members."""
    manifest = load_manifest()
    members = manifest["hash_members"]
    assert isinstance(members, dict)
    assert sorted(members) == [
        "core_operation_descriptors",
        "function_signatures",
        "limits",
        "views_schema",
    ]
    assert manifest["surface_manifest_hash"] == surface_manifest_hash(members)


def test_checked_in_manifest_binds_the_later_members_structurally() -> None:
    """The three still-unpopulated members carry their bound shape already."""
    manifest = load_manifest()
    members = manifest["hash_members"]
    assert isinstance(members, dict)
    assert members["function_signatures"] == {
        "contract": "memory_v1.functions/1",
        "functions": [],
    }
    assert members["core_operation_descriptors"] == {
        "contract": "memory_v1.core_operations/1",
        "operations": [],
    }
    limits = members["limits"]
    assert isinstance(limits, dict)
    assert sorted(limits) == [
        "contract",
        "cypher_dialect",
        "p2_projection",
        "resource_limits",
        "sql_grammar",
    ]


def test_manifest_raw_sql_text_never_reaches_the_hashed_members() -> None:
    """Definitions appear only as the pinned serialization, never as SQL."""
    manifest = load_manifest()
    views_schema = manifest["hash_members"]["views_schema"]
    assert isinstance(views_schema, dict)
    assert views_schema["definition_ast_serializer"] == SERIALIZER_VERSION
    views = views_schema["views"]
    assert isinstance(views, list)
    for view in views:
        assert isinstance(view, dict)
        definition = view["definition_ast"]
        assert isinstance(definition, str)
        assert definition.startswith("(w:select") or definition.startswith("((w:select")
        # the s-expression is a token tree, not SQL: a bare keyword with the
        # surrounding whitespace SQL requires cannot appear in it
        assert " SELECT " not in definition
        assert " FROM " not in definition


def test_manifest_covers_exactly_the_declared_relations() -> None:
    """The checked-in manifest and the declared contract enumerate one set."""
    manifest = load_manifest()
    views_schema = manifest["hash_members"]["views_schema"]
    assert isinstance(views_schema, dict)
    views = views_schema["views"]
    assert isinstance(views, list)
    names = [str(view["name"]) for view in views if isinstance(view, dict)]
    assert names == sorted(names)
    assert set(names) == {contract.name for contract in VIEW_CONTRACTS}
    assert len(names) == 24


def test_deletion_matrix_artifact_matches_its_generator() -> None:
    """The checked-in coverage artifact is the generated enumeration, verbatim."""
    generated = build_matrix()
    assert load_matrix() == generated
    assert MATRIX_PATH.read_text(encoding="utf-8") == render_matrix(generated)
    assert generated["cell_count"] == len(DELETION_TARGETS) * len(VIEW_CONTRACTS)
    cells = generated["cells"]
    assert isinstance(cells, list)
    assert len({json.dumps(cell, sort_keys=True) for cell in cells}) == len(cells)
