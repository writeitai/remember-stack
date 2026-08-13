"""Unit contracts for rememberstack.cost_export.v1 (no Postgres)."""

from datetime import datetime
from datetime import timezone
from decimal import Decimal
from typing import cast
from uuid import UUID

from fastapi.testclient import TestClient
from pydantic import SecretStr
from pydantic import ValidationError
import pytest

from rememberstack.model import ForgetInProgressError
from rememberstack.spine.cost_export import COST_EXPORT_PAGE_FIELDS
from rememberstack.spine.cost_export import COST_EXPORT_RECEIPT_FIELDS
from rememberstack.spine.cost_export import CostExportConfigError
from rememberstack.spine.cost_export import CostExportCursorError
from rememberstack.spine.cost_export import CostExportPage
from rememberstack.spine.cost_export import CostExportReceipt
from rememberstack.spine.cost_export import CostExportSettings
from rememberstack.spine.cost_export import decode_cost_export_cursor
from rememberstack.spine.cost_export import encode_cost_export_cursor
from rememberstack.spine.cost_export import parse_cost_export_bind
from rememberstack.surfaces.cost_export_api import _TokenBucket
from rememberstack.surfaces.cost_export_api import build_cost_export_app
from rememberstack.surfaces.http_api import build_api
from rememberstack.surfaces.query_engine import QueryEngine

_DEPLOYMENT = UUID("11111111-1111-1111-1111-111111111111")
_COST = UUID("22222222-2222-2222-2222-222222222222")
_WORK = UUID("33333333-3333-3333-3333-333333333333")
_WHEN = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)
_EXPORT_TOKEN = "e" * 32
_CUSTOMER_TOKEN = "customer-perimeter-token"


class _Ready:
    def ensure_ready(self, *, deployment_id: UUID) -> tuple[UUID, ...]:
        return ()

    def assert_available(self, *, deployment_id: UUID) -> None:
        return None


class _ForgetAdmission:
    def assert_available(self, *, deployment_id: UUID) -> None:
        raise ForgetInProgressError(str(deployment_id))


class _FakeReader:
    def __init__(self, page: CostExportPage | None = None) -> None:
        self.page = page or _sample_page()
        self.calls: list[dict[str, object]] = []

    def read_page(
        self, *, deployment_id: UUID, cursor: str | None, limit: int
    ) -> CostExportPage:
        self.calls.append(
            {"deployment_id": deployment_id, "cursor": cursor, "limit": limit}
        )
        if cursor == "bad":
            raise CostExportCursorError("malformed")
        return self.page


def _sample_receipt(**overrides: object) -> CostExportReceipt:
    payload: dict[str, object] = {
        "cost_id": _COST,
        "deployment_id": _DEPLOYMENT,
        "source": "surface",
        "work_id": _WORK,
        "stage": None,
        "lane": None,
        "attempt": None,
        "surface": "search",
        "call_key": "search_claims:1",
        "outcome": "ok",
        "model_name": "toy",
        "tokens_in": 3,
        "tokens_out": 0,
        "cost_usd": Decimal("0.000000001"),
        "latency_ms": 4,
        "occurred_at": _WHEN,
    }
    payload.update(overrides)
    return CostExportReceipt.model_validate(payload)


def _sample_page(**overrides: object) -> CostExportPage:
    payload: dict[str, object] = {
        "contract": "rememberstack.cost_export.v1",
        "deployment_id": _DEPLOYMENT,
        "server_time": _WHEN,
        "horizon": _WHEN,
        "cursor": "cursor-in",
        "next_cursor": "cursor-out",
        "persist_failures": 0,
        "scope_missing": 0,
        "receipts": (_sample_receipt(),),
    }
    payload.update(overrides)
    return CostExportPage.model_validate(payload)


def test_v1_field_sets_are_frozen() -> None:
    """Declared model fields must equal the frozen allowlists."""
    assert tuple(CostExportPage.model_fields) == COST_EXPORT_PAGE_FIELDS
    assert tuple(CostExportReceipt.model_fields) == COST_EXPORT_RECEIPT_FIELDS


def test_golden_v1_page_json_round_trip() -> None:
    """Checked-in golden page pins serialization including worker nulls."""
    import json
    from pathlib import Path

    golden_path = Path(__file__).parent / "golden" / "cost_export_v1_page.json"
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    page = CostExportPage.model_validate(golden)
    dumped = json.loads(page.model_dump_json())
    assert dumped == golden
    assert dumped["receipts"][0]["cost_usd"] == "0.000000001"
    assert dumped["receipts"][1]["cost_usd"] is None


def test_page_and_receipt_forbid_extra_keys() -> None:
    """v1 never grows undeclared fields."""
    with pytest.raises(ValidationError):
        CostExportReceipt.model_validate(
            {**_sample_receipt().model_dump(), "prompt": "secret"}
        )
    with pytest.raises(ValidationError):
        CostExportPage.model_validate({**_sample_page().model_dump(), "extra": 1})


def test_cost_usd_serializes_as_decimal_string_not_float() -> None:
    """Tiny surface amounts must not become IEEE floats."""
    dumped = _sample_receipt(cost_usd=Decimal("1E-9")).model_dump(mode="json")
    assert dumped["cost_usd"] == "0.000000001"
    assert not isinstance(dumped["cost_usd"], float)
    null_cost = _sample_receipt(cost_usd=None, source="worker", surface=None)
    assert null_cost.model_dump(mode="json")["cost_usd"] is None


def test_cursor_round_trips_and_rejects_garbage() -> None:
    """Opaque cursors encode the key plus frozen horizon."""
    encoded = encode_cost_export_cursor(
        last_occurred_at=_WHEN,
        last_source="surface",
        last_cost_id=_COST,
        horizon_at_issue=_WHEN,
    )
    decoded = decode_cost_export_cursor(cursor=encoded)
    assert decoded is not None
    assert decoded.last_source == "surface"
    assert decoded.last_cost_id == _COST
    assert decode_cost_export_cursor(cursor=None) is None
    with pytest.raises(CostExportCursorError):
        decode_cost_export_cursor(cursor="%%%not-a-cursor")


def test_build_api_does_not_register_export() -> None:
    """The customer app physically cannot serve the export path."""
    app = build_api(
        engine=cast(QueryEngine, object()),
        deployment_id=_DEPLOYMENT,
        admission=_Ready(),  # type: ignore[arg-type]
        readiness=_Ready(),  # type: ignore[arg-type]
    )
    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/ops/cost-export/v1" not in paths
    assert TestClient(app).get("/ops/cost-export/v1").status_code == 404


def test_export_accepts_export_token_and_rejects_customer_token() -> None:
    """Export auth is a dedicated bearer, not the query perimeter."""
    app = build_cost_export_app(
        reader=_FakeReader(),
        deployment_id=_DEPLOYMENT,
        token=_EXPORT_TOKEN,
        bucket=_TokenBucket(rate_per_s=10.0, capacity=10.0),
    )
    client = TestClient(app)
    denied = client.get("/ops/cost-export/v1")
    assert denied.status_code == 401
    assert _EXPORT_TOKEN not in denied.text
    wrong = client.get(
        "/ops/cost-export/v1", headers={"Authorization": f"Bearer {_CUSTOMER_TOKEN}"}
    )
    assert wrong.status_code == 401
    ok = client.get(
        "/ops/cost-export/v1", headers={"Authorization": f"Bearer {_EXPORT_TOKEN}"}
    )
    assert ok.status_code == 200
    body = ok.json()
    assert body["contract"] == "rememberstack.cost_export.v1"
    assert set(body) == set(COST_EXPORT_PAGE_FIELDS)
    assert set(body["receipts"][0]) == set(COST_EXPORT_RECEIPT_FIELDS)


def test_malformed_cursor_is_422() -> None:
    """A corrupt cursor must not return receipts."""
    app = build_cost_export_app(
        reader=_FakeReader(),
        deployment_id=_DEPLOYMENT,
        token=_EXPORT_TOKEN,
        bucket=_TokenBucket(rate_per_s=10.0, capacity=10.0),
    )
    response = TestClient(app).get(
        "/ops/cost-export/v1",
        params={"cursor": "bad"},
        headers={"Authorization": f"Bearer {_EXPORT_TOKEN}"},
    )
    assert response.status_code == 422
    assert "receipts" not in response.json() or "detail" in response.json()


def test_forget_in_progress_on_customer_app_does_not_stop_export() -> None:
    """D74 admission is a customer-app dependency; export has none."""
    customer = build_api(
        engine=cast(QueryEngine, object()),
        deployment_id=_DEPLOYMENT,
        admission=_ForgetAdmission(),  # type: ignore[arg-type]
        readiness=_Ready(),  # type: ignore[arg-type]
    )
    export = build_cost_export_app(
        reader=_FakeReader(),
        deployment_id=_DEPLOYMENT,
        token=_EXPORT_TOKEN,
        bucket=_TokenBucket(rate_per_s=10.0, capacity=10.0),
    )
    assert TestClient(customer).get("/resolve", params={"name": "x"}).status_code == 503
    assert (
        TestClient(export)
        .get(
            "/ops/cost-export/v1", headers={"Authorization": f"Bearer {_EXPORT_TOKEN}"}
        )
        .status_code
        == 200
    )


def test_rate_limit_is_one_request_per_second() -> None:
    """The in-process bucket starts at 1 req/s."""
    clock = {"now": 0.0}

    def _now() -> float:
        return clock["now"]

    app = build_cost_export_app(
        reader=_FakeReader(),
        deployment_id=_DEPLOYMENT,
        token=_EXPORT_TOKEN,
        bucket=_TokenBucket(rate_per_s=1.0, capacity=1.0, clock=_now),
    )
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {_EXPORT_TOKEN}"}
    assert client.get("/ops/cost-export/v1", headers=headers).status_code == 200
    assert client.get("/ops/cost-export/v1", headers=headers).status_code == 429
    clock["now"] = 1.1
    assert client.get("/ops/cost-export/v1", headers=headers).status_code == 200


def test_unauthenticated_request_does_not_consume_rate_limit() -> None:
    """Wrong or missing tokens must not starve a legitimate exporter."""
    clock = {"now": 0.0}

    def _now() -> float:
        return clock["now"]

    app = build_cost_export_app(
        reader=_FakeReader(),
        deployment_id=_DEPLOYMENT,
        token=_EXPORT_TOKEN,
        bucket=_TokenBucket(rate_per_s=1.0, capacity=1.0, clock=_now),
    )
    client = TestClient(app)
    assert client.get("/ops/cost-export/v1").status_code == 401
    assert (
        client.get(
            "/ops/cost-export/v1", headers={"Authorization": f"Bearer {_CUSTOMER_TOKEN}"}
        ).status_code
        == 401
    )
    assert (
        client.get(
            "/ops/cost-export/v1", headers={"Authorization": f"Bearer {_EXPORT_TOKEN}"}
        ).status_code
        == 200
    )


def test_bind_parser_and_token_floor() -> None:
    """Bind grammar and fail-closed token length."""
    assert parse_cost_export_bind(bind="127.0.0.1:8001") == ("127.0.0.1", 8001, None)
    assert parse_cost_export_bind(bind="[::1]:8001") == ("::1", 8001, None)
    assert parse_cost_export_bind(bind="unix:/tmp/export.sock") == (
        None,
        None,
        "/tmp/export.sock",
    )
    with pytest.raises(CostExportConfigError):
        parse_cost_export_bind(bind="not-a-bind")
    settings = CostExportSettings(
        cost_export_bind="127.0.0.1:8001", cost_export_token=SecretStr("short")
    )
    with pytest.raises(CostExportConfigError):
        settings.require_http_credentials()
    ok = CostExportSettings(
        cost_export_bind="127.0.0.1:8001", cost_export_token=SecretStr("t" * 32)
    )
    bind, token = ok.require_http_credentials()
    assert bind == "127.0.0.1:8001"
    assert token == "t" * 32


def test_attach_listener_fails_closed_without_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bind without a 32-byte token refuses to compose the customer app."""
    from fastapi import FastAPI

    from rememberstack.surfaces.cost_export_api import attach_cost_export_listener

    monkeypatch.setenv("REMEMBERSTACK_COST_EXPORT_BIND", "127.0.0.1:18001")
    monkeypatch.delenv("REMEMBERSTACK_COST_EXPORT_TOKEN", raising=False)
    with pytest.raises(CostExportConfigError):
        attach_cost_export_listener(
            app=FastAPI(),
            engine=cast(object, None),  # type: ignore[arg-type]
            deployment_id=_DEPLOYMENT,
        )


def test_unknown_export_version_path_is_404() -> None:
    """v1 is immutable; a later contract is a new path."""
    app = build_cost_export_app(
        reader=_FakeReader(), deployment_id=_DEPLOYMENT, token=_EXPORT_TOKEN
    )
    response = TestClient(app).get(
        "/ops/cost-export/v2", headers={"Authorization": f"Bearer {_EXPORT_TOKEN}"}
    )
    assert response.status_code == 404
