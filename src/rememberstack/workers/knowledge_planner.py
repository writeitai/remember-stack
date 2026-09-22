"""Transcripted stock-harness planner and independent reflection worker."""

from collections.abc import Callable
from datetime import datetime
from datetime import UTC
from decimal import Decimal
import json
from pathlib import PurePosixPath
import traceback
from typing import Final
from typing import Protocol
from uuid import UUID
from uuid import uuid4

from pydantic import Field
from pydantic import field_validator
from pydantic import TypeAdapter
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict

from rememberstack.core import knowledge_planning_input_hash
from rememberstack.core import knowledge_summary_hash
from rememberstack.core import primary_knowledge_plan_trigger
from rememberstack.core.storage_routing import storage_class_for_internal
from rememberstack.model import KnowledgeAgentSessionResult
from rememberstack.model import KnowledgePlanDecisionResult
from rememberstack.model import KnowledgePlannerSessionRequest
from rememberstack.model import KnowledgePlanningSnapshot
from rememberstack.model import KnowledgePlanProposal
from rememberstack.model import KnowledgePlanRunKind
from rememberstack.model import KnowledgePlanRunStatus
from rememberstack.model import KnowledgePlanRunWrite
from rememberstack.model import ObjectKey
from rememberstack.ports import MountPublisherPort
from rememberstack.ports import ObjectStorePort
from rememberstack.spine.knowledge import KnowledgeControlPlane

KNOWLEDGE_PLANNER_VERSION: Final = "k-planner-2026.07"

_PLANNER_PROMPT: Final = """You maintain Plane-K structure, never page content.
Your working directory is the declared output/ surface. Read ../INSTRUCTIONS.md,
../input/planning_snapshot.json, ../input/decision_schema.json, and the read-only memory
mount locations in ../context/memory_mounts.json. Do not use the internet, initialize git,
commit, edit a page body, or write outside the declared output.

Write exactly decisions.json in the current directory (archived as output/decisions.json):
one JSON array of zero or more schema-valid structural proposals. Use only create_page,
split_page, merge_pages, move_page, retire_page, adjust_rule, or convert_kind. Every created
page must be compiled and have a complete mechanical rule set. Base decisions only on the
supplied orphan, overflow, community, and writer-suggestion inputs. The deterministic driver
computes impact, applies the automatic band, and owns every file and database mutation. Your
output proposes; it never takes action.
"""

_REFLECTION_PROMPT: Final = """You are the independent Plane-K reflection seat.
Your working directory is the declared output/ surface. Read ../INSTRUCTIONS.md,
../input/planning_snapshot.json, ../input/decision_schema.json, and the read-only memory
mount locations in ../context/memory_mounts.json. Inspect the compiled tree and health
metrics for orphan volume, page sizes, uncited rates, and navigation problems. Do not use the
internet, initialize git, commit, edit any page, or write outside the declared output.

Write exactly decisions.json in the current directory (archived as output/decisions.json):
one JSON array of zero or more schema-valid structural proposals. Use only create_page,
split_page, merge_pages, move_page, retire_page, adjust_rule, or convert_kind. Every proposal
is review-band by contract. Your output proposes structural changes only; the deterministic
driver owns every mutation and commit.
"""

_PROPOSALS_ADAPTER = TypeAdapter(tuple[KnowledgePlanProposal, ...])


def _utc_now() -> datetime:
    """Return the current aware UTC time for injectable worker bookkeeping."""
    return datetime.now(tz=UTC)


class KnowledgePlannerSettings(BaseSettings):
    """Settings-owned model split, timeout, band, and transcript namespace."""

    model_config = SettingsConfigDict(env_prefix="REMEMBERSTACK_K_PLANNER_")

    planner_model: str = Field(min_length=1)
    planner_model_family: str = Field(min_length=1)
    reflection_model: str = Field(min_length=1)
    reflection_model_family: str = Field(min_length=1)
    timeout_seconds: int = Field(gt=0)
    auto_apply_max_expected_impact: Decimal = Field(ge=Decimal("0"))
    transcript_prefix: str = Field(min_length=1)

    @field_validator("reflection_model_family")
    @classmethod
    def require_independent_reflection_family(cls, value: str, info: object) -> str:
        """Bind D53 producer/checker independence in deploy-time settings."""
        data = getattr(info, "data", {})
        planner_family = data.get("planner_model_family")
        if planner_family is not None and value.casefold() == planner_family.casefold():
            raise ValueError(
                "reflection must use a different model family than planner"
            )
        return value

    @field_validator("transcript_prefix")
    @classmethod
    def require_safe_transcript_prefix(cls, value: str) -> str:
        """Keep immutable transcripts under one normalized relative prefix."""
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or str(path) != value:
            raise ValueError("transcript_prefix must be a normalized relative path")
        return value.rstrip("/")


class KnowledgePlannerError(RuntimeError):
    """A planner or reflection session failed its declared-output contract."""


class KnowledgePlannerSession(Protocol):
    """The generic stock-harness seam used by both structural seats."""

    def run_session(
        self, *, request: KnowledgePlannerSessionRequest
    ) -> KnowledgeAgentSessionResult:
        """Return raw declared files and the complete process transcript."""
        ...


class KnowledgePlannerWorker:
    """Run one decisions-only planner or independent reflection session."""

    def __init__(
        self,
        *,
        control_plane: KnowledgeControlPlane,
        agent_session: KnowledgePlannerSession,
        transcript_store: ObjectStorePort,
        mount_publisher: MountPublisherPort,
        settings: KnowledgePlannerSettings,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        """Bind the harness, immutable ledger, mounts, and deterministic policy."""
        self._control_plane = control_plane
        self._agent_session = agent_session
        self._transcript_store = transcript_store
        self._mount_publisher = mount_publisher
        self._settings = settings
        self._clock = clock

    def run_planner(
        self, *, snapshot: KnowledgePlanningSnapshot
    ) -> tuple[KnowledgePlanDecisionResult, ...]:
        """Run the producing structural planner only when a trigger is present."""
        return self._run(snapshot=snapshot, run_kind=KnowledgePlanRunKind.PLANNER)

    def run_reflection(
        self, *, snapshot: KnowledgePlanningSnapshot
    ) -> tuple[KnowledgePlanDecisionResult, ...]:
        """Run the cross-family checker seat with review-only consequences."""
        return self._run(snapshot=snapshot, run_kind=KnowledgePlanRunKind.REFLECTION)

    def _run(
        self, *, snapshot: KnowledgePlanningSnapshot, run_kind: KnowledgePlanRunKind
    ) -> tuple[KnowledgePlanDecisionResult, ...]:
        """Archive first, parse second, then ask the control plane to route effects."""
        run_id = uuid4()
        session_id = uuid4()
        input_hash = knowledge_planning_input_hash(snapshot=snapshot)
        trigger = primary_knowledge_plan_trigger(snapshot=snapshot, run_kind=run_kind)
        prompt, model = self._seat_configuration(run_kind=run_kind)
        component_version = _planner_component_version(
            settings=self._settings, run_kind=run_kind, prompt=prompt
        )
        transcript_uri: str | None = None
        result: KnowledgeAgentSessionResult | None = None
        try:
            mounts = self._mount_publisher.publish(deployment_id=snapshot.deployment_id)
            if mounts.deployment_id != snapshot.deployment_id:
                raise KnowledgePlannerError("mount publisher crossed deployments")
            result = self._agent_session.run_session(
                request=KnowledgePlannerSessionRequest(
                    session_id=session_id,
                    model=model,
                    prompt=prompt,
                    timeout_seconds=self._settings.timeout_seconds,
                    input_files={
                        "INSTRUCTIONS.md": prompt,
                        "input/planning_snapshot.json": (
                            f"{snapshot.model_dump_json(indent=2)}\n"
                        ),
                        "input/decision_schema.json": (
                            f"{json.dumps(_PROPOSALS_ADAPTER.json_schema(), indent=2, sort_keys=True)}\n"
                        ),
                        "context/memory_mounts.json": (
                            f"{mounts.model_dump_json(indent=2)}\n"
                        ),
                    },
                    mounts=mounts,
                )
            )
            if result.session_id != session_id:
                raise KnowledgePlannerError("planner returned a different session ID")
            transcript_uri = self._archive_transcript(
                snapshot=snapshot,
                run_kind=run_kind,
                session_id=session_id,
                transcript=result.transcript,
            )
            if result.timed_out:
                raise KnowledgePlannerError("planner session timed out")
            if result.exit_code != 0:
                raise KnowledgePlannerError(
                    f"planner session exited with status {result.exit_code}"
                )
            raw_decisions = result.output_files.get("output/decisions.json")
            if raw_decisions is None:
                raise KnowledgePlannerError("planner omitted output/decisions.json")
            try:
                proposals = _PROPOSALS_ADAPTER.validate_json(raw_decisions)
            except ValueError as error:
                raise KnowledgePlannerError(
                    "planner decisions violate the typed contract"
                ) from error
            return self._control_plane.record_plan_proposals(
                run=KnowledgePlanRunWrite(
                    run_id=run_id,
                    deployment_id=snapshot.deployment_id,
                    scope_id=snapshot.scope_id,
                    run_kind=run_kind,
                    trigger=trigger,
                    component_version=component_version,
                    input_hash=input_hash,
                    session_transcript_uri=transcript_uri,
                    status=KnowledgePlanRunStatus.SUCCEEDED,
                    tokens=result.tokens,
                    cost_usd=_cost_decimal(result=result),
                ),
                proposals=proposals,
                auto_apply_max_expected_impact=(
                    self._settings.auto_apply_max_expected_impact
                ),
            )
        except Exception as error:
            failure_trace = traceback.format_exc()
            if transcript_uri is None:
                try:
                    transcript_uri = self._archive_transcript(
                        snapshot=snapshot,
                        run_kind=run_kind,
                        session_id=session_id,
                        transcript=failure_trace,
                    )
                except Exception as transcript_error:
                    raise ExceptionGroup(
                        "planner failure and transcript archival both failed",
                        (error, transcript_error),
                    ) from None
            try:
                self._control_plane.record_plan_run_failure(
                    run=KnowledgePlanRunWrite(
                        run_id=run_id,
                        deployment_id=snapshot.deployment_id,
                        scope_id=snapshot.scope_id,
                        run_kind=run_kind,
                        trigger=trigger,
                        component_version=component_version,
                        input_hash=input_hash,
                        session_transcript_uri=transcript_uri,
                        status=KnowledgePlanRunStatus.FAILED,
                        failure=failure_trace,
                        tokens=None if result is None else result.tokens,
                        cost_usd=(
                            None if result is None else _cost_decimal(result=result)
                        ),
                    )
                )
            except Exception as ledger_error:
                raise ExceptionGroup(
                    "planner execution and failure-ledger recording both failed",
                    (error, ledger_error),
                ) from None
            raise

    def _seat_configuration(self, *, run_kind: KnowledgePlanRunKind) -> tuple[str, str]:
        """Select the settings-owned producer or checker model."""
        if run_kind is KnowledgePlanRunKind.REFLECTION:
            return _REFLECTION_PROMPT, self._settings.reflection_model
        return _PLANNER_PROMPT, self._settings.planner_model

    def _archive_transcript(
        self,
        *,
        snapshot: KnowledgePlanningSnapshot,
        run_kind: KnowledgePlanRunKind,
        session_id: UUID,
        transcript: str,
    ) -> str:
        """Persist the complete session before inspecting any declared output."""
        key = ObjectKey(
            f"{self._settings.transcript_prefix}/{snapshot.deployment_id}/"
            f"{run_kind.value}/{self._clock().date().isoformat()}/{session_id}.json"
        )
        self._transcript_store.write_bytes(
            key=key,
            content=transcript.encode("utf-8"),
            storage_class=storage_class_for_internal(),
        )
        return key.root


def _planner_component_version(
    *, settings: KnowledgePlannerSettings, run_kind: KnowledgePlanRunKind, prompt: str
) -> str:
    """Make model, family, prompt, timeout, and band policy ledger-visible."""
    if run_kind is KnowledgePlanRunKind.REFLECTION:
        model = settings.reflection_model
        family = settings.reflection_model_family
    else:
        model = settings.planner_model
        family = settings.planner_model_family
    fingerprint = knowledge_summary_hash(
        summary=(
            f"{run_kind.value}\n{prompt}\n{model}\n{family}\n"
            f"{settings.timeout_seconds}\n"
            f"{settings.auto_apply_max_expected_impact}"
        )
    )
    return f"{KNOWLEDGE_PLANNER_VERSION}:{run_kind.value}:{fingerprint[:16]}"


def _cost_decimal(*, result: KnowledgeAgentSessionResult) -> Decimal | None:
    """Convert adapter-native floating metering without binary artifacts."""
    return None if result.cost_usd is None else Decimal(str(result.cost_usd))
