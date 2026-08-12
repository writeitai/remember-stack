"""D79 bottom-up summary composition and hard request bounds."""

from pathlib import Path
from uuid import UUID

import pytest

from rememberstack.adapters.selfhost import LocalFSObjectStore
from rememberstack.adapters.testing import FakeModelProvider
from rememberstack.adapters.testing import NoopCostMeter
from rememberstack.core import blockize
from rememberstack.core import count_tokens
from rememberstack.core import parse_heading_skeleton
from rememberstack.model import ObjectKey
from rememberstack.model import ProviderAccountingError
from rememberstack.model import RootSummaryPlacementResponse
from rememberstack.model import SectionSummaryResponse
from rememberstack.model import StructureSource
from rememberstack.workers.e0_summary import _render_child_lines
from rememberstack.workers.e0_summary import _summary_cache_key
from rememberstack.workers.e0_summary import SectionSummarizer
from rememberstack.workers.e0_summary import SUMMARY_BALANCED_FAN_IN
from rememberstack.workers.e0_summary import SUMMARY_CALL_CHAR_CEILING
from rememberstack.workers.e0_summary import SUMMARY_CALL_TOKEN_CEILING
from rememberstack.workers.e0_summary import SUMMARY_MAX_CHARS
from rememberstack.workers.e0_summary import SummarySettings

_DEPLOYMENT = UUID("71000000-0000-0000-0000-000000000001")
_DOC = UUID("71000000-0000-0000-0000-000000000002")
_VERSION = UUID("71000000-0000-0000-0000-000000000003")
_REPRESENTATION = UUID("71000000-0000-0000-0000-000000000004")


class _NoCacheCatalog:
    """The unit proofs exercise composition without prior sidecars."""

    def summary_cache_sidecars(self, *, doc_id: UUID) -> tuple[str, ...]:
        assert doc_id == _DOC
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


def test_summary_does_not_swallow_unaccounted_paid_response(tmp_path: Path) -> None:
    """Optional summary degradation cannot hide missing provider accounting."""

    def unaccounted(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise ProviderAccountingError("provider accounting unavailable")

    with pytest.raises(ProviderAccountingError):
        _summarize(
            tmp_path=tmp_path,
            markdown="# Paid call\n\nBody.",
            provider=FakeModelProvider(generate_router=unaccounted),
        )


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
        if "Call kind: context-reduction" in prompt:
            return {"summary": "Balanced child group."}
        if "Section path: 0.0\n" in prompt:
            return {"summary": "Wide parent."}
        return {"summary": "x " * 400}

    result, requests = _summarize(
        tmp_path=tmp_path,
        markdown=markdown,
        provider=FakeModelProvider(generate_router=route),
    )
    reductions = [
        request.prompt
        for request in requests
        if "Call kind: context-reduction" in request.prompt
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


def test_eight_fat_child_lines_reduce_below_fan_in_and_complete(tmp_path: Path) -> None:
    markdown = "\n\n".join(
        (
            "# Parent",
            *(
                value
                for index in range(8)
                for value in (f"## Child {index}", f"Body {index}.")
            ),
        )
    )

    def route(prompt: str, response_type: str) -> dict[str, object]:
        if response_type == "RootSummaryPlacementResponse":
            return {"summary": "Fat document.", "placement_path": "/proof/fat/"}
        if "Call kind: context-reduction" in prompt:
            return {"summary": "Compressed child group."}
        if "Section path: 0.0\n" in prompt:
            return {"summary": "Recovered fat parent."}
        return {"summary": "x " * 400}

    result, requests = _summarize(
        tmp_path=tmp_path,
        markdown=markdown,
        provider=FakeModelProvider(generate_router=route),
    )

    reductions = [
        request.prompt
        for request in requests
        if "Call kind: context-reduction" in request.prompt
        and "Section path: 0.0\n" in request.prompt
    ]
    assert reductions
    assert all(
        len(prompt.split("Ordered one-liners:\n", 1)[1].splitlines())
        < SUMMARY_BALANCED_FAN_IN
        for prompt in reductions
    )
    assert result.sections[1].summary == "Recovered fat parent."
    assert result.summary_version is not None


def test_nine_short_children_compose_in_one_call_without_reduction(
    tmp_path: Path,
) -> None:
    markdown = "\n\n".join(
        (
            "# Parent",
            *(
                value
                for index in range(9)
                for value in (f"## Child {index}", f"Body {index}.")
            ),
        )
    )

    def route(prompt: str, response_type: str) -> dict[str, object]:
        if response_type == "RootSummaryPlacementResponse":
            return {"summary": "Short document.", "placement_path": "/proof/short/"}
        if "Section path: 0.0\n" in prompt:
            assert prompt.count(" | Child ") == 9
            return {"summary": "Nine children in one pass."}
        return {"summary": "Short child line."}

    result, requests = _summarize(
        tmp_path=tmp_path,
        markdown=markdown,
        provider=FakeModelProvider(generate_router=route),
    )

    parent_requests = [
        request.prompt
        for request in requests
        if "Section path: 0.0\n" in request.prompt
    ]
    assert len(parent_requests) == 1
    assert "Call kind: section-final" in parent_requests[0]
    assert not any("reduction" in request.prompt for request in requests)
    assert result.sections[1].summary == "Nine children in one pass."


def test_three_thousand_word_heading_is_capped_and_document_completes(
    tmp_path: Path,
) -> None:
    markdown = f"# {'heading ' * 3_000}\n\nBody remains small."
    provider = FakeModelProvider(
        generate_payloads={
            "SectionSummaryResponse": {"summary": "Bounded headed section."},
            "RootSummaryPlacementResponse": {
                "summary": "Bounded headed document.",
                "placement_path": "/proof/heading/",
            },
        }
    )

    result, requests = _summarize(
        tmp_path=tmp_path, markdown=markdown, provider=provider
    )

    assert result.summary_version is not None
    assert all(section.summary is not None for section in result.sections)
    assert requests
    assert all(len(request.prompt) <= SUMMARY_CALL_CHAR_CEILING for request in requests)


def test_whitespace_poor_text_never_crosses_the_character_ceiling(
    tmp_path: Path,
) -> None:
    markdown = "\n\n".join(("# Dense", *("界" * 2_000 for _ in range(20))))

    def route(prompt: str, response_type: str) -> dict[str, object]:
        if response_type == "RootSummaryPlacementResponse":
            return {"summary": "Dense document.", "placement_path": "/proof/dense/"}
        if "Call kind: block-shard" in prompt:
            return {"summary": "Dense bounded shard."}
        return {"summary": "Dense section."}

    result, requests = _summarize(
        tmp_path=tmp_path,
        markdown=markdown,
        provider=FakeModelProvider(generate_router=route),
    )

    assert (
        len(
            [
                request
                for request in requests
                if "Call kind: block-shard" in request.prompt
            ]
        )
        > 1
    )
    assert all(len(request.prompt) <= SUMMARY_CALL_CHAR_CEILING for request in requests)
    assert result.summary_version is not None


def test_overlong_multiline_provider_values_are_normalized_after_parse(
    tmp_path: Path,
) -> None:
    raw = "  first line \n second line  " + ("tail " * 200)
    provider = FakeModelProvider(
        generate_payloads={
            "SectionSummaryResponse": {"summary": raw},
            "RootSummaryPlacementResponse": {
                "summary": raw,
                "placement_path": "/proof/\n" + ("long-segment " * 100),
            },
        }
    )

    result, _ = _summarize(
        tmp_path=tmp_path, markdown="# Normalized\n\nBody.", provider=provider
    )

    values = [*(section.summary for section in result.sections), result.placement_path]
    assert result.summary_version is not None
    assert all(value is not None for value in values)
    assert all("\n" not in value for value in values if value is not None)
    assert all(len(value) <= SUMMARY_MAX_CHARS for value in values if value is not None)


def test_parent_cache_key_hashes_rendered_child_titles() -> None:
    markdown = "# Parent\n\n## Old child\n\nBody."
    blocks = blockize(document_md=markdown)
    sections = parse_heading_skeleton(
        blocks=blocks, title="Cache key proof", markdown_chars=len(markdown)
    )
    parent = sections[1]
    child = sections[2]
    old_lines = _render_child_lines(
        child_sections=(child,), child_summaries=("Unchanged summary.",)
    )
    renamed_lines = _render_child_lines(
        child_sections=(child.model_copy(update={"title": "Renamed child"}),),
        child_summaries=("Unchanged summary.",),
    )

    old_key = _summary_cache_key(
        section=parent,
        direct_blocks=(),
        child_lines=old_lines,
        model="summary/test",
        source_kind="upload",
        markdown=markdown,
    )
    renamed_key = _summary_cache_key(
        section=parent,
        direct_blocks=(),
        child_lines=renamed_lines,
        model="summary/test",
        source_kind="upload",
        markdown=markdown,
    )
    assert old_key != renamed_key


def test_one_bad_sidecar_entry_does_not_discard_later_cache_values(
    tmp_path: Path,
) -> None:
    class _SidecarCatalog:
        def summary_cache_sidecars(self, *, doc_id: UUID) -> tuple[str, ...]:
            assert doc_id == _DOC
            return ("cache/pageindex.json",)

    store = LocalFSObjectStore(root=tmp_path)
    store.write_bytes(
        key=ObjectKey("cache/pageindex.json"),
        content=(
            b'{"placement":null,"sections":['
            b'{"node_path":"0","summary_cache_key":"bad","summary":"root"},'
            b'{"node_path":"0.0","summary_cache_key":"good","summary":"usable"}]}'
        ),
    )
    summarizer = SectionSummarizer(
        catalog=_SidecarCatalog(),  # type: ignore[arg-type]
        artifact_store=store,
        model_provider=None,
        settings=SummarySettings(model="summary/test"),
    )

    cache = summarizer._load_cache(doc_id=_DOC)
    assert set(cache) == {"good"}
    assert cache["good"].summary == "usable"


def test_summary_response_schemas_are_closed_and_provider_compatible() -> None:
    section_schema = SectionSummaryResponse.model_json_schema()
    root_schema = RootSummaryPlacementResponse.model_json_schema()
    assert section_schema["additionalProperties"] is False
    assert root_schema["additionalProperties"] is False
    for field in (
        section_schema["properties"]["summary"],
        root_schema["properties"]["summary"],
        root_schema["properties"]["placement_path"],
    ):
        assert field["minLength"] == 1
        assert "maxLength" not in field
        assert "pattern" not in field
