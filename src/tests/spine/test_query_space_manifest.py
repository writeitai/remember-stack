"""Database-free proofs for the query-space canonicalizer and its artifacts.

These run everywhere, including where no PostgreSQL is configured, because a
manifest hash that only reproduces on one machine is not a contract. That is
the point of the source-derived manifest: the hash below is recomputed here
from the repository alone — the authored DDL parsed by PostgreSQL's own parser
plus the declared contract — with no server involved. The database-backed half
of §9.1, that the *deployed* schema equals what this manifest publishes, lives
in `test_query_space_batch_a.py`.
"""

import json

import pytest

from rememberstack.spine.query_space import AUTHORED_AUTHORIZATION_HELPERS
from rememberstack.spine.query_space import AUTHORED_VIEWS
from rememberstack.spine.query_space import build_manifest
from rememberstack.spine.query_space import build_matrix
from rememberstack.spine.query_space import canonical_json
from rememberstack.spine.query_space import declared_views
from rememberstack.spine.query_space import DELETION_TARGETS
from rememberstack.spine.query_space import EXECUTED_TARGETS
from rememberstack.spine.query_space import GOLDEN_VECTORS_PATH
from rememberstack.spine.query_space import load_manifest
from rememberstack.spine.query_space import load_matrix
from rememberstack.spine.query_space import MANIFEST_PATH
from rememberstack.spine.query_space import MATRIX_SURFACES
from rememberstack.spine.query_space import render_manifest
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


def test_canonical_json_matches_the_canonicalization_scheme_expectations() -> None:
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


def test_canonical_json_refuses_values_outside_the_admitted_subset() -> None:
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
    """The pinned serializer still produces byte-identical output.

    This is also the tripwire for a parser upgrade: `pglast` embeds
    PostgreSQL's own grammar, so a new major could shape a node differently and
    silently move every hash. It would fail here first.
    """
    golden = _golden()
    assert golden["serializer"] == SERIALIZER_VERSION
    vectors = golden["vectors"]
    assert isinstance(vectors, list) and vectors
    for vector in vectors:
        assert isinstance(vector, dict)
        produced = serialize_definition(authored_definition=str(vector["definition"]))
        assert produced == vector["ast"], vector["name"]


def test_ast_serializer_ignores_formatting_and_notices_semantics() -> None:
    """Layout and comments cannot move the hash; a semantic change must."""
    golden = _golden()
    equivalences = golden["equivalences"]
    distinctions = golden["distinctions"]
    assert isinstance(equivalences, list) and isinstance(distinctions, list)
    for case in equivalences:
        assert isinstance(case, dict)
        assert serialize_definition(
            authored_definition=str(case["left"])
        ) == serialize_definition(authored_definition=str(case["right"])), case["name"]
    for case in distinctions:
        assert isinstance(case, dict)
        assert serialize_definition(
            authored_definition=str(case["left"])
        ) != serialize_definition(authored_definition=str(case["right"])), case["name"]


def test_the_manifest_hash_recomputes_from_source_without_a_database() -> None:
    """The published hash is reproducible from the repository alone.

    No connection, no server version, no deparser: the authored DDL and the
    declared contract are the only inputs, which is what makes the hash
    independent of the PostgreSQL minor version a deployment happens to run.
    """
    manifest = load_manifest()
    generated = build_manifest()
    assert generated["hash_members"] == manifest["hash_members"]
    assert generated["surface_manifest_hash"] == manifest["surface_manifest_hash"]
    assert MANIFEST_PATH.read_text(encoding="utf-8") == render_manifest(generated)

    members = manifest["hash_members"]
    assert isinstance(members, dict)
    assert sorted(members) == [
        "core_operation_descriptors",
        "function_signatures",
        "limits",
        "views_schema",
    ]
    assert manifest["surface_manifest_hash"] == surface_manifest_hash(members)


def test_two_independent_builds_of_the_manifest_agree_byte_for_byte() -> None:
    """The hash is a property of the source, not of when it was generated."""
    first = build_manifest()
    second = build_manifest()
    assert first["surface_manifest_hash"] == second["surface_manifest_hash"]
    assert render_manifest(first) == render_manifest(second)


def test_checked_in_manifest_binds_the_later_members_structurally() -> None:
    """The still-unpopulated members carry their bound shape already."""
    manifest = load_manifest()
    members = manifest["hash_members"]
    assert isinstance(members, dict)
    signatures = members["function_signatures"]
    assert isinstance(signatures, dict)
    assert signatures["contract"] == "memory_v1.functions/1"
    published = signatures["functions"]
    assert isinstance(published, list)
    # Every function the bridge resolves publishes its arity, its filter
    # vocabulary, and the columns it answers with, so a caller can read the
    # contract without executing anything.
    assert {entry["name"] for entry in published} == {  # type: ignore[index]
        "explain_cypher",
        "facts_as_of",
        "fetch_chunk_bodies",
        "graph_neighborhood",
        "graph_path",
        "lexical_chunks",
        "lexical_claims",
        "query_cypher",
        "semantic_chunks",
        "semantic_claims",
        "semantic_entities",
        "semantic_facts",
    }
    for entry in published:
        assert isinstance(entry, dict)
        assert entry["arguments_min"] >= 1
        assert entry["columns"]
    core = members["core_operation_descriptors"]
    assert isinstance(core, dict)
    assert core["contract"] == "memory_v1.core_operations/1"
    operations = core["operations"]
    assert isinstance(operations, list)
    assert [operation["name"] for operation in operations] == [  # type: ignore[index]
        "current_context",
        "question_context",
        "resolve_entity",
    ]
    question = next(
        operation
        for operation in operations
        if isinstance(operation, dict) and operation["name"] == "question_context"
    )
    assert question["version"] == 4
    schema = question["input_schema"]
    assert isinstance(schema, dict)
    properties = schema["properties"]
    assert isinstance(properties, dict)
    assert properties["include_facts"]["default"] is False  # type: ignore[index]
    assert properties["include_entities"]["default"] is False  # type: ignore[index]
    limits = members["limits"]
    assert isinstance(limits, dict)
    assert sorted(limits) == [
        "contract",
        "cypher_dialect",
        "p2_projection",
        "resource_limits",
        "sql_grammar",
    ]
    cypher = limits["cypher_dialect"]
    assert isinstance(cypher, dict)
    assert cypher["text_bytes_max"] == 32 * 1024
    assert cypher["read_openings"] == ["match", "optional", "return", "unwind", "with"]
    assert cypher["engine_rejected_mutations"] == ["create", "delete", "merge", "set"]
    projection = limits["p2_projection"]
    assert isinstance(projection, dict)
    assert projection["contract_version"] == "p2-rebuild-2026.07"
    assert set(projection["node_types"]) == {"Entity", "Document"}  # type: ignore[arg-type]
    assert set(projection["edge_types"]) == {  # type: ignore[arg-type]
        "RELATES",
        "MENTIONED_IN",
        "DOC_CROSSREF",
        "IS_DOCUMENT",
    }
    relates = projection["edge_types"]["RELATES"]  # type: ignore[index]
    assert set(relates) == {  # type: ignore[arg-type]
        "relation_id",
        "subject_id",
        "object_id",
        "predicate",
        "fact",
        "evidence_count",
        "contradict_count",
        "confidence",
        "contradiction_group",
        "valid_from",
        "valid_until",
        "ingested_at",
        "invalidated_at",
    }


def test_manifest_definitions_are_parse_trees_and_never_sql_text() -> None:
    """A definition reaches the hash only as PostgreSQL's own parse tree."""
    manifest = load_manifest()
    views_schema = manifest["hash_members"]["views_schema"]
    assert isinstance(views_schema, dict)
    assert views_schema["definition_ast_serializer"] == SERIALIZER_VERSION
    helpers = views_schema["authorization_helpers"]
    assert isinstance(helpers, list)
    assert [helper["qualified_name"] for helper in helpers] == [
        helper.qualified_name for helper in AUTHORED_AUTHORIZATION_HELPERS.values()
    ]
    for helper in helpers:
        assert isinstance(helper, dict)
        assert helper["definition_ast"]["@"] == "ViewStmt"
    views = views_schema["views"]
    assert isinstance(views, list)
    for view in views:
        assert isinstance(view, dict)
        definition = view["definition_ast"]
        assert isinstance(definition, dict), view["name"]
        # a parse tree, tagged with the node types PostgreSQL's grammar builds
        assert definition["@"] == "ViewStmt"
        rendered = canonical_json(definition)
        assert " SELECT " not in rendered and " FROM " not in rendered
        # positions into the input text are not semantics and are stripped
        assert '"location"' not in rendered


def test_every_published_column_is_authored_declared_and_documented() -> None:
    """The DDL, the declared contract, and the manifest describe one column set."""
    views = declared_views()
    assert {view.name for view in views} == {
        contract.name for contract in VIEW_CONTRACTS
    }
    for view in views:
        authored = AUTHORED_VIEWS[view.name]
        assert [column.name for column in view.columns] == list(authored.column_names)
        for column in view.columns:
            assert column.type
            assert column.comment.endswith(".")
        assert view.comment.endswith(".")


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
    assert generated["cell_count"] == len(DELETION_TARGETS) * len(MATRIX_SURFACES)
    cells = generated["cells"]
    assert isinstance(cells, list)
    coordinates = {
        (str(cell["target_id"]), str(cell["surface"]))
        for cell in cells
        if isinstance(cell, dict)
    }
    assert len(coordinates) == len(cells)


def test_every_matrix_cell_states_a_status_and_a_reason() -> None:
    """No cell is silent: each carries its status, its basis, and one line of why."""
    matrix = build_matrix()
    cells = matrix["cells"]
    assert isinstance(cells, list)
    seen = {"applicable": 0, "not_applicable": 0, "deferred": 0}
    for cell in cells:
        assert isinstance(cell, dict)
        status = str(cell["status"])
        seen[status] += 1
        expectation = str(cell["expectation"])
        assert expectation.endswith(".") and len(expectation) > 60
        if status == "not_applicable":
            assert cell["basis"] in {
                "no_identifier_of_this_class",
                "not_caller_reachable",
            }
        else:
            assert cell["basis"] is None
    assert seen == matrix["status_counts"]
    assert seen["applicable"] > 0 and seen["deferred"] > 0


def test_the_matrix_covers_every_surface_and_names_its_deferrals() -> None:
    """Every public relation and the private helper appear, and so does the rest."""
    matrix = build_matrix()
    surfaces = matrix["surfaces"]
    assert isinstance(surfaces, list)
    names = {str(surface["name"]) for surface in surfaces if isinstance(surface, dict)}
    # Every authorization helper is a surface: the mention, fact, and citation
    # helpers compile deletion rules, while the two survivor views are private
    # merge machinery.
    assert names == {f"memory_v1.{contract.name}" for contract in VIEW_CONTRACTS} | {
        "public.v_graph_survivor",
        "public.v_memory_entity_survivor",
        "public.v_memory_fact_visible",
        "public.v_memory_mention_current_content",
        "public.v_memory_page_citation_visible",
    }
    private = [
        surface
        for surface in surfaces
        if isinstance(surface, dict) and not surface["caller_reachable"]
    ]
    # All five helpers are unreachable to a caller; three of them additionally
    # compile a deletion rule, so their cells execute rather than resting on
    # non-reachability alone.
    assert len(private) == 5
    assert sum(1 for surface in private if surface["compiles_deletion"]) == 3

    deferred = [target for target in DELETION_TARGETS if target.deferred]
    assert {target.target_id for target in deferred} == {
        "p1_candidate",
        "corpus_body",
    }
    assert len(EXECUTED_TARGETS) + len(deferred) == len(DELETION_TARGETS)
    for target in deferred:
        assert target.executed_in in {"C", "D"}
