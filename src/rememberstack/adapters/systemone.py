"""Explicit availability fallback for the System One substrate boundary."""

import logging
from typing import Any
from typing import Mapping

from rememberstack.adapters.openai_decisions import OpenAIDecisionsClient
from rememberstack.adapters.openai_decisions import OpenAIDecisionsSettings
from rememberstack.adapters.openrouter import OpenRouterSettings
from rememberstack.adapters.typesafe import ConfigurationError
from rememberstack.adapters.typesafe import require_openrouter_origin
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
        except TypeSafeUnavailableError as error:
            if error.usage is not None:
                raise
            _logger.warning(
                "systemone_backup_used primary=typesafe backup=openai_decisions"
            )
        return self._backup.evaluate(
            model=self._backup_model,
            state=state,
            questions=questions,
            timeout_s=timeout_s,
        )


def validate_systemone_routing(
    *, settings: TypeSafeSettings, openrouter: OpenRouterSettings | None
) -> None:
    """Refuse a System One configuration that could break the retention policy.

    Pure and cheap, so a profile can call it when it is built, before any
    task arrives. ``openrouter`` is the deployment's provider settings.

    - ``route="openrouter"`` needs the deployment's OpenRouter settings.
    - A ZDR deployment (``openrouter.zdr``) must use ``route="openrouter"``:
      TypeSafe's own API has no zero-data-retention control, so the direct
      route would send customer content outside it.
    - Under ZDR the native OpenAI Decisions backup is refused: it would send
      the same content to OpenAI outside OpenRouter's ZDR routing.
    - Under ZDR on the ``openrouter`` route, the base URL must be OpenRouter
      itself over HTTPS: ``provider.zdr`` has no authority over another host.
    """
    zdr = openrouter is not None and openrouter.zdr
    if settings.route == "openrouter" and openrouter is None:
        raise ConfigurationError(
            "REMEMBERSTACK_TYPESAFE_ROUTE=openrouter needs the deployment's"
            " OpenRouter settings"
        )
    if zdr and settings.route != "openrouter":
        raise ConfigurationError(
            "REMEMBERSTACK_OPENROUTER_ZDR=true needs"
            " REMEMBERSTACK_TYPESAFE_ROUTE=openrouter for Jev: TypeSafe's own API"
            " cannot be held to zero data retention"
        )
    if zdr and settings.route == "openrouter" and openrouter is not None:
        require_openrouter_origin(base_url=openrouter.base_url)
    if zdr and settings.backup != "none":
        raise ConfigurationError(
            "REMEMBERSTACK_TYPESAFE_BACKUP must be none when"
            " REMEMBERSTACK_OPENROUTER_ZDR=true"
        )


def build_systemone_provider(
    *, settings: TypeSafeSettings, openrouter: OpenRouterSettings | None = None
) -> SystemOnePort:
    """Keep Jev primary, and fail configuration early when an opted-in key is absent.

    ``openrouter`` is the deployment's provider settings. With
    ``route="openrouter"`` they supply the key, base URL and ZDR flag, so each
    deployment pays with its own key. :func:`validate_systemone_routing`
    refuses every combination that could bypass zero data retention.
    """
    validate_systemone_routing(settings=settings, openrouter=openrouter)
    if settings.backup == "none":
        return TypeSafeSystemOneClient(settings=settings, openrouter=openrouter)
    native_settings = OpenAIDecisionsSettings()
    # Construct the backup first so a missing key cannot leak a primary transport.
    backup = OpenAIDecisionsClient(settings=native_settings)
    try:
        primary = TypeSafeSystemOneClient(settings=settings, openrouter=openrouter)
    except Exception:
        backup.close()
        raise
    return AvailabilityFallbackSystemOne(
        primary=primary, backup=backup, backup_model=native_settings.model
    )
