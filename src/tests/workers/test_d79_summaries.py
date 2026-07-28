"""D79 bottom-up summary composition and hard request bounds."""

from pathlib import Path
from uuid import UUID

from rememberstack.adapters.selfhost import LocalFSObjectStore
from rememberstack.adapters.testing import FakeModelProvider
from rememberstack.adapters.testing import NoopCostMeter
from rememberstack.core import blockize
from rememberstack.core import count_tokens
from rememberstack.core import parse_heading_skeleton
from rememberstack.model import RootSummaryPlacementResponse
from rememberstack.model import SectionSummaryResponse
from rememberstack.model import StructureSource
from rememberstack.workers.e0_summary import SectionSummarizer
from rememberstack.workers.e0_summary import SUMMARY_BALANCED_FAN_IN
from rememberstack.workers.e0_summary import SUMMARY_CALL_TOKEN_CEILING
from rememberstack.workers.e0_summary import SummarySettings

_DEPLOYMENT = UUID("71000000-0000-0000-0000-000000000001")
_DOC = UUID("71000000-0000-0000-0000-000000000002")
_VERSION = UUID("71000000-0000-0000-0000-000000000003")
_REPRESENTATION = UUID("71000000-0000-0000-0000-000000000004")


class _NoCacheCatalog:
    """The unit proofs exercise composition without prior sidecars."""

    def summary_cache_sidecars(
        self, *, doc_id: UUID, summary_version: str
    ) -> tuple[str, ...]:
        assert doc_id == _DOC
        assert summary_version
        return ()


def _source() -> StructureSource:
    return StructureSource(
        deployment_id=_DEPLOYMENT,
        doc_id=_DOC,
        version_id=_VERSION,
        representation_id=_REPRESENTATION,
        blocks_uri="unused/blocks.json",
        markdown_uri="unused/document.md",
        title="Composition proof",
        source_kind="upload",
    )


def _summarize(*, tmp_path: Path, markdown: str, provider: FakeModelProvider):
    blocks = blockize(document_md=markdown)
    sections = parse_heading_skeleton(
        blocks=blocks, title="Composition proof", markdown_chars=len(markdown)
    )
    result = SectionSummarizer(
        catalog=_NoCacheCatalog(),  # type: ignore[arg-type]
        artifact_store=LocalFSObjectStore(root=tmp_path),
        model_provider=provider,
        settings=SummarySettings(model="summary/test"),
    ).summarize(
        source=_source(),
        sections=sections,
        blocks=blocks,
        markdown=markdown,
        meter=NoopCostMeter(),
    )
    return result, provider.generated_requests


def test_leaf_parent_root_read_exactly_their_composition_inputs(tmp_path: Path) -> None:
    markdown = "\n\n".join(
        (
            "ROOT-OWN preamble.",
            "# Parent",
            "PARENT-OWN preamble.",
            "## Leaf Alpha",
            "ALPHA-ONLY body.",
            "## Leaf Beta",
            "BETA-ONLY body.",
        )
    )

    def route(prompt: str, response_type: str) -> dict[str, object]:
        if response_type == "RootSummaryPlacementResponse":
            assert "ROOT-OWN preamble." in prompt
            assert "Parent composed line." in prompt
            assert "PARENT-OWN preamble." not in prompt
            assert "ALPHA-ONLY body." not in prompt
            return {
                "summary": "Document composed line.",
                "placement_path": "/proof/composition/",
            }
        assert response_type == "SectionSummaryResponse"
        if "Call kind: section-final\nSection path: 0.0.0\n" in prompt:
            assert "ALPHA-ONLY body." in prompt
            assert "BETA-ONLY body." not in prompt
            assert "PARENT-OWN preamble." not in prompt
            return {"summary": "Alpha leaf line."}
        if "Call kind: section-final\nSection path: 0.0.1\n" in prompt:
            assert "BETA-ONLY body." in prompt
            assert "ALPHA-ONLY body." not in prompt
            assert "PARENT-OWN preamble." not in prompt
            return {"summary": "Beta leaf line."}
        assert "Call kind: section-final\nSection path: 0.0\n" in prompt
        assert "PARENT-OWN preamble." in prompt
        assert "Alpha leaf line." in prompt
        assert "Beta leaf line." in prompt
        assert "ALPHA-ONLY body." not in prompt
        assert "BETA-ONLY body." not in prompt
        return {"summary": "Parent composed line."}

    result, requests = _summarize(
        tmp_path=tmp_path,
        markdown=markdown,
        provider=FakeModelProvider(generate_router=route),
    )

    assert [section.summary for section in result.sections] == [
        "Document composed line.",
        "Parent composed line.",
        "Alpha leaf line.",
        "Beta leaf line.",
    ]
    assert result.placement_path == "/proof/composition/"
    assert result.summary_version is not None
    assert result.placement_version is not None
    assert all(request.temperature == 0.0 for request in requests)


def test_oversized_leaf_shards_at_block_grain_then_composes(tmp_path: Path) -> None:
    paragraphs = [
        f"BLOCK-{index} " + " ".join(f"word-{index}-{word}" for word in range(90))
        for index in range(80)
    ]
    markdown = "\n\n".join(("# Giant", *paragraphs))

    def route(prompt: str, response_type: str) -> dict[str, object]:
        if response_type == "RootSummaryPlacementResponse":
            return {"summary": "A giant document.", "placement_path": "/proof/giant/"}
        assert response_type == "SectionSummaryResponse"
        if "Call kind: block-shard" in prompt:
            return {"summary": "One bounded shard."}
        return {"summary": "The giant section composed from bounded shards."}

    result, requests = _summarize(
        tmp_path=tmp_path,
        markdown=markdown,
        provider=FakeModelProvider(generate_router=route),
    )

    shard_prompts = [
        request.prompt
        for request in requests
        if "Call kind: block-shard" in request.prompt
    ]
    assert len(shard_prompts) > 1
    assert all(
        count_tokens(text=request.prompt) <= SUMMARY_CALL_TOKEN_CEILING
        for request in requests
    )
    assert result.sections[1].summary == (
        "The giant section composed from bounded shards."
    )
    assert result.sections[0].summary == "A giant document."


def test_many_children_use_balanced_bounded_fan_in(tmp_path: Path) -> None:
    markdown = "\n\n".join(
        (
            "# Parent",
            "Parent preamble.",
            *(
                value
                for index in range(18)
                for value in (f"## Child {index}", f"Body {index}.")
            ),
        )
    )

    def route(prompt: str, response_type: str) -> dict[str, object]:
        if response_type == "RootSummaryPlacementResponse":
            return {"summary": "Wide document.", "placement_path": "/proof/wide/"}
        if "Call kind: child-reduction" in prompt:
            return {"summary": "Balanced child group."}
        if "Section path: 0.0\n" in prompt:
            return {"summary": "Wide parent."}
        return {"summary": "Child line."}

    result, requests = _summarize(
        tmp_path=tmp_path,
        markdown=markdown,
        provider=FakeModelProvider(generate_router=route),
    )
    reductions = [
        request.prompt
        for request in requests
        if "Call kind: child-reduction" in request.prompt
        and "Section path: 0.0\n" in request.prompt
    ]
    assert len(reductions) == 3
    assert {
        len(prompt.split("Ordered one-liners:\n", 1)[1].splitlines())
        for prompt in reductions
    } == {6}
    assert all(
        len(prompt.split("Ordered one-liners:\n", 1)[1].splitlines())
        <= SUMMARY_BALANCED_FAN_IN
        for prompt in reductions
    )
    assert result.sections[1].summary == "Wide parent."


def test_summary_response_schemas_are_closed_and_bounded() -> None:
    section_schema = SectionSummaryResponse.model_json_schema()
    root_schema = RootSummaryPlacementResponse.model_json_schema()
    assert section_schema["additionalProperties"] is False
    assert root_schema["additionalProperties"] is False
    assert section_schema["properties"]["summary"]["maxLength"] == 512
    assert root_schema["properties"]["summary"]["maxLength"] == 512
    assert root_schema["properties"]["placement_path"]["maxLength"] == 512
