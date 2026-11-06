"""Explicit availability fallback for the System One substrate boundary."""

import logging
from typing import Any
from typing import Mapping

from rememberstack.adapters.openai_decisions import OpenAIDecisionsClient
from rememberstack.adapters.openai_decisions import OpenAIDecisionsSettings
from rememberstack.adapters.typesafe import TypeSafeSettings
from rememberstack.adapters.typesafe import TypeSafeSystemOneClient
from rememberstack.adapters.typesafe import TypeSafeUnavailableError
from rememberstack.model import ProviderCallUsage
from rememberstack.ports.systemone import SystemOnePort

_logger = logging.getLogger(__name__)


class AvailabilityFallbackSystemOne:
    """Use one approved backup only after a classified primary rejection."""

    def __init__(
        self, *, primary: SystemOnePort, backup: SystemOnePort, backup_model: str
    ) -> None:
        """Keep provider selection explicit rather than forwarding a Jev model ID."""
        self._primary = primary
        self._backup = backup
        self._backup_model = backup_model

    def evaluate(
        self,
        *,
        model: str | None = None,
        state: Mapping[str, Any],
        questions: Mapping[str, Any],
        timeout_s: float | None = None,
    ) -> tuple[Mapping[str, Any], ProviderCallUsage]:
        """Do not treat uncertainty, invalid outputs or accounting gaps as outages."""
        try:
            return self._primary.evaluate(
                model=model, state=state, questions=questions, timeout_s=timeout_s
            )
        except TypeSafeUnavailableError:
            _logger.warning(
                "systemone_backup_used primary=typesafe backup=openai_decisions"
            )
        return self._backup.evaluate(
            model=self._backup_model,
            state=state,
            questions=questions,
            timeout_s=timeout_s,
        )


def build_systemone_provider(*, settings: TypeSafeSettings) -> SystemOnePort:
    """Keep Jev primary, and fail configuration early when an opted-in key is absent."""
    if settings.backup == "none":
        return TypeSafeSystemOneClient(settings=settings)
    native_settings = OpenAIDecisionsSettings()
    # Construct the backup first so a missing key cannot leak a primary transport.
    backup = OpenAIDecisionsClient(settings=native_settings)
    try:
        primary = TypeSafeSystemOneClient(settings=settings)
    except Exception:
        backup.close()
        raise
    return AvailabilityFallbackSystemOne(
        primary=primary, backup=backup, backup_model=native_settings.model
    )
