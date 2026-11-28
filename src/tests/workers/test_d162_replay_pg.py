"""The strict benchmark checker replays grounded extraction from recorded wire replies."""

from argparse import Namespace
import json
from pathlib import Path
from uuid import UUID

from benchmarks.d162_replay import _clone
from benchmarks.d162_replay import _work
from benchmarks.d162_replay import replay
import httpx
from sqlalchemy import text

from rememberstack.adapters import OpenRouterModelProvider
from rememberstack.adapters import OpenRouterSettings
from rememberstack.adapters import PostgresP1Index
from rememberstack.adapters.testing import NoopCostMeter
from rememberstack.core import chunker_version
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingLane
from rememberstack.model import RunResultOutcome
from rememberstack.ports.p1_index import P1_VECTOR_DIMENSIONS
from rememberstack.workers import E1_EMBED_VERSION
from rememberstack.workers import E1Settings
from rememberstack.workers import E2_EXTRACTOR_VERSION
from rememberstack.workers import E2Settings
from rememberstack.workers import EmbedChunksHandler
from rememberstack.workers import ExtractClaimsHandler
from tests.workers.test_reuse_lifecycle import _canned
from tests.workers.test_reuse_lifecycle import _DEPLOYMENT_ID
from tests.workers.test_reuse_lifecycle import _PARAMS
from tests.workers.test_reuse_lifecycle import _ReuseRig
from tests.workers.test_reuse_lifecycle import (
    bootstrapped_deployment as bootstrapped_deployment,
)
from tests.workers.test_reuse_lifecycle import database_engine as database_engine
from tests.workers.test_reuse_lifecycle import rig as rig


def test_strict_replay_compares_persisted_claims_and_occurrence_spans(
    rig: _ReuseRig, tmp_path: Path
) -> None:
    model = "qwen/qwen3-embedding-8b"
    endpoint = "https://openrouter.ai/api/v1"
    item = rig.observe(content="Every traveler needs a receipt.")
    for stage in (PipelineStage.CONVERT, PipelineStage.STRUCTURE, PipelineStage.CHUNK):
        assert (
            rig.worker.run_one(
                deployment_id=_DEPLOYMENT_ID, stage=stage, lane=ProcessingLane.STEADY
            ).outcome
            is RunResultOutcome.SUCCEEDED
        )
    with rig.engine.connect() as connection:
        representation = connection.execute(
            text("""
            SELECT current_representation_id FROM document_versions WHERE version_id=:id
        """),
            {"id": item.version_id},
        ).scalar_one()
    generation = chunker_version(params=_PARAMS)
    chunk_ids = rig.chunk_catalog.list_chunk_ids(
        representation_id=representation, chunker_version=generation
    )
    source = dict(
        deployment_id=_DEPLOYMENT_ID,
        representation_id=representation,
        version_id=item.version_id,
        content_hash=item.content_hash,
    )
    records: list[dict[str, object]] = []
    target: UUID = item.version_id
    stage = PipelineStage.EMBED_CHUNK

    def record(request: httpx.Request) -> httpx.Response:
        sent = json.loads(request.content)
        if stage is PipelineStage.EMBED_CHUNK:
            reply: dict[str, object] = {
                "model": model,
                "data": [
                    {"index": i, "embedding": [1.0] * P1_VECTOR_DIMENSIONS}
                    for i, _ in enumerate(sent["input"])
                ],
                "usage": {"prompt_tokens": 16, "total_tokens": 16, "cost": 0},
            }
        else:
            result = _canned(
                sent["messages"][0]["content"],
                sent["response_format"]["json_schema"]["name"],
            )
            reply = {
                "model": sent["model"],
                "choices": [
                    {
                        "message": {"content": json.dumps(result)},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 32, "completion_tokens": 16, "cost": 0},
            }
        records.append(
            dict(
                stage=stage.value,
                target_id=str(target),
                method=request.method,
                url=str(request.url),
                request=sent,
                status=200,
                reply=reply,
            )
        )
        return httpx.Response(200, json=reply)

    provider = OpenRouterModelProvider(
        settings=OpenRouterSettings(
            api_key="offline",
            base_url=endpoint,
            embedding_provider="nebius",
            zdr=False,
            chat_provider_only=None,
            chat_provider_order=None,
        )
    )
    provider._client.close()
    with httpx.Client(
        transport=httpx.MockTransport(record), base_url=endpoint
    ) as client:
        provider._client = client
        embed = EmbedChunksHandler(
            catalog=rig.chunk_catalog,
            artifact_store=rig.artifact_store,
            model_provider=provider,
            params=_PARAMS,
            embedding_model=model,
            settings=E1Settings(),
            chunk_index=PostgresP1Index(engine=rig.engine, embedding_model=model),
        )
        meter = NoopCostMeter()
        embed.handle(
            work=_work(
                source=source, stage=stage, target=target, generation=E1_EMBED_VERSION
            ),
            meter=meter,
        )
        extract = ExtractClaimsHandler(
            catalog=rig.claim_catalog,
            chunk_catalog=rig.chunk_catalog,
            artifact_store=rig.artifact_store,
            model_provider=provider,
            settings=E2Settings(),
            chunker_version=generation,
        )
        for stage in (PipelineStage.EXTRACT_CLAIMS, PipelineStage.GROUND_CLAIMS):
            for target in chunk_ids:
                extract.handle(
                    work=_work(
                        source=source,
                        stage=stage,
                        target=target,
                        generation=E2_EXTRACTOR_VERSION,
                        is_chunk=True,
                    ),
                    meter=meter,
                )

    tape = tmp_path / "wire.json"
    tape.write_text(json.dumps(records))
    rig.engine.dispose()
    with _clone(rig.engine.url.render_as_string(hide_password=False)) as (
        baseline,
        candidate,
    ):
        result = replay(
            Namespace(
                packing=_PARAMS.model_dump_json(),
                representation=None,
                wire_tape=tape,
                model=model,
                routing="custom",
                base_url=endpoint,
                embedding_provider="nebius",
                artifacts=tmp_path / "artifacts",
                batch_size=64,
                extract_model=E2Settings().extract_model,
            ),
            baseline,
            candidate,
        )
    assert result["wire_verified"] is True
    assert result["splits"] == result["live_calls"] == 0
    tables = result["tables"]
    assert isinstance(tables, dict)
    assert tables["claims"]["rows"] == tables["chunk_claims"]["rows"] == 1
    assert all(table["equal"] for table in tables.values())
