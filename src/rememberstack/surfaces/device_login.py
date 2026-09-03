"""Device-grant HTTP client for ``remember login`` / ``logout`` (D92).

The token host contract is JSON, not RFC 8628 form-encoding. Only the
``grant_type`` URN is borrowed from the RFC.
"""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Mapping
from datetime import datetime
import time
from typing import Literal
from uuid import UUID

import httpx
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import SecretStr
from pydantic import ValidationError

from rememberstack.surfaces.credentials import CredentialFile
from rememberstack.surfaces.credentials import DeferredInterrupts

DEVICE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
_POLL_FLOOR_SECONDS = 1.0
_POLL_CAP_SECONDS = 30.0
_SLOW_DOWN_STEP_SECONDS = 5.0
_MAX_REDIRECTS = 5


class DeviceGrantError(RuntimeError):
    """The token host refused or failed a device-grant request."""

    def __init__(self, message: str, *, exit_code: int = 1) -> None:
        """Keep the process exit code next to the human-readable reason."""
        super().__init__(message)
        self.exit_code = exit_code


class DeviceAuthorizeResponse(BaseModel):
    """Authorize success body from the token host."""

    model_config = ConfigDict(extra="ignore", frozen=True, hide_input_in_errors=True)

    device_code: SecretStr
    user_code: str
    verification_uri: str
    verification_uri_complete: str
    expires_in: int = Field(gt=0)
    interval: int = Field(ge=1)


class DeviceTokenSuccess(BaseModel):
    """Successful token poll body.

    ``extra="ignore"``, not ``forbid``, and the difference is a bug we shipped.
    This model describes a response written by a *separately deployed* control
    plane, and that control plane added ``data_plane_hostname`` and
    ``data_plane_hostname_live`` on 2026-08-25. Every ``remember login`` against
    it has failed since with a validation error, because forbidding unknown
    fields turns any additive server change into a client outage.

    Forbidding extras is right for something we own both ends of. For a response
    crossing a deployment boundary it inverts the compatibility we want: the
    server may add, and the client must carry on. Fields this client actually
    needs are declared and validated; anything else is the server's business.

    The advertised data-plane hostname is declared explicitly because login uses
    it to configure managed deployments without a separate ``--api-url``. Other
    additive fields remain safe to ignore until the client needs them.
    """

    model_config = ConfigDict(extra="ignore", frozen=True, hide_input_in_errors=True)

    access_token: SecretStr
    token_type: Literal["Bearer"]
    token_id: UUID
    org_id: UUID
    deployment_id: UUID
    label: str
    token_prefix: str
    #: Where this deployment answers, and whether that name resolves yet (D33).
    #: Advertised by the control plane; the CLI stores it so a caller does not
    #: have to be told the host separately.
    data_plane_hostname: str | None = None
    data_plane_hostname_live: bool = False
    #: When the credential stops working (D60). Absent for the unexpiring
    #: tokens minted before that decision, which is why it is optional rather
    #: than required — a client that demanded it would refuse today's tokens.
    expires_at: datetime | None = None


class DeviceTokenErrorBody(BaseModel):
    """RFC 8628 poll error body."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    error: str
    error_description: str = ""


def normalize_token_host(*, token_host: str) -> str:
    """Strip trailing slashes; do not rewrite the path layout."""
    return token_host.strip().rstrip("/")


def authorize_device(*, client: httpx.Client) -> DeviceAuthorizeResponse:
    """POST ``/v1/device/authorize`` and return the grant."""
    response = request_same_origin(
        client=client,
        method="POST",
        url="/v1/device/authorize",
        json={"client_name": "remember-cli"},
    )
    if response.status_code != 200:
        raise DeviceGrantError(
            f"authorize failed with HTTP {response.status_code}", exit_code=1
        )
    try:
        return DeviceAuthorizeResponse.model_validate(response.json())
    except (ValidationError, ValueError) as error:
        raise DeviceGrantError(
            "token host returned an unusable authorize response", exit_code=1
        ) from error


#: The token poll's own per-operation timeout.
#:
#: Short and explicit, because signals are deferred across this call. It is
#: **not** an absolute deadline: HTTP timeouts here are per-operation, so a
#: response that trickles a byte at a time resets it, and no client-side
#: setting bounds that without a watchdog. What it does bound is the ordinary
#: case — a slow or unresponsive token host — and, with the redirect limit
#: below, the pathological-redirect case too.
#:
#: A device-token response is a few hundred bytes, so a generous client-wide
#: timeout would only mean a longer silence for no benefit.
_POLL_TIMEOUT_SECONDS = 10.0

#: Redirects the token poll will follow. One is generosity; five was the shared
#: default, and five sends at the timeout above is five times the silence.
_POLL_MAX_REDIRECTS = 1


def _report_orphan(
    *,
    response: httpx.Response,
    on_orphan: "Callable[[Mapping[str, object]], None] | None",
) -> None:
    """Hand a caller the raw body of a credential that never became usable.

    Best effort by construction: the body may be exactly what failed to parse.
    A body that yields nothing usable simply reports nothing, because there is
    nothing to act on.
    """
    if on_orphan is None:
        return
    try:
        payload = response.json()
    except ValueError:
        return
    if isinstance(payload, dict):
        on_orphan(payload)


def poll_device_token(
    *,
    client: httpx.Client,
    device_code: str,
    interval: int,
    expires_in: int,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    on_minted: Callable[[DeviceTokenSuccess], None] | None = None,
    on_orphan: Callable[[Mapping[str, object]], None] | None = None,
) -> DeviceTokenSuccess:
    """Poll ``/v1/device/token`` until 200, a terminal error, or TTL expiry.

    ``on_minted`` is called with the parsed token **before this function
    returns**, and is where a caller records that a credential now exists.

    That placement is the point. A caller cannot guard the moment between this
    returning and its own next statement — CPython leaves that boundary outside
    any exception region, so a Ctrl-C landing there produced a live credential
    with nothing on the machine naming it, and no possible cleanup. Recording
    it here removes the window instead of narrowing it: by the time the value
    is visible to a caller, it has already been written down.

    ``on_orphan`` covers the rest of that window. From the moment a ``200``
    arrives a credential exists at the token host, and everything between there
    and ``on_minted`` succeeding — parsing, validating, recording — can fail or
    be interrupted. It is called with the raw response body so a caller can
    still withdraw a credential that never became a usable one.
    """
    deadline = clock() + expires_in
    wait = _clamp_poll_wait(seconds=float(interval))
    while clock() < deadline:
        sleep(wait)
        if clock() >= deadline:
            break
        # Signals are held for the request and everything that follows a
        # `200`. From the moment the token host answers, a credential exists —
        # and no arrangement of `try` blocks makes the steps that follow safe,
        # because a signal lands *between* bytecodes and the gaps between
        # statements belong to no exception region. Deferring is what closes
        # them.
        #
        # The hold starts *before* the request, because a `200` that arrives
        # while a signal is in flight must still be recorded — but it is
        # released the instant the answer turns out not to be a credential, so
        # a user who pressed Ctrl-C while waiting is not made to sit through
        # the rest of the poll.
        with DeferredInterrupts() as deferred:
            response = request_same_origin(
                client=client,
                method="POST",
                url="/v1/device/token",
                json={"grant_type": DEVICE_GRANT_TYPE, "device_code": device_code},
                timeout=_POLL_TIMEOUT_SECONDS,
                max_redirects=_POLL_MAX_REDIRECTS,
            )
            if response.status_code != 200:
                # Nothing was issued, so nothing has to be protected.
                deferred.deliver_if_pending()
            if response.status_code == 200:
                try:
                    payload = response.json()
                    minted = DeviceTokenSuccess.model_validate(payload)
                    if on_minted is not None:
                        on_minted(minted)
                except (ValidationError, ValueError) as error:
                    _report_orphan(response=response, on_orphan=on_orphan)
                    raise DeviceGrantError(
                        "token host returned an unusable token response", exit_code=1
                    ) from error
                except BaseException:
                    _report_orphan(response=response, on_orphan=on_orphan)
                    raise
                return minted
        if response.status_code == 400:
            try:
                body = DeviceTokenErrorBody.model_validate(response.json())
            except (ValidationError, ValueError) as error:
                raise DeviceGrantError(
                    "token host returned an unusable poll error", exit_code=1
                ) from error
            if body.error == "authorization_pending":
                wait = _retry_after_seconds(
                    response=response, fallback=wait, bump=False
                )
                continue
            if body.error == "slow_down":
                wait = _retry_after_seconds(response=response, fallback=wait, bump=True)
                continue
            if body.error in {"expired_token", "access_denied", "invalid_grant"}:
                raise DeviceGrantError(
                    body.error_description or body.error, exit_code=1
                )
            if body.error == "temporarily_unavailable":
                wait = _retry_after_seconds(response=response, fallback=wait, bump=True)
                continue
            raise DeviceGrantError(body.error_description or body.error, exit_code=1)
        raise DeviceGrantError(
            f"token poll failed with HTTP {response.status_code}", exit_code=1
        )
    raise DeviceGrantError("device grant expired before authorization", exit_code=1)


def revoke_self(*, client: httpx.Client, access_token: str) -> int:
    """DELETE ``/v1/api-tokens/self``. Returns the HTTP status, or 0 on network."""
    try:
        response = request_same_origin(
            client=client,
            method="DELETE",
            url="/v1/api-tokens/self",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    except (httpx.HTTPError, DeviceGrantError):
        return 0
    return response.status_code


def credential_from_token(
    *, token: DeviceTokenSuccess, api_url: str | None, token_host: str
) -> CredentialFile:
    """Build the v1 credential document, deriving its managed API URL."""
    if api_url is None:
        hostname = token.data_plane_hostname
        if hostname is None or not hostname.strip():
            raise DeviceGrantError(
                "token host did not advertise a data-plane hostname; pass --api-url"
            )
        if not token.data_plane_hostname_live:
            raise DeviceGrantError(
                f"deployment hostname: {hostname}\n"
                "your deployment is not live yet; run `remember login` again when it is"
            )
        api_url = f"https://{hostname}"
    return CredentialFile(
        version=1,
        api_url=api_url,
        token_host=token_host,
        access_token=token.access_token,
        token_type=token.token_type,
        token_id=token.token_id,
        org_id=token.org_id,
        deployment_id=token.deployment_id,
        label=token.label,
        token_prefix=token.token_prefix,
        expires_at=token.expires_at,
    )


def request_same_origin(
    *,
    client: httpx.Client,
    method: str,
    url: str,
    max_redirects: int | None = None,
    **kwargs: object,
) -> httpx.Response:
    """Send one request; follow only host-preserving redirects.

    ``max_redirects`` narrows the budget for a caller that pays for each hop.
    The token poll does, because signals are deferred across the whole call:
    every redirect it follows is another timeout a user's Ctrl-C waits behind.

    It counts **redirects, not sends** — one redirect means two requests — so a
    budget of 1 follows a redirect rather than refusing it. The loop below
    bounds sends, and the two differ by exactly the original request; conflating
    them made a budget of 1 reject the first redirect it saw.
    """
    budget = max_redirects if max_redirects is not None else _MAX_REDIRECTS - 1
    current = client.build_request(method, url, **kwargs)  # type: ignore[arg-type]
    for _ in range(budget + 1):
        response = client.send(current)
        if response.is_redirect:
            location = response.headers.get("location")
            response.close()
            if not location:
                raise DeviceGrantError("redirect is missing Location", exit_code=1)
            nxt = current.url.join(location)
            if not _same_origin(left=current.url, right=nxt):
                raise DeviceGrantError("refusing a cross-host redirect", exit_code=1)
            current = client.build_request(method, nxt, **kwargs)  # type: ignore[arg-type]
            continue
        return response
    raise DeviceGrantError("too many same-origin redirects", exit_code=1)


def _same_origin(*, left: httpx.URL, right: httpx.URL) -> bool:
    """True when scheme, host, and port match."""
    return (
        left.scheme == right.scheme
        and left.host == right.host
        and left.port == right.port
    )


def _clamp_poll_wait(*, seconds: float) -> float:
    """Keep poll sleeps inside the 1s–30s band."""
    return min(max(seconds, _POLL_FLOOR_SECONDS), _POLL_CAP_SECONDS)


def _retry_after_seconds(
    *, response: httpx.Response, fallback: float, bump: bool
) -> float:
    """Never poll faster than the current interval; honor Retry-After as a floor."""
    wait = fallback + _SLOW_DOWN_STEP_SECONDS if bump else fallback
    raw = response.headers.get("Retry-After")
    if raw is not None:
        try:
            header = float(int(raw))
        except ValueError:
            header = None
        else:
            if header >= 0:
                wait = max(wait, header)
    return _clamp_poll_wait(seconds=wait)
