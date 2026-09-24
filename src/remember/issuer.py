"""Talking to a key issuer: signed-key claims, metadata, device login, revocation.

An issuer is the service that mints signed keys (remember.dev's account
service, or any operator's equivalent), identified by the HTTPS URL in each
key's ``iss`` claim (D136 §8.1). The client finds everything else from the
issuer's OAuth authorization-server metadata (RFC 8414):

- ``device_authorization_endpoint`` / ``token_endpoint`` — ``remember login``,
  the OAuth device grant (RFC 8628);
- ``revocation_endpoint`` — ``remember logout`` and re-login (RFC 7009);
- ``remember_project_endpoint`` — which deployment serves a project
  (:mod:`remember.connection`);
- ``remember_account_endpoint`` — the issuer's account API
  (``client.account``);
- ``remember_mcp_endpoint`` — the hosted MCP endpoint.

Every request here follows redirects only within the same origin, so a key is
never handed to another host by a redirect.
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from datetime import datetime
from datetime import UTC
from ipaddress import ip_address
import json
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

from remember.errors import MemoryApiError

#: The issuer ``remember login`` and ``remember setup --cloud`` use when no
#: other is given. Nothing else defaults to it.
DEFAULT_ISSUER: Final = "https://remember.dev"
#: The OAuth ``client_id`` the CLI presents; the device grant is a public client.
CLIENT_ID: Final = "remember-cli"
DEVICE_GRANT_TYPE: Final = "urn:ietf:params:oauth:grant-type:device_code"

_MAX_REDIRECTS: Final = 3
_POLL_CAP_SECONDS: Final = 30.0
_SLOW_DOWN_STEP_SECONDS: Final = 5.0


class IssuerError(MemoryApiError):
    """The issuer could not be used: unreachable, refused, or answered badly."""


class DeviceGrantError(IssuerError):
    """The device grant ended without a key (denied, expired, or failed)."""


# ---------------------------------------------------------------------------
# Signed keys
# ---------------------------------------------------------------------------


class KeyClaims(BaseModel):
    """The claims of a signed key, read **without verifying the signature**.

    Only for routing and display: the engine perimeter verifies every key.
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    iss: str = Field(min_length=1)
    jti: str | None = None
    sub: str | None = None
    org: str | None = None
    exp: int | None = None
    projects: list[str] | Literal["org:*"] | None = None
    permissions: list[str] = Field(default_factory=list)

    def covers(self, project_id: str) -> bool:
        """Whether this key's ``projects`` claim includes ``project_id``."""
        if self.projects == "org:*":
            return True
        return self.projects is not None and project_id in self.projects

    @property
    def expires_at(self) -> datetime | None:
        """``exp`` as an aware datetime."""
        return None if self.exp is None else datetime.fromtimestamp(self.exp, UTC)


def signed_key_claims(key: str) -> KeyClaims | None:
    """The claims of a signed key, or ``None`` for any other bearer.

    A signed key is a compact JWS, bare or behind a letters-only prefix
    (``rmb_eyJ…``). A bearer that has that shape but unreadable claims raises
    ``ValueError`` rather than being mistaken for a shared secret, because a
    shared secret is sent to a default local engine and a signed key never is.
    """
    token = key.strip()
    if not token.startswith("eyJ"):
        prefix, separator, rest = token.partition("_")
        if not (separator and prefix.isalpha() and prefix.isascii()):
            return None
        if not rest.startswith("eyJ"):
            return None
        token = rest
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("the key looks like a signed key but is not a JWS")
    try:
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        return KeyClaims.model_validate(payload)
    except (ValueError, ValidationError) as error:
        raise ValueError(
            "the key looks like a signed key but its claims cannot be read"
        ) from error


# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------


def is_loopback(host: str) -> bool:
    """``localhost`` or a loopback IP literal."""
    bare = host.strip("[]").lower()
    if bare == "localhost":
        return True
    try:
        return ip_address(bare).is_loopback
    except ValueError:
        return False


def require_secure_url(url: str, *, what: str) -> httpx.URL:
    """Accept ``https``, or ``http`` only on a loopback host."""
    try:
        parsed = httpx.URL(url)
    except (httpx.InvalidURL, TypeError) as error:
        raise IssuerError(detail=f"{what} is not a valid URL: {url!r}") from error
    if parsed.scheme == "https" and parsed.host:
        return parsed
    if parsed.scheme == "http" and parsed.host and is_loopback(parsed.host):
        return parsed
    raise IssuerError(
        detail=f"{what} must be https (or http on a loopback address): {url!r}"
    )


def origin(url: str | httpx.URL) -> tuple[str, str, int | None]:
    """Scheme, host and effective port — what "same origin" compares."""
    parsed = httpx.URL(url) if isinstance(url, str) else url
    port = parsed.port or {"https": 443, "http": 80}.get(parsed.scheme)
    return (parsed.scheme, (parsed.host or "").rstrip(".").lower(), port)


def same_origin(left: str | httpx.URL, right: str | httpx.URL) -> bool:
    """Whether two URLs share scheme, host and port."""
    try:
        return origin(left) == origin(right)
    except (httpx.InvalidURL, TypeError):
        return False


def send_same_origin(
    http: httpx.Client, request: httpx.Request, *, max_redirects: int = _MAX_REDIRECTS
) -> httpx.Response:
    """Send ``request``, following redirects only within its origin.

    A cross-origin redirect is an error: following it would hand the bearer
    or the device code to another host.
    """
    current = request
    for _ in range(max_redirects + 1):
        response = http.send(current)
        if not response.is_redirect:
            return response
        location = response.headers.get("location")
        response.close()
        if not location:
            raise IssuerError(detail=f"{current.url} redirected without a Location")
        target = current.url.join(location)
        if not same_origin(current.url, target):
            raise IssuerError(
                detail=f"refusing a cross-origin redirect from {current.url} to {target}"
            )
        # Same method and body on every hop: issuer endpoints have no use for
        # the POST-to-GET rewrite, and a same-origin hop keeps every header.
        current = http.build_request(
            current.method, target, headers=current.headers, content=current.content
        )
    raise IssuerError(detail=f"too many redirects from {request.url}")


# ---------------------------------------------------------------------------
# Metadata (RFC 8414)
# ---------------------------------------------------------------------------


class IssuerMetadata(BaseModel):
    """The fields of the issuer's authorization-server metadata the client uses."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    issuer: str
    device_authorization_endpoint: str | None = None
    token_endpoint: str | None = None
    revocation_endpoint: str | None = None
    jwks_uri: str | None = None
    remember_mcp_endpoint: str | None = None
    remember_project_endpoint: str | None = None
    remember_account_endpoint: str | None = None

    def endpoint(
        self,
        name: Literal[
            "device_authorization_endpoint",
            "token_endpoint",
            "revocation_endpoint",
            "remember_project_endpoint",
            "remember_account_endpoint",
            "remember_mcp_endpoint",
        ],
    ) -> str:
        """One advertised endpoint, checked; raises when the issuer has none."""
        value = getattr(self, name)
        if not value:
            raise IssuerError(detail=f"issuer {self.issuer} does not advertise {name}")
        require_secure_url(value, what=name)
        return str(value)


def normalize_issuer(issuer: str) -> str:
    """Trim whitespace and trailing slashes; require https (or loopback http)."""
    text = issuer.strip().rstrip("/")
    require_secure_url(text, what="issuer")
    return text


def metadata_url(issuer: str) -> str:
    """RFC 8414 §3: the well-known segment goes between the host and any path."""
    parsed = httpx.URL(normalize_issuer(issuer))
    path = parsed.path.rstrip("/")
    return str(
        parsed.copy_with(
            path=f"/.well-known/oauth-authorization-server{path}", query=None
        )
    )


_metadata_cache: dict[str, IssuerMetadata] = {}
_metadata_lock = threading.Lock()


def fetch_issuer_metadata(issuer: str, *, http: httpx.Client) -> IssuerMetadata:
    """The issuer's metadata, fetched once per process.

    The document must name the same issuer it was fetched for (RFC 8414 §3.3),
    so a misconfigured or hostile redirect cannot substitute another issuer's
    endpoints.
    """
    normalized = normalize_issuer(issuer)
    with _metadata_lock:
        cached = _metadata_cache.get(normalized)
    if cached is not None:
        return cached
    url = metadata_url(normalized)
    try:
        response = send_same_origin(
            http, http.build_request("GET", url, headers={"Accept": "application/json"})
        )
    except httpx.HTTPError as error:
        raise IssuerError(
            detail=f"issuer metadata is unavailable at {url}: {error}"
        ) from error
    if response.status_code != 200:
        raise IssuerError(
            detail=f"issuer metadata is unavailable at {url} (HTTP {response.status_code})"
        )
    try:
        metadata = IssuerMetadata.model_validate(response.json())
    except (ValueError, ValidationError) as error:
        raise IssuerError(detail=f"issuer metadata at {url} is malformed") from error
    if metadata.issuer.rstrip("/") != normalized:
        raise IssuerError(
            detail=f"issuer metadata at {url} names issuer {metadata.issuer!r}"
        )
    with _metadata_lock:
        _metadata_cache[normalized] = metadata
    return metadata


def clear_metadata_cache() -> None:
    """Forget cached metadata (tests, and long-running hosts after a change)."""
    with _metadata_lock:
        _metadata_cache.clear()


# ---------------------------------------------------------------------------
# Device grant (RFC 8628)
# ---------------------------------------------------------------------------


class DeviceAuthorization(BaseModel):
    """The device authorization response (RFC 8628 §3.2)."""

    model_config = ConfigDict(extra="ignore", frozen=True, hide_input_in_errors=True)

    device_code: SecretStr
    user_code: str
    verification_uri: str
    verification_uri_complete: str | None = None
    expires_in: int = Field(gt=0)
    interval: int = Field(default=5, ge=1)


class IssuedKey(BaseModel):
    """The key a successful device grant returns, with what the client stores."""

    model_config = ConfigDict(frozen=True, hide_input_in_errors=True)

    key: SecretStr
    key_id: str | None
    expires_at: datetime | None
    default_project: str | None


class _TokenResponse(BaseModel):
    """The token response (RFC 6749 §5.1) plus the issuer's non-secret extras."""

    model_config = ConfigDict(extra="ignore", frozen=True, hide_input_in_errors=True)

    access_token: SecretStr
    token_type: str
    expires_in: int | None = None
    key_id: str | None = None
    default_project: str | None = None


def _post_form(
    http: httpx.Client, url: str, data: dict[str, str], *, timeout: float | None = None
) -> httpx.Response:
    """POST a form body to an issuer endpoint (same-origin redirects only)."""
    request = http.build_request(
        "POST",
        url,
        data=data,
        headers={"Accept": "application/json"},
        timeout=timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT,
    )
    return send_same_origin(http, request)


def start_device_authorization(
    *, http: httpx.Client, metadata: IssuerMetadata
) -> DeviceAuthorization:
    """Ask the issuer for a device code and a user code."""
    url = metadata.endpoint("device_authorization_endpoint")
    try:
        response = _post_form(http, url, {"client_id": CLIENT_ID})
    except httpx.HTTPError as error:
        raise DeviceGrantError(
            detail=f"device authorization failed: {error}"
        ) from error
    if response.status_code != 200:
        raise DeviceGrantError(
            detail=f"device authorization failed with HTTP {response.status_code}"
        )
    try:
        return DeviceAuthorization.model_validate(response.json())
    except (ValueError, ValidationError) as error:
        raise DeviceGrantError(
            detail="the issuer returned an unusable device authorization"
        ) from error


def poll_for_key(
    *,
    http: httpx.Client,
    metadata: IssuerMetadata,
    authorization: DeviceAuthorization,
    sleep: Callable[[float], None] | None = None,
    clock: Callable[[], float] | None = None,
) -> IssuedKey:
    """Poll the token endpoint until the person approves, denies, or time runs out.

    Honours ``slow_down`` (+5 s), ``Retry-After`` and a 30 s cap on the wait.
    """
    sleep = sleep or time.sleep
    clock = clock or time.monotonic
    url = metadata.endpoint("token_endpoint")
    form = {
        "grant_type": DEVICE_GRANT_TYPE,
        "device_code": authorization.device_code.get_secret_value(),
        "client_id": CLIENT_ID,
    }
    deadline = clock() + authorization.expires_in
    wait = min(float(authorization.interval), _POLL_CAP_SECONDS)
    while True:
        remaining = deadline - clock()
        if remaining <= 0:
            raise DeviceGrantError(detail="the device code expired before approval")
        sleep(min(wait, remaining))
        if clock() >= deadline:
            raise DeviceGrantError(detail="the device code expired before approval")
        try:
            response = _post_form(http, url, form)
        except httpx.TimeoutException:
            # RFC 8628 §3.5: back off on a timeout as on ``slow_down``.
            wait = min(wait + _SLOW_DOWN_STEP_SECONDS, _POLL_CAP_SECONDS)
            continue
        except httpx.TransportError:
            continue
        except httpx.HTTPError as error:
            raise DeviceGrantError(detail=f"token polling failed: {error}") from error
        if response.status_code == 200:
            return _issued_key(response)
        error_code = _oauth_error(response)
        if error_code == "authorization_pending":
            wait = _next_wait(response, wait)
        elif error_code == "slow_down":
            wait = _next_wait(response, wait + _SLOW_DOWN_STEP_SECONDS)
        elif response.status_code in (429, 503):
            wait = _next_wait(response, wait)
        elif error_code == "access_denied":
            raise DeviceGrantError(detail="the sign-in was denied")
        elif error_code == "expired_token":
            raise DeviceGrantError(detail="the device code expired before approval")
        else:
            raise DeviceGrantError(
                detail=(
                    f"token polling failed with HTTP {response.status_code}"
                    + (f" ({error_code})" if error_code else "")
                )
            )


def _issued_key(response: httpx.Response) -> IssuedKey:
    """Parse a successful token response into the key the client stores."""
    try:
        body = _TokenResponse.model_validate(response.json())
    except (ValueError, ValidationError) as error:
        raise DeviceGrantError(
            detail="the issuer returned an unusable token response"
        ) from error
    if body.token_type.lower() != "bearer":
        raise DeviceGrantError(
            detail=f"the issuer returned token_type {body.token_type!r}, not Bearer"
        )
    key = body.access_token.get_secret_value()
    claims = signed_key_claims(key)
    expires_at = claims.expires_at if claims is not None else None
    if expires_at is None and body.expires_in is not None:
        expires_at = datetime.fromtimestamp(time.time() + body.expires_in, UTC)
    return IssuedKey(
        key=body.access_token,
        key_id=body.key_id or (claims.jti if claims is not None else None),
        expires_at=expires_at,
        default_project=body.default_project,
    )


def _oauth_error(response: httpx.Response) -> str | None:
    """The ``error`` member of an OAuth error body, when there is one."""
    try:
        body = response.json()
    except ValueError:
        return None
    value = body.get("error") if isinstance(body, dict) else None
    return value if isinstance(value, str) else None


def _next_wait(response: httpx.Response, wait: float) -> float:
    """``Retry-After`` (seconds) as a floor on ``wait``, capped at 30 s."""
    raw = response.headers.get("Retry-After")
    if raw is not None and raw.strip().isdigit():
        wait = max(wait, float(raw.strip()))
    return min(max(wait, 1.0), _POLL_CAP_SECONDS)


# ---------------------------------------------------------------------------
# Revocation (RFC 7009)
# ---------------------------------------------------------------------------


def revoke_key(
    *, http: httpx.Client, metadata: IssuerMetadata, key: str, timeout: float = 10.0
) -> bool:
    """Revoke ``key`` at the issuer; ``True`` only when that is confirmed.

    ``2xx`` confirms. ``401`` and ``404`` also count: the issuer no longer
    knows the key, which is the outcome wanted. Anything else — ``5xx``, no
    response — confirms nothing.
    """
    url = metadata.endpoint("revocation_endpoint")
    try:
        response = _post_form(
            http,
            url,
            {"token": key, "token_type_hint": "access_token", "client_id": CLIENT_ID},
            timeout=timeout,
        )
    except httpx.HTTPError:
        return False
    except IssuerError:
        return False
    return response.is_success or response.status_code in (401, 404)
