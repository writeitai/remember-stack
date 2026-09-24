"""One connection resolver for the SDK and the CLI (D136 §8.2–§8.3).

:func:`resolve_connection` gives every setting one precedence — explicit
argument (CLI flag), then environment, then the stored credential file, then
what the key itself implies:

========== ====================== ================= ================= =================================
Setting    explicit               environment       credential file   derived / default
========== ====================== ================= ================= =================================
Key        ``api_key=``           REMEMBER_API_KEY  ``key``           none
           ``--api-key``
Engine URL ``base_url=``          REMEMBER_API_URL  ``api_url``       resolved from a signed key, else
           ``--api-url``                                              ``http://127.0.0.1:8000``
Project    ``project=``           REMEMBER_PROJECT  ``default_project`` the issuer's default for the key
           ``--project``
Issuer     ``--issuer``           REMEMBER_ISSUER   ``issuer``        the key's ``iss`` claim
========== ====================== ================= ================= =================================

:class:`EngineRoute` turns a connection into the base URL and bearer each
engine request uses:

- With an engine URL, requests go there.
- Otherwise a **signed key** is routed by its issuer: the issuer's
  ``remember_project_endpoint`` names the deployment serving the project. The
  answer is cached for ten minutes and re-resolved when the deployment appears
  to have moved (connection failure, ``421``, or a ``404`` that is not the
  engine's). A signed key never falls back to a local engine.
- Otherwise the default local engine.

**Stored-key origin rule.** A key read from the credential file is sent only
to the engine URL stored beside it, to its issuer (and the endpoints the
issuer advertises), or to a deployment the issuer resolved for it. Any other
destination raises :class:`~remember.errors.StoredKeyRefused`; an explicit key
(argument or ``REMEMBER_API_KEY``) goes wherever its caller directs.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import threading
import time
from typing import Final
from typing import Literal

import httpx
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import SecretStr
from pydantic import ValidationError
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict

from remember.credentials import load_credentials
from remember.credentials import StoredCredentials
from remember.errors import ProjectResolutionError
from remember.errors import StoredKeyRefused
from remember.issuer import fetch_issuer_metadata
from remember.issuer import IssuerError
from remember.issuer import KeyClaims
from remember.issuer import require_secure_url
from remember.issuer import same_origin
from remember.issuer import send_same_origin
from remember.issuer import signed_key_claims

#: Where a client without an engine URL or a signed key connects.
DEFAULT_API_URL: Final = "http://127.0.0.1:8000"
#: How long a resolved deployment URL is reused (starting value, D136 §8.3).
HOST_CACHE_TTL_SECONDS: Final = 600.0

Source = Literal["explicit", "environment", "file"]


class _Environment(BaseSettings):
    """The ``REMEMBER_*`` connection variables. Nothing else is read."""

    model_config = SettingsConfigDict(env_prefix="REMEMBER_", extra="ignore")

    api_key: SecretStr | None = None
    api_url: str | None = None
    project: str | None = None
    issuer: str | None = None


def environment_issuer() -> str | None:
    """``REMEMBER_ISSUER``, when set."""
    return _Environment.model_validate({}).issuer or None


def normalize_key(value: str) -> str:
    """Accept a bare key or ``Bearer <key>``; refuse empty or multi-line values."""
    text = value.strip()
    if text.lower().startswith("bearer "):
        text = text[7:].strip()
    if not text or text.lower() == "bearer":
        raise ValueError("the API key is empty")
    if any(character in text for character in "\r\n"):
        raise ValueError("the API key must not contain line breaks")
    return text


@dataclass(frozen=True)
class Connection:
    """The resolved settings for one client, and where each came from."""

    key: SecretStr | None
    key_source: Source | None
    api_url: str | None
    api_url_source: Source | None
    project: str | None
    issuer: str | None
    stored: StoredCredentials | None
    #: The key's claims when it is a signed key, read without verification.
    claims: KeyClaims | None

    @property
    def authorization(self) -> str | None:
        """The ``Authorization`` header value for the key, if there is one."""
        return None if self.key is None else f"Bearer {self.key.get_secret_value()}"


def resolve_connection(
    *,
    api_key: str | None = None,
    api_url: str | None = None,
    project: str | None = None,
    issuer: str | None = None,
) -> Connection:
    """Resolve key, engine URL, project and issuer with the one precedence.

    The credential file is read only when the key or the engine URL falls
    through to it, so a caller that supplies both is unaffected by a stale
    file.
    """
    env = _Environment.model_validate({})
    env_key = env.api_key.get_secret_value() if env.api_key is not None else None

    stored: StoredCredentials | None = None
    if (api_key is None and not env_key) or (api_url is None and not env.api_url):
        # A project matters only for a key routed by its issuer, which needs no
        # engine URL, so a caller naming both key and URL never needs the file.
        stored = load_credentials()

    key: SecretStr | None = None
    key_source: Source | None = None
    key_candidates: tuple[tuple[str | None, Source], ...] = (
        (api_key, "explicit"),
        (env_key, "environment"),
        (stored.key.get_secret_value() if stored and stored.key else None, "file"),
    )
    for candidate, source in key_candidates:
        if candidate:
            key, key_source = SecretStr(normalize_key(candidate)), source
            break

    resolved_url: str | None = None
    url_source: Source | None = None
    url_candidates: tuple[tuple[str | None, Source], ...] = (
        (api_url, "explicit"),
        (env.api_url, "environment"),
        (stored.api_url if stored else None, "file"),
    )
    for candidate, source in url_candidates:
        if candidate:
            resolved_url, url_source = candidate.strip(), source
            break

    claims = signed_key_claims(key.get_secret_value()) if key is not None else None
    return Connection(
        key=key,
        key_source=key_source,
        api_url=resolved_url,
        api_url_source=url_source,
        project=project or env.project or (stored.default_project if stored else None),
        issuer=issuer
        or env.issuer
        or (stored.issuer if stored else None)
        or (claims.iss if claims else None),
        stored=stored,
        claims=claims,
    )


# ---------------------------------------------------------------------------
# Project → deployment resolution
# ---------------------------------------------------------------------------


class ResolvedProject(BaseModel):
    """The issuer's answer: which deployment serves one project for this key."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    project: str = Field(min_length=1)
    name: str
    api_url: str = Field(min_length=1)


_host_cache: dict[tuple[str, str, str | None], tuple[float, ResolvedProject]] = {}
_host_lock = threading.Lock()


def clear_host_cache() -> None:
    """Forget every cached deployment URL."""
    with _host_lock:
        _host_cache.clear()


def resolve_project(
    *,
    key: str,
    claims: KeyClaims,
    project: str | None,
    http: httpx.Client,
    clock: Callable[[], float] = time.monotonic,
    refresh: bool = False,
) -> ResolvedProject:
    """Ask the key's issuer which deployment serves ``project``.

    ``project`` may be an id or a name; ``None`` asks for the key's default
    project. The answer must name a project the key covers and an ``https``
    (or loopback ``http``) URL. Cached per (issuer, key id, project) for
    :data:`HOST_CACHE_TTL_SECONDS`; ``refresh`` bypasses the cache.
    """
    key_id = claims.jti or hashlib.sha256(key.encode()).hexdigest()
    cache_key = (claims.iss.rstrip("/"), key_id, project)
    now = clock()
    if not refresh:
        with _host_lock:
            cached = _host_cache.get(cache_key)
        if cached is not None and now - cached[0] < HOST_CACHE_TTL_SECONDS:
            return cached[1]

    label = f"project {project!r}" if project else "the key's default project"
    try:
        metadata = fetch_issuer_metadata(claims.iss, http=http)
        endpoint = metadata.endpoint("remember_project_endpoint")
        request = http.build_request(
            "GET",
            endpoint,
            params={"project": project} if project else None,
            headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
        )
        response = send_same_origin(http, request)
    except (IssuerError, httpx.HTTPError) as error:
        raise ProjectResolutionError(
            detail=f"could not resolve {label} with issuer {claims.iss}: {error}"
        ) from error
    if response.status_code != 200:
        raise ProjectResolutionError(
            status_code=response.status_code,
            detail=(
                f"issuer {claims.iss} could not resolve {label} "
                f"(HTTP {response.status_code})"
            ),
        )
    try:
        resolved = ResolvedProject.model_validate(response.json())
    except (ValueError, ValidationError) as error:
        raise ProjectResolutionError(
            detail=f"issuer {claims.iss} returned an unusable answer for {label}"
        ) from error
    if not claims.covers(resolved.project):
        raise ProjectResolutionError(
            detail=(
                f"issuer {claims.iss} resolved {label} to project "
                f"{resolved.project!r}, which this key does not cover"
            )
        )
    try:
        require_secure_url(resolved.api_url, what="the resolved deployment URL")
    except IssuerError as error:
        raise ProjectResolutionError(detail=error.detail) from error
    with _host_lock:
        _host_cache[cache_key] = (now, resolved)
    return resolved


# ---------------------------------------------------------------------------
# Engine routing
# ---------------------------------------------------------------------------


class EngineRoute:
    """Where engine requests go and which bearer they carry.

    Resolution is lazy — constructing a client makes no network call — and
    re-resolution happens only for a key-routed connection.
    """

    def __init__(
        self,
        *,
        connection: Connection,
        http: httpx.Client,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Bind a resolved connection and the HTTP client used for the issuer."""
        self._connection = connection
        self._http = http
        self._clock = clock
        #: ``(project id, engine URL)`` from the issuer, set as one value under
        #: the lock. The first resolution wins; a re-resolution asks for the
        #: same project id, never the issuer's (possibly changed) default.
        self._pinned: tuple[str, str] | None = None
        self._lock = threading.Lock()

    @property
    def key_routed(self) -> bool:
        """Whether the engine URL comes from the issuer rather than configuration."""
        return self._connection.api_url is None and self._connection.claims is not None

    def target(self) -> tuple[str, str | None]:
        """The engine base URL and ``Authorization`` value for the next request."""
        connection = self._connection
        if connection.api_url is not None:
            self._check_stored_key_destination(connection.api_url)
            return connection.api_url, connection.authorization
        if connection.claims is not None and connection.key is not None:
            return self._first()[1], connection.authorization
        if connection.key_source == "file":
            raise StoredKeyRefused(
                detail=(
                    "the stored key has no engine URL and is not a signed key; "
                    "run `remember login` or `remember setup --self-hosted`"
                )
            )
        return DEFAULT_API_URL, connection.authorization

    def re_resolve(self) -> bool:
        """Re-resolve a key-routed URL; ``True`` only when it changed.

        A failure to resolve is not raised here: the caller surfaces the
        request's original error instead.
        """
        if not self.key_routed:
            return False
        project_id, previous = self._first()
        try:
            fresh = self._resolve(project=project_id, refresh=True)
        except ProjectResolutionError:
            return False
        with self._lock:
            if self._pinned is not None and self._pinned[0] == project_id:
                self._pinned = (project_id, fresh.api_url)
        return previous.rstrip("/") != fresh.api_url.rstrip("/")

    def _first(self) -> tuple[str, str]:
        """The pinned ``(project id, URL)``, resolving once under the lock."""
        with self._lock:
            if self._pinned is None:
                resolved = self._resolve(
                    project=self._connection.project, refresh=False
                )
                self._pinned = (resolved.project, resolved.api_url)
            return self._pinned

    def _resolve(self, *, project: str | None, refresh: bool) -> ResolvedProject:
        connection = self._connection
        assert connection.claims is not None and connection.key is not None
        return resolve_project(
            key=connection.key.get_secret_value(),
            claims=connection.claims,
            project=project,
            http=self._http,
            clock=self._clock,
            refresh=refresh,
        )

    def _check_stored_key_destination(self, url: str) -> None:
        """Enforce the stored-key origin rule for a configured engine URL."""
        connection = self._connection
        if connection.key_source != "file" or connection.api_url_source == "file":
            return
        stored = connection.stored
        if stored is not None and stored.api_url and same_origin(url, stored.api_url):
            return
        claims = connection.claims
        if claims is not None and connection.key is not None:
            if same_origin(url, claims.iss):
                return
            try:
                resolved = self._first()[1]
            except ProjectResolutionError:
                resolved = None
            if resolved is not None and same_origin(url, resolved):
                return
        raise StoredKeyRefused(
            detail=(
                f"the stored key is not sent to {url}: it goes only to its "
                "issuer, the deployment the issuer resolves for it, or the "
                "engine URL stored with it. Pass the key explicitly "
                "(--api-key or REMEMBER_API_KEY) to use it there"
            )
        )
