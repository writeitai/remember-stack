"""Unit-speed proofs of the acyclic D79 checker/fallback routing state machine."""

from collections.abc import Iterator
import json
from pathlib import Path
from uuid import UUID
from uuid import uuid4

import pytest

from rememberstack.adapters.selfhost import LocalFSObjectStore
from rememberstack.adapters.testing import FakeModelProvider
from rememberstack.adapters.testing import NoopCostMeter
from rememberstack.core import analyze_skeleton
from rememberstack.core import blockize
from rememberstack.core import parse_heading_skeleton
from rememberstack.core import skeleton_hash
from rememberstack.model import ClaimedWork
from rememberstack.model import ObjectKey
from rememberstack.model import PersistedSectionTree
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingLane
from rememberstack.model import ProcessingTarget
from rememberstack.model import ProviderCallError
from rememberstack.model import SectionTreeRecord
from rememberstack.model import SkeletonCheckRecord
from rememberstack.model import StructureRouteTag
from rememberstack.model import StructureSource
from rememberstack.workers import E0_STRUCTURE_VERSION
from rememberstack.workers import RoleSettings
from rememberstack.workers import SkeletonCheckSettings
from rememberstack.workers import StructureHandler
from rememberstack.workers import StructurerSettings
from rememberstack.workers.e0 import _render_check_prompt
from rememberstack.workers.e0 import SKELETON_CHECK_PROMPT_CEILING

_DEPLOYMENT = UUID("70000000-0000-0000-0000-000000000001")
_DOC = UUID("70000000-0000-0000-0000-000000000002")
_VERSION = UUID("70000000-0000-0000-0000-000000000003")
_REPRESENTATION = UUID("70000000-0000-0000-0000-000000000004")

_SOURCE = "\n\n".join(
    ("# Section A", "A" * 100, "# Section B", "B" * 100, "# Section C", "C" * 100)
)


class _Catalog:
    """Capture checker and generation appends without replacing route logic."""

    def __init__(self) -> None:
        self.checks: list[SkeletonCheckRecord] = []
        self.generations: list[SectionTreeRecord] = []

    def structure_source(self, *, representation_id: UUID) -> StructureSource:
        assert representation_id == _REPRESENTATION
        return StructureSource(
            deployment_id=_DEPLOYMENT,
            doc_id=_DOC,
            version_id=_VERSION,
            representation_id=_REPRESENTATION,
            blocks_uri="doc/blocks.json",
            markdown_uri="doc/document.md",
            title="Routing proof",
            source_kind="upload",
        )

    def record_skeleton_check(self, *, record: SkeletonCheckRecord) -> None:
        self.checks.append(record)

    def record_section_tree(self, *, record: SectionTreeRecord) -> PersistedSectionTree:
        self.generations.append(record)
        return PersistedSectionTree(
            sections=record.sections,
            placement_path=record.placement_path,
            structurer_version=record.structurer_version,
            structure_generation_id=record.structure_generation_id,
            pageindex_uri=record.pageindex_uri,
            skeleton_version=record.skeleton_version,
            skeleton_hash=record.skeleton_hash,
            skeleton_producer_family=record.skeleton_producer_family,
            skeleton_check_version=record.skeleton_check_version,
            roles_version=record.roles_version,
            summary_version=record.summary_version,
            placement_version=record.placement_version,
            selecting_check_id=record.selecting_check_id,
            route_tag=record.route_tag,
            candidate_skeleton_hash=record.candidate_skeleton_hash,
            stats_version=record.stats_version,
            stats=record.stats,
        )


def _work() -> ClaimedWork:
    return ClaimedWork(
        processing_id=uuid4(),
        deployment_id=_DEPLOYMENT,
        target_kind=ProcessingTarget.DOCUMENT_VERSION,
        target_id=_VERSION,
        stage=PipelineStage.STRUCTURE,
        component_version=E0_STRUCTURE_VERSION,
        content_hash="content",
        lane=ProcessingLane.STEADY,
        attempt=1,
        payload={
            "version_id": str(_VERSION),
            "representation_id": str(_REPRESENTATION),
        },
    )


def _router(verdicts: Iterator[str]):
    def route(prompt: str, response_type: str) -> dict[str, object]:
        del prompt
        if response_type == "SkeletonCheckResponse":
            return {"verdict": next(verdicts)}
        if response_type == "FallbackStructureResponse":
            return {
                "sections": [
                    {"anchor": "Section A", "occurrence_index": 0, "children": []},
                    {"anchor": "Section B", "occurrence_index": 0, "children": []},
                    {"anchor": "Section C", "occurrence_index": 0, "children": []},
                ]
            }
        if response_type == "RoleClassificationResponse":
            return {"assignments": []}
        raise AssertionError(response_type)

    return route


def _run(
    *,
    tmp_path: Path,
    provider: object | None,
    source: str = _SOURCE,
    min_density: float = 0,
    max_leaf: float = 1,
) -> _Catalog:
    store = LocalFSObjectStore(root=tmp_path)
    blocks = blockize(document_md=source)
    store.write_bytes(key=ObjectKey("doc/document.md"), content=source.encode())
    store.write_bytes(
        key=ObjectKey("doc/blocks.json"),
        content=json.dumps(
            {
                "markdown_chars": len(source),
                "blocks": [block.model_dump(mode="json") for block in blocks],
            }
        ).encode(),
    )
    catalog = _Catalog()
    handler = StructureHandler(
        catalog=catalog,  # type: ignore[arg-type]
        artifact_store=store,
        model_provider=provider,  # type: ignore[arg-type]
        settings=StructurerSettings(
            min_heading_density_per_10k=min_density, max_oversized_leaf_ratio=max_leaf
        ),
        check_settings=SkeletonCheckSettings(model="checker/fake"),
        role_settings=RoleSettings(model="roles/fake"),
    )
    handler.handle(work=_work(), meter=NoopCostMeter())
    return catalog


def test_not_run_short_is_persisted_without_a_provider(tmp_path: Path) -> None:
    catalog = _run(tmp_path=tmp_path, provider=None, source="# A\n\nbody\n\n# B\n")
    assert [record.check_outcome for record in catalog.checks] == ["not_run_short"]
    assert catalog.generations[-1].route_tag is StructureRouteTag.PARSER


def test_coherent_keeps_the_parser_tree(tmp_path: Path) -> None:
    provider = FakeModelProvider(generate_router=_router(iter(("coherent",))))
    catalog = _run(tmp_path=tmp_path, provider=provider)
    assert [record.check_outcome for record in catalog.checks] == ["coherent"]
    assert catalog.generations[-1].route_tag is StructureRouteTag.PARSER
    assert catalog.generations[-1].skeleton_producer_family == "N/A"


@pytest.mark.parametrize(
    "verdict",
    (
        "incoherent_repeated_boilerplate",
        "incoherent_heading_sequence",
        "incoherent_junk_titles",
        "incoherent_over_fragmented",
    ),
)
def test_every_incoherent_verdict_demotes_then_terminally_accepts_fallback(
    tmp_path: Path, verdict: str
) -> None:
    provider = FakeModelProvider(generate_router=_router(iter((verdict, "coherent"))))
    catalog = _run(tmp_path=tmp_path, provider=provider)
    assert [record.check_outcome for record in catalog.checks] == [verdict, "coherent"]
    assert [record.route_tag for record in catalog.generations] == [
        StructureRouteTag.PARSER_DEMOTED_CHECK,
        StructureRouteTag.FALLBACK_AFTER_CHECK,
    ]


def test_provider_error_is_explicit_and_fail_open(tmp_path: Path) -> None:
    class DeadProvider:
        def generate(self, *, request: object, response_type: object) -> object:
            raise ProviderCallError("offline")

        def embed(self, *, request: object) -> object:
            raise NotImplementedError

    catalog = _run(tmp_path=tmp_path, provider=DeadProvider())
    assert [record.check_outcome for record in catalog.checks] == ["provider_error"]
    failure = catalog.checks[0].provider_failure
    assert failure is not None
    assert failure["error_type"] == "ProviderCallError"
    assert failure["has_usage"] is False
    assert len(str(failure["error_fingerprint"])) == 64
    assert catalog.generations[-1].route_tag is StructureRouteTag.PARSER


def test_invalid_response_is_explicit_and_fail_open(tmp_path: Path) -> None:
    provider = FakeModelProvider(
        generate_router=lambda prompt, response_type: (
            {"verdict": "not-an-enum"}
            if response_type == "SkeletonCheckResponse"
            else {"assignments": []}
        )
    )
    catalog = _run(tmp_path=tmp_path, provider=provider)
    assert [record.check_outcome for record in catalog.checks] == ["invalid_response"]
    assert catalog.generations[-1].route_tag is StructureRouteTag.PARSER


def test_terminal_incoherence_yields_one_synthetic_root_and_stops(
    tmp_path: Path,
) -> None:
    provider = FakeModelProvider(
        generate_router=_router(
            iter(("incoherent_junk_titles", "incoherent_over_fragmented"))
        )
    )
    catalog = _run(tmp_path=tmp_path, provider=provider)
    assert len(catalog.checks) == 2
    final = catalog.generations[-1]
    assert final.route_tag is StructureRouteTag.SYNTHETIC_AFTER_CHECK
    assert len(final.sections) == 1
    assert final.sections[0].node_path == "0"


@pytest.mark.parametrize("terminal_failure", ("provider_error", "invalid_response"))
def test_terminal_provider_failures_are_explicit_and_keep_the_fallback(
    tmp_path: Path, terminal_failure: str
) -> None:
    check_calls = 0

    def router(prompt: str, response_type: str) -> dict[str, object]:
        nonlocal check_calls
        del prompt
        if response_type == "SkeletonCheckResponse":
            check_calls += 1
            if check_calls == 1:
                return {"verdict": "incoherent_junk_titles"}
            if terminal_failure == "provider_error":
                raise ProviderCallError("terminal offline")
            return {"verdict": "not-an-enum"}
        if response_type == "FallbackStructureResponse":
            return {
                "sections": [
                    {"anchor": "Section A", "occurrence_index": 0, "children": []},
                    {"anchor": "Section B", "occurrence_index": 0, "children": []},
                    {"anchor": "Section C", "occurrence_index": 0, "children": []},
                ]
            }
        if response_type == "RoleClassificationResponse":
            return {"assignments": []}
        raise AssertionError(response_type)

    catalog = _run(
        tmp_path=tmp_path, provider=FakeModelProvider(generate_router=router)
    )
    assert [record.check_outcome for record in catalog.checks] == [
        "incoherent_junk_titles",
        terminal_failure,
    ]
    assert catalog.generations[-1].route_tag is StructureRouteTag.FALLBACK_AFTER_CHECK
    assert len(catalog.generations[-1].sections) == 4


@pytest.mark.parametrize(
    ("source", "min_density", "max_leaf", "route"),
    (
        ("One block without headings.\n", 1, 1, StructureRouteTag.FALLBACK_DENSITY),
        ("# Wrapper\n\n" + "body " * 100, 0, 0.5, StructureRouteTag.FALLBACK_LEAF),
    ),
)
def test_shared_density_and_leaf_metrics_drive_direct_fallback_routes(
    tmp_path: Path,
    source: str,
    min_density: float,
    max_leaf: float,
    route: StructureRouteTag,
) -> None:
    provider = FakeModelProvider(
        generate_router=lambda prompt, response_type: (
            {"sections": []}
            if response_type == "FallbackStructureResponse"
            else {"assignments": []}
        )
    )
    catalog = _run(
        tmp_path=tmp_path,
        provider=provider,
        source=source,
        min_density=min_density,
        max_leaf=max_leaf,
    )
    assert catalog.checks == []
    generation = catalog.generations[-1]
    assert generation.route_tag is route
    parsed = parse_heading_skeleton(
        blocks=blockize(document_md=source),
        title="Routing proof",
        markdown_chars=len(source),
    )
    assert generation.candidate_skeleton_hash == skeleton_hash(sections=parsed)
    if route is StructureRouteTag.FALLBACK_LEAF:
        assert generation.skeleton_hash != generation.candidate_skeleton_hash


def test_checker_prompt_is_hard_capped_and_samples_mid_document_anomalies() -> None:
    headings = [f"# Heading {index} {'x' * 100}" for index in range(500)]
    headings[249] = "# MID TEMPLATE DUPLICATE"
    headings[250] = "# MID TEMPLATE DUPLICATE"
    source = "\n\n".join(headings)
    blocks = blockize(document_md=source)
    sections = parse_heading_skeleton(
        blocks=blocks, title="Large", markdown_chars=len(source)
    )
    analysis = analyze_skeleton(
        sections=sections, blocks=blocks, markdown_chars=len(source)
    )
    prompt = _render_check_prompt(
        source=StructureSource(
            deployment_id=_DEPLOYMENT,
            doc_id=_DOC,
            version_id=_VERSION,
            representation_id=_REPRESENTATION,
            blocks_uri="blocks",
            markdown_uri="markdown",
            title="Large",
            source_kind="upload",
        ),
        sections=sections,
        analysis=analysis,
    )
    assert len(prompt) <= SKELETON_CHECK_PROMPT_CEILING
    assert "omitted" in prompt
    assert "MID TEMPLATE DUPLICATE" in prompt


def test_roles_use_rules_then_title_classifier_with_body_as_failure_default(
    tmp_path: Path,
) -> None:
    source = "# Abstract\n\nbody\n\n# Bibliography\n\nbody\n\n# Mystery\n\nbody\n"

    def router(prompt: str, response_type: str) -> dict[str, object]:
        del prompt
        if response_type == "SkeletonCheckResponse":
            return {"verdict": "coherent"}
        if response_type == "RoleClassificationResponse":
            return {"assignments": [{"node_path": "0.2", "role": "methods"}]}
        raise AssertionError(response_type)

    catalog = _run(
        tmp_path=tmp_path,
        provider=FakeModelProvider(generate_router=router),
        source=source,
    )
    final = catalog.generations[-1]
    assert [section.role for section in final.sections] == [
        "body",
        "abstract",
        "references",
        "methods",
    ]

    failed = _run(tmp_path=tmp_path / "failed", provider=None, source=source)
    assert [section.role for section in failed.generations[-1].sections] == [
        "body",
        "abstract",
        "references",
        "body",
    ]
