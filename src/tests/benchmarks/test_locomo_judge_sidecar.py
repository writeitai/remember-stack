"""Independent grading preserves answers and checkpoints every attempted call."""

from datetime import datetime
from datetime import UTC
from decimal import Decimal
import json
from pathlib import Path
from typing import TypeVar

from benchmarks.locomo import judge_sidecar as sidecar
from benchmarks.locomo.dataset import DATASET_COMMIT
from benchmarks.locomo.dataset import item_ids_hash
from benchmarks.locomo.dataset import manifest_bytes_hash
from benchmarks.locomo.model import LoCoMoDataset
from benchmarks.locomo.model import LoCoMoQuestion
from benchmarks.locomo.model import LoCoMoSample
from benchmarks.locomo.model import LoCoMoSession
from benchmarks.locomo.model import LoCoMoTurn
from benchmarks.locomo.model import QuestionManifest
import pytest

from rememberstack.model import EmbeddingRequest
from rememberstack.model import EmbeddingResponse
from rememberstack.model import GeneratedResponse
from rememberstack.model import ModelRequest
from rememberstack.model import ProviderCallUsage
from rememberstack.model import StructuredResponseModel

ResponseT = TypeVar("ResponseT", bound=StructuredResponseModel)


class FakeProvider:
    """Return a billed structured judgment without a network request."""

    def __init__(self, *, interrupt: bool = False) -> None:
        """Capture requests and optionally simulate a crash during a paid call."""
        self.requests: list[ModelRequest] = []
        self.interrupt = interrupt

    def generate(
        self, *, request: ModelRequest, response_type: type[ResponseT]
    ) -> GeneratedResponse[ResponseT]:
        """Exercise request settings and return the requested strict shape."""
        self.requests.append(request)
        if self.interrupt:
            raise RuntimeError("simulated interruption")
        return GeneratedResponse(
            output=response_type.model_validate(
                {"label": "CORRECT", "reasoning": "The same item is named."}
            ),
            usage=ProviderCallUsage(
                model_name=request.model,
                tokens_in=10,
                tokens_out=5,
                cost_usd=Decimal("0.01"),
                latency_ms=1,
            ),
        )

    def embed(self, *, request: EmbeddingRequest) -> EmbeddingResponse:
        """Forbid retrieval or embedding during independent grading."""
        raise AssertionError("the judge must not retrieve")


@pytest.fixture
def source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a single-item frozen run with source text and an existing grade."""
    question = LoCoMoQuestion(
        item_id="fixture/qa/0000",
        sample_id="fixture",
        question="What might Kai like?",
        answer="tea; inferred from preferences",
        evidence=("D1:1",),
        category=3,
    )
    dataset = LoCoMoDataset(
        sha256="fixture-dataset",
        samples=(
            LoCoMoSample(
                sample_id="fixture",
                speaker_a="Kai",
                speaker_b="Lee",
                sessions=(
                    LoCoMoSession(
                        ordinal=1,
                        session_id="D1",
                        timestamp="1:00 pm on 1 January, 2026",
                        source_modified_at=datetime(2026, 1, 1, tzinfo=UTC),
                        source_timezone_basis="assumed_utc",
                        turns=(
                            LoCoMoTurn(
                                speaker="Kai", dia_id="D1:1", text="I enjoy tea."
                            ),
                        ),
                    ),
                ),
                questions=(question,),
            ),
        ),
    )
    monkeypatch.setattr(sidecar, "load_dataset", lambda path: dataset)
    manifest = QuestionManifest(
        tier="smoke",
        dataset_commit=DATASET_COMMIT,
        dataset_sha256=dataset.sha256,
        item_ids=(question.item_id,),
        item_ids_sha256=item_ids_hash(item_ids=(question.item_id,)),
    )
    run = tmp_path / "source"
    run.mkdir()
    (run / "run.json").write_text(
        json.dumps(
            {
                "protocol_fingerprint": "source-fingerprint",
                "dataset_path": "unused.json",
                "dataset_sha256": dataset.sha256,
                "manifest_sha256": manifest_bytes_hash(manifest=manifest),
            }
        )
    )
    (run / "manifest.json").write_text(manifest.model_dump_json())
    (run / "state.json").write_text(
        json.dumps(
            {
                "protocol_fingerprint": "source-fingerprint",
                "answers": {
                    question.item_id: {
                        "item_id": question.item_id,
                        "question": question.question,
                        "gold_answer": question.answer,
                        "generated_answer": "Tea",
                        "failure": None,
                    }
                },
                "judges": {question.item_id: {"label": "WRONG"}},
            }
        )
    )
    return run


def test_dry_run_needs_no_provider_and_creates_no_output(
    source: Path, tmp_path: Path
) -> None:
    """Planning is free and does not modify input or output files."""
    output = tmp_path / "sidecar"
    result = sidecar.grade_saved_answers(
        run_dir=source,
        output=output,
        max_calls=1,
        max_cost_usd=Decimal(1),
        execute=False,
    )
    assert result["saved_answers"] == 1
    assert not output.exists()


def test_execute_resumes_without_regrading_and_preserves_original(
    source: Path, tmp_path: Path
) -> None:
    """A saved answer is judged once under a separate, fully recorded policy."""
    provider = FakeProvider()
    before = {p.name: p.read_bytes() for p in source.iterdir()}
    kwargs = dict(
        run_dir=source,
        output=tmp_path / "sidecar",
        max_calls=1,
        max_cost_usd=Decimal(1),
        execute=True,
        provider=provider,
    )
    first = sidecar.grade_saved_answers(**kwargs)
    assert first == sidecar.grade_saved_answers(**kwargs)
    assert len(provider.requests) == 1
    assert first["correct"] == 1
    assert provider.requests[0].model == "openai/gpt-6-luna"
    assert provider.requests[0].reasoning_effort == "high"
    assert '"gold_answer": "tea"' in provider.requests[0].prompt
    assert "inferred from preferences" not in provider.requests[0].prompt
    assert before == {p.name: p.read_bytes() for p in source.iterdir()}


def test_resume_rejects_changed_source_snapshot(source: Path, tmp_path: Path) -> None:
    """New answers or grades cannot silently alter a frozen comparison."""
    kwargs = dict(
        run_dir=source,
        output=tmp_path / "sidecar",
        max_calls=1,
        max_cost_usd=Decimal(1),
        execute=True,
        provider=FakeProvider(),
    )
    sidecar.grade_saved_answers(**kwargs)
    path = source / "state.json"
    path.write_text(path.read_text() + "\n")
    with pytest.raises(ValueError, match="snapshot or grading identity changed"):
        sidecar.grade_saved_answers(**kwargs)


def test_interrupted_call_is_not_automatically_repeated(
    source: Path, tmp_path: Path
) -> None:
    """An uncertain paid attempt remains visible and requires reconciliation."""
    provider = FakeProvider(interrupt=True)
    kwargs = dict(
        run_dir=source,
        output=tmp_path / "sidecar",
        max_calls=2,
        max_cost_usd=Decimal(1),
        execute=True,
        provider=provider,
    )
    with pytest.raises(RuntimeError, match="simulated interruption"):
        sidecar.grade_saved_answers(**kwargs)
    with pytest.raises(ValueError, match="unsettled model attempt"):
        sidecar.grade_saved_answers(**kwargs)
    assert len(provider.requests) == 1


def test_optional_evidence_has_separate_identity_and_acceptance_only_policy(
    source: Path, tmp_path: Path
) -> None:
    """Only the sidecar judge receives gold-referenced source text."""
    provider = FakeProvider()
    sidecar.grade_saved_answers(
        run_dir=source,
        output=tmp_path / "sidecar",
        max_calls=1,
        max_cost_usd=Decimal(1),
        execute=True,
        provider=provider,
        with_evidence=True,
    )
    assert "never to reject an answer more strictly" in provider.requests[0].prompt
    assert "I enjoy tea." in provider.requests[0].prompt
