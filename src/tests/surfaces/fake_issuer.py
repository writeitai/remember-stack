"""A fake key issuer (RFC 8414 + 8628 + 7009 + project resolution) and engines.

One ``httpx.MockTransport`` serves the issuer at :data:`ISSUER` and any number
of engine deployments, recording every request, so client tests exercise the
real wire contract of D136 §8 without a network.
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field
import json
import time
from urllib.parse import parse_qs

import httpx

ISSUER = "https://issuer.test"
DEPLOYMENT_A = "https://dp-a.test"
DEPLOYMENT_B = "https://dp-b.test"


def _b64(data: dict[str, object]) -> str:
    raw = json.dumps(data, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def make_key(
    *,
    jti: str = "key-1",
    projects: list[str] | str | None = None,
    permissions: tuple[str, ...] = ("memory:read", "memory:write"),
    iss: str = ISSUER,
    exp: int | None = None,
) -> str:
    """An ``rmb_``-prefixed compact JWS with the given claims (unsigned: the
    client never verifies; the engine does)."""
    claims: dict[str, object] = {
        "iss": iss,
        "aud": "org:o1",
        "sub": "u1",
        "org": "o1",
        "projects": ["p-docs", "p-notes"] if projects is None else projects,
        "permissions": list(permissions),
        "kind": "key",
        "iat": int(time.time()),
        "nbf": int(time.time()),
        "exp": exp if exp is not None else int(time.time()) + 90 * 86400,
        "jti": jti,
    }
    return f"rmb_{_b64({'alg': 'EdDSA', 'kid': 'k1'})}.{_b64(claims)}.c2ln"


@dataclass
class FakeIssuer:
    """The issuer's behaviour; tests mutate the fields to script scenarios."""

    #: project id or name → (project id, name, api_url); ``None`` key = default.
    projects: dict[str | None, tuple[str, str, str]] = field(
        default_factory=lambda: {
            None: ("p-docs", "docs", DEPLOYMENT_A),
            "p-docs": ("p-docs", "docs", DEPLOYMENT_A),
            "docs": ("p-docs", "docs", DEPLOYMENT_A),
            "p-notes": ("p-notes", "notes", DEPLOYMENT_B),
            "notes": ("p-notes", "notes", DEPLOYMENT_B),
            "p-other": ("p-other", "other", DEPLOYMENT_B),
        }
    )
    metadata_status: int = 200
    account_endpoint: bool = True
    #: Token-endpoint answers before success, e.g. ``["authorization_pending"]``.
    poll_script: list[str] = field(default_factory=list)
    #: The key the token endpoint issues on success.
    issued_key: str = field(default_factory=lambda: make_key(jti="key-new"))
    default_project: str | None = None
    revoke_status: int = 200
    #: Called at token issuance and at each revocation (to assert disk order).
    on_mint: Callable[[], None] | None = None
    on_revoke: Callable[[str], None] | None = None
    revoked: list[str] = field(default_factory=list)
    requests: list[httpx.Request] = field(default_factory=list)
    #: Engine behaviour per deployment base URL: a status, or an exception.
    engines: dict[str, list[int | Exception]] = field(default_factory=dict)

    def metadata(self) -> dict[str, object]:
        body: dict[str, object] = {
            "issuer": ISSUER,
            "device_authorization_endpoint": f"{ISSUER}/oauth/device",
            "token_endpoint": f"{ISSUER}/oauth/token",
            "revocation_endpoint": f"{ISSUER}/oauth/revoke",
            "remember_project_endpoint": f"{ISSUER}/api/v1/keys/self/project",
            "remember_mcp_endpoint": f"{ISSUER}/mcp",
        }
        if self.account_endpoint:
            body["remember_account_endpoint"] = f"{ISSUER}/api"
        return body

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def http(self) -> httpx.Client:
        return httpx.Client(transport=self.transport(), follow_redirects=False)

    def calls(self, path: str) -> list[httpx.Request]:
        return [request for request in self.requests if request.url.path == path]

    def engine_requests(self) -> list[httpx.Request]:
        return [
            request
            for request in self.requests
            if f"{request.url.scheme}://{request.url.host}" != ISSUER
        ]

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        origin = f"{request.url.scheme}://{request.url.host}"
        if request.url.port:
            origin += f":{request.url.port}"
        if origin != ISSUER:
            return self._engine(origin, request)
        path = request.url.path
        if path == "/.well-known/oauth-authorization-server":
            if self.metadata_status != 200:
                return httpx.Response(self.metadata_status)
            return httpx.Response(200, json=self.metadata())
        if path == "/oauth/device":
            form = parse_qs(request.content.decode())
            assert form["client_id"] == ["remember-cli"]
            return httpx.Response(
                200,
                json={
                    "device_code": "device-secret",
                    "user_code": "ABCD-EFGH",
                    "verification_uri": f"{ISSUER}/device",
                    "verification_uri_complete": f"{ISSUER}/device?code=ABCD-EFGH",
                    "expires_in": 600,
                    "interval": 5,
                },
            )
        if path == "/oauth/token":
            form = parse_qs(request.content.decode())
            assert form["grant_type"] == [
                "urn:ietf:params:oauth:grant-type:device_code"
            ]
            assert form["device_code"] == ["device-secret"]
            if self.poll_script:
                error = self.poll_script.pop(0)
                headers = {"Retry-After": "12"} if error == "slow_down+retry" else {}
                return httpx.Response(
                    400, json={"error": error.split("+")[0]}, headers=headers
                )
            if self.on_mint is not None:
                self.on_mint()
            claims = json.loads(
                base64.urlsafe_b64decode(self.issued_key.split(".")[1] + "==")
            )
            return httpx.Response(
                200,
                json={
                    "access_token": self.issued_key,
                    "token_type": "Bearer",
                    "expires_in": 7776000,
                    "key_id": claims["jti"],
                    "org_id": "o1",
                    "default_project": self.default_project,
                },
            )
        if path == "/oauth/revoke":
            form = parse_qs(request.content.decode())
            token = form["token"][0]
            if self.on_revoke is not None:
                self.on_revoke(token)
            if 200 <= self.revoke_status < 300:
                self.revoked.append(token)
            return httpx.Response(self.revoke_status)
        if path == "/api/v1/keys/self/project":
            project = request.url.params.get("project")
            found = self.projects.get(project)
            if found is None:
                return httpx.Response(404, json={"detail": "unknown project"})
            project_id, name, api_url = found
            return httpx.Response(
                200, json={"project": project_id, "name": name, "api_url": api_url}
            )
        if path == "/api/v1/keys/self":
            return httpx.Response(200, json={"user": {"id": "u1"}, "role": "OWNER"})
        return httpx.Response(404, text="not found")

    def _engine(self, origin: str, request: httpx.Request) -> httpx.Response:
        script = self.engines.get(origin)
        if script:
            outcome = script.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            if outcome == 404:
                return httpx.Response(404, text="<html>no such host</html>")
            if outcome == 429:
                return httpx.Response(
                    429,
                    json={"detail": {"code": "rate_limited", "message": "slow down"}},
                    headers={"Retry-After": "7"},
                )
            if outcome != 200:
                return httpx.Response(outcome, text="moved")
        if request.url.path.endswith("/operations"):
            return httpx.Response(200, json=[])
        return httpx.Response(404, json={"detail": "not found"})
