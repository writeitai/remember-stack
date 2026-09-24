"""``remember login`` / ``logout`` / ``switch`` / ``whoami`` (D136 §8.1, §8.4).

Runs against a fake RFC 8414 + 8628 + 7009 issuer. The crash-safety tests put
the disk in each state the §8.4 crash table names and check what the next CLI
start does; the ordering tests check, from inside the issuer's own handlers,
that the journal and the credential file are in the right state at the moment
a key is minted and at the moment the old one is revoked.
"""

from __future__ import annotations

import json
from pathlib import Path
import stat

import httpx
from pydantic import SecretStr
import pytest

from remember.cli import main
from remember.credentials import config_dir
from remember.credentials import load_credentials
from remember.credentials import load_pending_revocations
from remember.credentials import PendingRevocation
from remember.credentials import StoredCredentials
from remember.credentials import write_credentials
from remember.issuer import fetch_issuer_metadata
from remember.issuer import IssuerError
from tests.surfaces.fake_issuer import FakeIssuer
from tests.surfaces.fake_issuer import ISSUER
from tests.surfaces.fake_issuer import make_key

OLD_KEY = make_key(jti="key-old")


@pytest.fixture()
def issuer(monkeypatch: pytest.MonkeyPatch) -> FakeIssuer:
    """Route every ``httpx.Client`` to the fake issuer; make polling instant."""
    fake = FakeIssuer()
    real_client = httpx.Client

    def patched(*args: object, **kwargs: object) -> httpx.Client:
        kwargs["transport"] = fake.transport()
        return real_client(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "Client", patched)
    sleeps: list[float] = []
    monkeypatch.setattr("remember.issuer.time.sleep", sleeps.append)
    fake.sleeps = sleeps  # type: ignore[attr-defined]
    return fake


def _config() -> Path:
    return config_dir()


def _store_old_key() -> None:
    write_credentials(
        credentials=StoredCredentials(
            version=2, issuer=ISSUER, key=SecretStr(OLD_KEY), key_id="key-old"
        )
    )


def _stored_key() -> str | None:
    stored = load_credentials()
    return (
        None if stored is None or stored.key is None else stored.key.get_secret_value()
    )


def _journal_keys() -> list[str]:
    return [
        entry.key.get_secret_value() for entry in load_pending_revocations().entries
    ]


def _journal(key: str) -> None:
    from remember.credentials import journal_key

    journal_key(
        entry=PendingRevocation(issuer=ISSUER, key=SecretStr(key), key_id="key-old")
    )


# --- first login -----------------------------------------------------------------


def test_login_stores_one_key_owner_only(
    issuer: FakeIssuer, capsys: pytest.CaptureFixture[str]
) -> None:
    issuer.poll_script = ["authorization_pending", "slow_down"]
    issuer.default_project = "p-docs"
    assert main(["login", "--issuer", ISSUER]) == 0
    out = capsys.readouterr().out
    assert "ABCD-EFGH" in out and f"{ISSUER}/device" in out
    assert issuer.issued_key not in out  # the key is never printed

    path = _config() / "credentials.json"
    stored = json.loads(path.read_text())
    assert stored["version"] == 2
    assert stored["issuer"] == ISSUER
    assert stored["key"] == issuer.issued_key
    assert stored["key_id"] == "key-new"
    assert stored["default_project"] == "p-docs"
    assert stored["api_url"] is None
    assert stored["expires_at"]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(_config().stat().st_mode) == 0o700
    # interval 5, pending keeps 5, slow_down adds 5.
    assert issuer.sleeps == [5.0, 5.0, 10.0]  # type: ignore[attr-defined]


def test_login_honours_retry_after_with_a_cap(issuer: FakeIssuer) -> None:
    issuer.poll_script = ["slow_down+retry"] + ["slow_down"] * 5
    assert main(["login", "--issuer", ISSUER]) == 0
    sleeps = issuer.sleeps  # type: ignore[attr-defined]
    assert sleeps[:3] == [5.0, 12.0, 17.0]  # Retry-After 12 floors the next wait
    assert max(sleeps) == 30.0


def test_login_uses_remember_issuer_from_the_environment(
    issuer: FakeIssuer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REMEMBER_ISSUER", ISSUER)
    assert main(["login"]) == 0
    assert _stored_key() == issuer.issued_key


def test_login_refuses_a_key_from_another_issuer(issuer: FakeIssuer) -> None:
    issuer.issued_key = make_key(jti="key-new", iss="https://other.test")
    assert main(["login", "--issuer", ISSUER]) == 1
    assert load_credentials() is None
    assert issuer.revoked == [issuer.issued_key]  # withdrawn at once


def test_login_denied_writes_nothing(issuer: FakeIssuer) -> None:
    _store_old_key()
    issuer.poll_script = ["access_denied"]
    assert main(["login", "--issuer", ISSUER]) == 1
    assert _stored_key() == OLD_KEY
    assert _journal_keys() == []  # the step-1 entry is withdrawn
    assert issuer.revoked == []


def test_login_ctrl_c_exits_130_with_nothing_written(
    issuer: FakeIssuer, monkeypatch: pytest.MonkeyPatch
) -> None:
    _store_old_key()

    def interrupt(_seconds: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("remember.issuer.time.sleep", interrupt)
    assert main(["login", "--issuer", ISSUER]) == 130
    assert _stored_key() == OLD_KEY
    assert _journal_keys() == []


def test_login_replaces_a_version_1_file(
    issuer: FakeIssuer, capsys: pytest.CaptureFixture[str]
) -> None:
    _config().mkdir(parents=True, exist_ok=True)
    path = _config() / "credentials.json"
    path.write_text(json.dumps({"version": 1, "access_token": "umc_dp_x"}))
    path.chmod(0o600)
    assert main(["login", "--issuer", ISSUER]) == 0
    assert "not a version-2 credential file" in capsys.readouterr().err
    assert _stored_key() == issuer.issued_key


# --- re-login: journal → mint → replace → revoke ---------------------------------


def test_relogin_order_journal_mint_replace_revoke(issuer: FakeIssuer) -> None:
    _store_old_key()
    seen: dict[str, object] = {}

    def at_mint() -> None:
        seen["mint_journal"] = _journal_keys()
        seen["mint_file"] = _stored_key()

    def at_revoke(token: str) -> None:
        seen["revoked_token"] = token
        seen["revoke_file"] = _stored_key()
        seen["revoke_journal"] = _journal_keys()

    issuer.on_mint = at_mint
    issuer.on_revoke = at_revoke
    assert main(["login", "--issuer", ISSUER]) == 0
    # Step 1 happened before step 2: the old key was journalled, still stored.
    assert seen["mint_journal"] == [OLD_KEY]
    assert seen["mint_file"] == OLD_KEY
    # Step 3 happened before step 4: the file already names the new key.
    assert seen["revoked_token"] == OLD_KEY
    assert seen["revoke_file"] == issuer.issued_key
    assert seen["revoke_journal"] == [OLD_KEY]
    # Step 4 confirmed: the entry is gone.
    assert _journal_keys() == []
    assert issuer.revoked == [OLD_KEY]


def test_failed_replace_withdraws_the_new_key(
    issuer: FakeIssuer, monkeypatch: pytest.MonkeyPatch
) -> None:
    _store_old_key()

    def broken_write(**_kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("remember.login.write_credentials", broken_write)
    assert main(["login", "--issuer", ISSUER]) == 1
    assert _stored_key() == OLD_KEY  # still the old, working key
    assert _journal_keys() == []  # step-1 entry discarded
    assert issuer.revoked == [issuer.issued_key]  # the new key withdrawn


def test_failed_replace_journals_the_new_key_when_it_cannot_be_withdrawn(
    issuer: FakeIssuer, monkeypatch: pytest.MonkeyPatch
) -> None:
    _store_old_key()

    def broken_write(**_kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("remember.login.write_credentials", broken_write)
    issuer.revoke_status = 503
    assert main(["login", "--issuer", ISSUER]) == 1
    assert _stored_key() == OLD_KEY
    assert _journal_keys() == [issuer.issued_key]


# --- the §8.4 crash table: what the next CLI start does ---------------------------


@pytest.mark.parametrize("crashed_after_step", [1, 2])
def test_crash_before_replace_discards_the_entry(
    issuer: FakeIssuer, crashed_after_step: int
) -> None:
    """Rows 1–2: old file, journal names it → discarded, never revoked.

    (After step 2 the new key also exists at the issuer, unstored; the issuer
    lists it until it expires. Nothing on this machine can name it.)
    """
    _store_old_key()
    _journal(OLD_KEY)
    assert main(["whoami"]) == 0
    assert _journal_keys() == []
    assert issuer.calls("/oauth/revoke") == []
    assert _stored_key() == OLD_KEY


def test_crash_after_replace_revokes_the_old_key(issuer: FakeIssuer) -> None:
    """Row 3: new file, journal holds the old key → revoked at next start."""
    write_credentials(
        credentials=StoredCredentials(
            version=2, issuer=ISSUER, key=SecretStr(issuer.issued_key), key_id="key-new"
        )
    )
    _journal(OLD_KEY)
    assert main(["whoami"]) == 0
    assert issuer.revoked == [OLD_KEY]
    assert _journal_keys() == []


@pytest.mark.parametrize("final_status", [200, 401, 404])
def test_unconfirmed_revocation_is_retried_until_confirmed(
    issuer: FakeIssuer, final_status: int
) -> None:
    """Row 4: revocation unconfirmed → kept and retried; 401/404 count as done."""
    _store_old_key()
    issuer.revoke_status = 503
    assert main(["login", "--issuer", ISSUER]) == 0  # the new key is kept
    assert _stored_key() == issuer.issued_key
    assert _journal_keys() == [OLD_KEY]

    assert main(["whoami"]) == 0  # still 503: kept
    assert _journal_keys() == [OLD_KEY]

    issuer.revoke_status = final_status
    assert main(["whoami"]) == 0
    assert _journal_keys() == []


def test_unreadable_journal_is_kept(issuer: FakeIssuer) -> None:
    _config().mkdir(parents=True, exist_ok=True)
    journal = _config() / "pending-revocation.json"
    journal.write_text("{garbage")
    journal.chmod(0o600)
    assert main(["whoami"]) == 1
    assert journal.read_text() == "{garbage"


# --- logout ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "code", "kept"),
    [(200, 0, False), (401, 0, False), (404, 0, False), (503, 1, True)],
)
def test_logout_revokes_then_unlinks(
    issuer: FakeIssuer, status: int, code: int, kept: bool
) -> None:
    _store_old_key()
    issuer.revoke_status = status
    assert main(["logout"]) == code
    assert (_stored_key() == OLD_KEY) is kept
    revocations = issuer.calls("/oauth/revoke")
    assert len(revocations) == 1
    assert b"token_type_hint=access_token" in revocations[0].content


def test_logout_without_a_file_is_success(issuer: FakeIssuer) -> None:
    assert main(["logout"]) == 0
    assert issuer.requests == []


def test_logout_of_a_self_hosted_entry_makes_no_call(issuer: FakeIssuer) -> None:
    write_credentials(
        credentials=StoredCredentials(
            version=2, api_url="http://127.0.0.1:8000", key="shared"
        )
    )
    assert main(["logout"]) == 0
    assert load_credentials() is None
    assert issuer.requests == []


def test_logout_retries_the_journal(issuer: FakeIssuer) -> None:
    write_credentials(
        credentials=StoredCredentials(
            version=2, issuer=ISSUER, key=SecretStr(issuer.issued_key), key_id="key-new"
        )
    )
    _journal(OLD_KEY)
    assert main(["logout"]) == 0
    assert set(issuer.revoked) == {OLD_KEY, issuer.issued_key}


# --- switch and whoami -------------------------------------------------------------


def test_switch_stores_the_resolved_project(
    issuer: FakeIssuer, capsys: pytest.CaptureFixture[str]
) -> None:
    _store_old_key()
    assert main(["switch", "notes"]) == 0
    assert "notes (p-notes)" in capsys.readouterr().out
    stored = load_credentials()
    assert stored is not None and stored.default_project == "p-notes"
    assert issuer.engine_requests() == []


def test_switch_refuses_a_project_outside_the_key(issuer: FakeIssuer) -> None:
    _store_old_key()
    assert main(["switch", "p-other"]) == 1
    stored = load_credentials()
    assert stored is not None and stored.default_project is None


def test_whoami_reads_claims_without_any_call(
    issuer: FakeIssuer, capsys: pytest.CaptureFixture[str]
) -> None:
    _store_old_key()
    assert main(["whoami"]) == 0
    out = capsys.readouterr().out
    assert f"Issuer: {ISSUER}" in out
    assert "Key: key-old" in out
    assert "p-docs, p-notes" in out
    assert "memory:read, memory:write" in out
    assert OLD_KEY not in out
    assert issuer.requests == []


def test_whoami_adds_the_account_view_with_account_read(
    issuer: FakeIssuer, capsys: pytest.CaptureFixture[str]
) -> None:
    key = make_key(permissions=("memory:read", "account:read"))
    write_credentials(
        credentials=StoredCredentials(
            version=2, issuer=ISSUER, key=SecretStr(key), key_id="k"
        )
    )
    assert main(["whoami"]) == 0
    assert '"role": "OWNER"' in capsys.readouterr().out
    assert issuer.engine_requests() == []


# --- issuer metadata ----------------------------------------------------------------


def test_metadata_must_name_the_issuer_it_was_fetched_for(issuer: FakeIssuer) -> None:
    original = issuer.metadata
    issuer.metadata = lambda: {**original(), "issuer": "https://evil.test"}  # type: ignore[method-assign]
    with pytest.raises(IssuerError, match="names issuer"):
        fetch_issuer_metadata(ISSUER, http=issuer.http())


def test_metadata_refuses_cross_origin_redirects() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "https://evil.test/meta"})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(IssuerError, match="cross-origin redirect"):
        fetch_issuer_metadata(ISSUER, http=http)


def test_unavailable_metadata_names_the_url(issuer: FakeIssuer) -> None:
    issuer.metadata_status = 500
    with pytest.raises(IssuerError, match=r"\.well-known/oauth-authorization-server"):
        fetch_issuer_metadata(ISSUER, http=issuer.http())


def test_issuer_must_be_https() -> None:
    with pytest.raises(IssuerError, match="https"):
        fetch_issuer_metadata("http://issuer.example", http=httpx.Client())
