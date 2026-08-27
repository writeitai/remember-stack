"""Focused D67 task-queue signature, route, vocabulary, and UTC contract tests."""

from datetime import datetime
from datetime import timedelta
from datetime import timezone
from inspect import Parameter
from inspect import signature
from typing import get_type_hints
from uuid import UUID
from uuid import uuid4

from pydantic import TypeAdapter
from pydantic import ValidationError
import pytest

from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingLane
from rememberstack.model import QueueRoute
from rememberstack.model import UTCDateTime
from rememberstack.ports import TaskQueuePort

_PIPELINE_STAGE_VALUES = (
    "ingest",
    "convert",
    "structure",
    "crossref",
    "chunk",
    "embed_chunk",
    "extract_claims",
    "embed_claim",
    "ground_claims",
    "resolve_entities",
    "normalize_relations",
    "adjudicate_supersession",
    "adjudicate_observations",
    "embed_relation",
    "label_relation",
    "embed_observation",
    "label_observation",
    "refresh_profile",
    "compile_knowledge",
    "reflect_knowledge",
    "lint_knowledge",
    "reconcile",
    "dispatch_knowledge",
    "hard_forget",
)


class RecordingTaskQueue:
    """Delivery-only fake that records exactly one D67 announcement."""

    def __init__(self) -> None:
        """Initialize without an announcement."""
        self.announcement: tuple[UUID, QueueRoute, datetime] | None = None

    def announce(
        self,
        *,
        processing_id: UUID,
        route_snapshot: QueueRoute,
        not_before_snapshot: UTCDateTime,
    ) -> None:
        """Record delivery hints without creating or changing work state."""
        self.announcement = (processing_id, route_snapshot, not_before_snapshot)


_queue_assignment: TaskQueuePort = RecordingTaskQueue()


def test_queue_protocol_has_exactly_one_operation_and_exact_signature() -> None:
    """Prevent queue adapters from acquiring Postgres work-state authority."""
    public_operations = {
        name
        for name, value in TaskQueuePort.__dict__.items()
        if not name.startswith("_") and callable(value)
    }
    operation_signature = signature(TaskQueuePort.announce)
    parameters = tuple(operation_signature.parameters.values())
    hints = get_type_hints(TaskQueuePort.announce, include_extras=True)

    assert public_operations == {"announce"}
    assert tuple(parameter.name for parameter in parameters) == (
        "self",
        "processing_id",
        "route_snapshot",
        "not_before_snapshot",
    )
    assert all(parameter.kind is Parameter.KEYWORD_ONLY for parameter in parameters[1:])
    assert hints["processing_id"] is UUID
    assert hints["route_snapshot"] is QueueRoute
    assert hints["not_before_snapshot"] == UTCDateTime
    assert hints["return"] is type(None)


def test_queue_route_has_exact_authoritative_vocabulary_and_nullable_lane() -> None:
    """Bind the route snapshot to schema stages and only steady/backfill/none lanes."""
    deployment_id = uuid4()
    plane_e_route = QueueRoute(
        deployment_id=deployment_id,
        stage=PipelineStage.EXTRACT_CLAIMS,
        lane=ProcessingLane.STEADY,
    )
    aggregate_route = QueueRoute(
        deployment_id=deployment_id, stage=PipelineStage.COMPILE_KNOWLEDGE, lane=None
    )

    assert tuple(stage.value for stage in PipelineStage) == _PIPELINE_STAGE_VALUES
    assert tuple(lane.value for lane in ProcessingLane) == ("steady", "backfill")
    assert set(QueueRoute.model_fields) == {"deployment_id", "stage", "lane"}
    assert plane_e_route.lane is ProcessingLane.STEADY
    assert aggregate_route.lane is None

    with pytest.raises(ValidationError):
        QueueRoute.model_validate(
            {
                "deployment_id": deployment_id,
                "stage": "compile_knowledge",
                "lane": "aggregate",
            }
        )

    with pytest.raises(ValidationError):
        QueueRoute.model_validate(
            {
                "deployment_id": deployment_id,
                "stage": "compile_knowledge",
                "lane": None,
                "queue_name": "provider-owned-name",
            }
        )


def test_utc_datetime_rejects_naive_and_non_utc_values() -> None:
    """Enforce timezone-aware UTC before a due snapshot reaches an adapter."""
    adapter = TypeAdapter(UTCDateTime)
    utc_value = datetime(2026, 7, 17, 20, 0, tzinfo=timezone.utc)

    assert adapter.validate_python(utc_value) is utc_value

    with pytest.raises(ValidationError):
        adapter.validate_python(datetime(2026, 7, 17, 20, 0))

    with pytest.raises(ValidationError):
        adapter.validate_python(
            datetime(2026, 7, 17, 21, 0, tzinfo=timezone(timedelta(hours=1)))
        )


def test_queue_fake_preserves_identity_and_non_authoritative_snapshots() -> None:
    """Exercise a named-argument announcement without an enqueue or mutation API."""
    queue = RecordingTaskQueue()
    processing_id = uuid4()
    route = QueueRoute(
        deployment_id=uuid4(), stage=PipelineStage.COMPILE_KNOWLEDGE, lane=None
    )
    due = datetime(2026, 7, 17, 20, 0, tzinfo=timezone.utc)

    queue.announce(
        processing_id=processing_id, route_snapshot=route, not_before_snapshot=due
    )

    assert queue.announcement == (processing_id, route, due)
