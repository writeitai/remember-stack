"""The device-grant response is written by a separately deployed control plane.

This file exists because that boundary was broken in production for a week. The
control plane added two fields on 2026-08-25; this client set `extra="forbid"`
and had never had them, so every `remember login` failed with a validation
error. Nobody noticed, because no test ever fed a real control-plane response
through the client model.
"""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta
from datetime import timezone
from uuid import uuid4

from pydantic import ValidationError
import pytest

from rememberstack.surfaces.device_login import DeviceTokenSuccess


def _control_plane_body(**extra: object) -> dict[str, object]:
    """The body the control plane actually sends today."""
    deployment_id = str(uuid4())
    body: dict[str, object] = {
        "access_token": "umc_dp_secret",
        "token_type": "Bearer",
        "token_id": str(uuid4()),
        "org_id": str(uuid4()),
        "deployment_id": deployment_id,
        "label": "laptop",
        "token_prefix": "umc_dp_abcd",
        # Added by the control plane on 2026-08-25; absent from this model until
        # now, which is what broke `remember login`.
        "data_plane_hostname": f"{deployment_id}.dp.remember.dev",
        "data_plane_hostname_live": True,
    }
    body.update(extra)
    return body


def test_todays_control_plane_response_parses() -> None:
    """The regression. This failed for a week in production."""
    parsed = DeviceTokenSuccess.model_validate(_control_plane_body())

    assert parsed.access_token.get_secret_value() == "umc_dp_secret"
    assert parsed.data_plane_hostname is not None
    assert parsed.data_plane_hostname_live is True


def test_an_unprovisioned_deployment_may_report_null_status() -> None:
    """A nullable hostname may naturally arrive with a nullable live flag."""
    parsed = DeviceTokenSuccess.model_validate(
        _control_plane_body(data_plane_hostname=None, data_plane_hostname_live=None)
    )

    assert parsed.data_plane_hostname is None
    assert parsed.data_plane_hostname_live is False


def test_an_expiry_the_server_starts_sending_parses() -> None:
    """D60 adds `expires_at`; a client must absorb it rather than break."""
    expires_at = datetime.now(timezone.utc) + timedelta(days=365)
    parsed = DeviceTokenSuccess.model_validate(
        _control_plane_body(expires_at=expires_at.isoformat())
    )

    assert parsed.expires_at is not None
    assert parsed.expires_at.year == expires_at.year


def test_a_field_this_client_has_never_heard_of_parses() -> None:
    """The actual lesson: additive server change must not be a client outage.

    This model describes a response from a service deployed separately and
    released on its own schedule. Forbidding unknown fields inverts the
    compatibility that boundary needs.
    """
    parsed = DeviceTokenSuccess.model_validate(
        _control_plane_body(some_future_field={"nested": [1, 2, 3]})
    )

    assert parsed.token_type == "Bearer"


def test_a_missing_expiry_is_still_valid() -> None:
    """Tokens minted before D60 never expire; demanding it would refuse them."""
    parsed = DeviceTokenSuccess.model_validate(_control_plane_body())
    assert parsed.expires_at is None


@pytest.mark.parametrize("missing", ["access_token", "token_id", "deployment_id"])
def test_a_field_this_client_needs_is_still_required(missing: str) -> None:
    """Tolerating extras must not become tolerating absences."""
    body = _control_plane_body()
    del body[missing]

    with pytest.raises(ValidationError):
        DeviceTokenSuccess.model_validate(body)
