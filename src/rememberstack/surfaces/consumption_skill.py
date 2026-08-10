"""Deployment surface for rendering and publishing the D51 consumption skill."""

from pathlib import Path
from uuid import UUID
from uuid import uuid4

from rememberstack.core import render_consumption_skill
from rememberstack.model import AssuredOperation
from rememberstack.model import ConsumptionOperation
from rememberstack.model import ConsumptionSkillContext
from rememberstack.model import PublishedMounts
from rememberstack.model import RenderedConsumptionSkill
from rememberstack.spine.assured_operations import AssuredOperationRegistry
from rememberstack.spine.consumption import ConsumptionCatalog


class ConsumptionSkillSurface:
    """Render the skill from one deployment's live registries and mounts."""

    def __init__(
        self,
        *,
        catalog: ConsumptionCatalog,
        operations: AssuredOperationRegistry,
        deployment_id: UUID,
    ) -> None:
        """Bind the renderer to one deployment and its two spine read models."""
        self._catalog = catalog
        self._operations = operations
        self._deployment_id = deployment_id

    @property
    def deployment_id(self) -> UUID:
        """The one deployment this skill surface renders."""
        return self._deployment_id

    def render(
        self, *, mounts: PublishedMounts | None = None
    ) -> RenderedConsumptionSkill:
        """Render the current deployment-specific skill without writing it."""
        if mounts is not None and mounts.deployment_id != self._deployment_id:
            raise ValueError(
                "the mount set and consumption skill serve different deployments"
            )
        operations = tuple(
            ConsumptionOperation(
                name=operation.name.value,
                description=operation.description,
                output_grain=operation.output_grain,
                answer_intent=operation.answer_intent,
            )
            for operation in _latest_operations(
                operations=self._operations.active(deployment_id=self._deployment_id)
            )
        )
        return render_consumption_skill(
            context=ConsumptionSkillContext(
                deployment=self._catalog.deployment(deployment_id=self._deployment_id),
                operations=operations,
                mounts=mounts,
            )
        )

    def publish(self, *, directory: Path, rendered: RenderedConsumptionSkill) -> Path:
        """Atomically publish one already-rendered ``SKILL.md`` artifact."""
        if rendered.deployment_id != self._deployment_id:
            raise ValueError(
                "the rendered skill and publisher serve different deployments"
            )
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / rendered.filename
        staging = directory / f".{rendered.filename}.{uuid4().hex}.tmp"
        staging.write_text(data=rendered.content, encoding="utf-8")
        staging.replace(target=destination)
        return destination


def _latest_operations(
    *, operations: tuple[AssuredOperation, ...]
) -> tuple[AssuredOperation, ...]:
    """Keep one active descriptor per operation name."""
    seen: set[str] = set()
    latest: list[AssuredOperation] = []
    for operation in operations:
        if operation.name.value in seen:
            continue
        seen.add(operation.name.value)
        latest.append(operation)
    return tuple(latest)
