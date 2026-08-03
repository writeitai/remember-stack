"""Unit proofs for D80 recovery, poison split, E2 validation, legacy hydration."""

from uuid import uuid4

from rememberstack.adapters.selfhost import LanceChunkIndex
from rememberstack.core.embedding_input_policy import build_location_elements
from rememberstack.core.embedding_input_policy import EMBEDDING_INPUT_POLICY_VERSION
from rememberstack.core.embedding_input_policy import location_facts_json
from rememberstack.core.embedding_input_policy import LocationElementKind
from rememberstack.core.embedding_input_policy import LocationFacts
from rememberstack.core.embedding_input_policy import LocationProvenance
from rememberstack.core.embedding_input_policy import render_embedding_input
from rememberstack.model import ChunkForEmbedding
from rememberstack.model import P1ChunkRow
from rememberstack.surfaces.query_engine import _strip_legacy_prefix
from rememberstack.workers.e2 import _location_bundle_line
from rememberstack.workers.e2 import _location_grounding_pairs


def test_match_chunk_embeddings_requires_generation_and_hash(tmp_path) -> None:
    """Crash recovery only accepts the active triple with a matching hash."""
    deployment_id = uuid4()
    chunk_id = uuid4()
    index = LanceChunkIndex(root=tmp_path / "lance")
    body = "Recovered body text for generation-safe P1 rows."
    rendered = render_embedding_input(
        facts=LocationFacts(
            chunk_id=chunk_id,
            doc_id=uuid4(),
            version_id=uuid4(),
            title="Doc",
            source_kind="upload",
            source_shape="document",
            section_title="One",
            section_path="0",
            section_role="body",
            chunk_count=2,
        ),
        body=body,
    )
    index.upsert_chunks(
        rows=(
            P1ChunkRow(
                chunk_id=chunk_id,
                deployment_id=deployment_id,
                doc_id=uuid4(),
                version_id=uuid4(),
                section_role="body",
                text=rendered.body,
                vector=(0.25, 0.75),
                policy_generation=EMBEDDING_INPUT_POLICY_VERSION,
                embedder_generation="test-embedder",
                embedding_text_hash=rendered.embedding_text_hash,
                source_kind="upload",
                source_shape="document",
            ),
        )
    )

    matched = index.match_chunk_embeddings(
        deployment_id=str(deployment_id),
        chunk_ids=(str(chunk_id),),
        policy_generation=EMBEDDING_INPUT_POLICY_VERSION,
        embedder_generation="test-embedder",
    )
    assert str(chunk_id) in matched
    vector, stored_hash = matched[str(chunk_id)]
    assert vector == (0.25, 0.75)
    assert stored_hash == rendered.embedding_text_hash

    wrong_gen = index.match_chunk_embeddings(
        deployment_id=str(deployment_id),
        chunk_ids=(str(chunk_id),),
        policy_generation=EMBEDDING_INPUT_POLICY_VERSION,
        embedder_generation="other-embedder",
    )
    assert wrong_gen == {}


def test_dual_generation_rows_coexist(tmp_path) -> None:
    """Upsert by triple keeps two generations for the same chunk_id."""
    deployment_id = uuid4()
    chunk_id = uuid4()
    index = LanceChunkIndex(root=tmp_path / "lance")
    base = dict(
        chunk_id=chunk_id,
        deployment_id=deployment_id,
        doc_id=uuid4(),
        version_id=uuid4(),
        section_role="body",
        text="shared body",
        vector=(1.0, 0.0),
        embedding_text_hash="hash-a",
        source_kind="upload",
        source_shape="document",
    )
    index.upsert_chunks(
        rows=(
            P1ChunkRow(
                **base, policy_generation="policy-v1", embedder_generation="emb-a"
            ),
            P1ChunkRow(
                chunk_id=chunk_id,
                deployment_id=deployment_id,
                doc_id=base["doc_id"],
                version_id=base["version_id"],
                section_role="body",
                text="shared body",
                vector=(0.0, 1.0),
                policy_generation="policy-v2",
                embedder_generation="emb-a",
                embedding_text_hash="hash-b",
                source_kind="upload",
                source_shape="document",
            ),
        )
    )
    v1 = index.chunk_vectors(
        deployment_id=str(deployment_id),
        chunk_ids=(str(chunk_id),),
        policy_generation="policy-v1",
        embedder_generation="emb-a",
    )
    v2 = index.chunk_vectors(
        deployment_id=str(deployment_id),
        chunk_ids=(str(chunk_id),),
        policy_generation="policy-v2",
        embedder_generation="emb-a",
    )
    assert v1[str(chunk_id)] == (1.0, 0.0)
    assert v2[str(chunk_id)] == (0.0, 1.0)
    assert index.row_count() == 2


def test_e2_rejects_model_derived_and_unknown_kinds() -> None:
    """Grounding union only admits closed LocationElement kinds/provenance."""
    chunk_id = uuid4()
    facts = LocationFacts(
        chunk_id=chunk_id,
        doc_id=uuid4(),
        version_id=uuid4(),
        title="Title",
        source_kind="upload",
        section_title="Section",
        section_path="0.1",
        section_role="body",
        chunk_count=1,
    )
    elements = list(build_location_elements(facts=facts))
    # Inject invalid records into the stamp JSON.
    bad_payload = location_facts_json(facts=facts, elements=tuple(elements))
    import json

    payload = json.loads(bad_payload)
    payload["elements"].extend(
        [
            {
                "element_id": "bad1",
                "kind": "summary",
                "text": "Invented summary",
                "provenance": "source",
            },
            {
                "element_id": "bad2",
                "kind": "section_title",
                "text": "Model prose",
                "provenance": "model_derived",
            },
            {
                "element_id": "bad3",
                "kind": "section_role",
                "text": "body",
                "provenance": "source",
            },
        ]
    )
    chunk = ChunkForEmbedding(
        chunk_id=chunk_id,
        doc_id=facts.doc_id,
        version_id=facts.version_id,
        ordinal=0,
        char_start=0,
        char_end=5,
        chunk_content_hash="h",
        extraction_input_hash="i",
        section_role="body",
        section_path="0.1",
        section_title="Section",
        location_facts_json=json.dumps(payload),
        context_prefix="LEGACY FREEFORM PREFIX",
        location_header="Document: Title",
    )
    pairs = _location_grounding_pairs(chunk=chunk)
    kinds = {kind for kind, _ in pairs}
    texts = {text for _, text in pairs}
    assert "summary" not in kinds
    assert "section_role" not in kinds
    assert "Model prose" not in texts
    assert "Invented summary" not in texts
    assert LocationElementKind.DOCUMENT_TITLE.value in kinds
    assert "LEGACY FREEFORM PREFIX" not in _location_bundle_line(chunk=chunk)
    assert LocationProvenance.SOURCE.value  # touch enum for import stability


def test_legacy_hydration_strips_embedded_prefix() -> None:
    """Legacy P1 text that embeds the prefix is body-only after strip."""
    header = "Document: staffing; Section: staffing; Role: body"
    body = "Alice joined the team in March."
    indexed = f"{header}\n\n{body}"
    assert _strip_legacy_prefix(indexed_text=indexed, location_header=header) == body
    # D80 body-only rows are left untouched.
    assert _strip_legacy_prefix(indexed_text=body, location_header=header) == body


def test_provider_outage_classifier_distinguishes_poison() -> None:
    """Total outages re-raise; invalid responses are size-1 poison candidates."""
    from rememberstack.model import ProviderCallError
    from rememberstack.model import ProviderInvalidResponseError
    from rememberstack.workers.e1 import _is_provider_outage

    assert _is_provider_outage(exc=ProviderInvalidResponseError("bad vector")) is False
    assert _is_provider_outage(exc=ProviderCallError("upstream 503")) is True
    assert _is_provider_outage(exc=TimeoutError()) is True
    assert _is_provider_outage(exc=ConnectionError("reset")) is True
    assert _is_provider_outage(exc=RuntimeError("unknown")) is True


def test_multi_chunk_without_section_title_is_body_only() -> None:
    """§4.3 step 4: bare title alone does not force a multi-chunk header."""
    rendered = render_embedding_input(
        facts=LocationFacts(
            chunk_id=uuid4(),
            doc_id=uuid4(),
            version_id=uuid4(),
            title="Only a title",
            source_kind="upload",
            source_shape="document",
            section_title=None,
            section_path="0.3",
            section_role="body",
            chunk_count=4,
        ),
        body="Short body under the multi-chunk gate.",
    )
    from rememberstack.core.embedding_input_policy import EmbedHeaderMode

    assert rendered.mode is EmbedHeaderMode.BODY_ONLY
    assert rendered.location_header is None
