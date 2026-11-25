"""``remember login``, ``logout``, ``switch`` and ``whoami`` (D136 §8.4).

**Re-login order: journal, mint, replace, revoke.** A second login must never
leave the machine without a working key, and must never leave a replaced key
live with nothing on the machine naming it:

1. durably journal the **old** key (issuer, key id, secret);
2. mint the new key through the device grant;
3. atomically replace ``credentials.json`` with the new key;
4. revoke the old key through the issuer's ``revocation_endpoint`` and drop
   its journal entry once the issuer confirms (``2xx``, or ``401``/``404``
   meaning already revoked).

After a crash, :func:`retry_journal` (run at every CLI start) settles the
journal: an entry naming the key still in ``credentials.json`` was never
replaced and is discarded; any other entry is revoked. A failed (not crashed)
step 2 or 3 discards the step-1 entry itself; a failed step 3 also revokes the
new key, journalling it if that revocation cannot be confirmed.
"""

from __future__ import annotations

from collections.abc import Callable
import json
import sys
from typing import TextIO

import httpx

from remember.cli_output import failure
from remember.connection import resolve_project
from remember.credentials import confirm_credentials_durable
from remember.credentials import credential_lock
from remember.credentials import CredentialError
from remember.credentials import DurabilityUnconfirmed
from remember.credentials import forget_journalled_key
from remember.credentials import journal_key
from remember.credentials import load_credentials
from remember.credentials import load_pending_revocations
from remember.credentials import PendingRevocation
from remember.credentials import StoredCredentials
from remember.credentials import unlink_credentials
from remember.credentials import write_credentials
from remember.errors import MemoryApiError
from remember.issuer import DeviceGrantError
from remember.issuer import fetch_issuer_metadata
from remember.issuer import IssuedKey
from remember.issuer import IssuerError
from remember.issuer import normalize_issuer
from remember.issuer import poll_for_key
from remember.issuer import revoke_key
from remember.issuer import signed_key_claims
from remember.issuer import start_device_authorization

#: Revocations retried at CLI start run under the credential lock, so each
#: gets a short timeout.
RECOVERY_TIMEOUT_SECONDS = 5.0


def _warn(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr)


def retry_journal(*, http: httpx.Client) -> None:
    """Settle every journalled key; call with the credential lock held.

    Never raises for an individual entry: one unreachable issuer must not
    block the others or the command that triggered the retry.
    """
    journal = load_pending_revocations()
    if not journal.entries:
        return
    try:
        current = load_credentials()
    except CredentialError:
        _warn(
            "the credential file is unreadable, so journalled revocations were "
            "left alone (one of them may be the key still in use)"
        )
        return
    current_key = current.key.get_secret_value() if current and current.key else None
    if any(entry.key.get_secret_value() != current_key for entry in journal.entries):
        # Revoke a replaced key only once its replacement is durable.
        try:
            confirm_credentials_durable()
        except DurabilityUnconfirmed as error:
            _warn(f"{error}; replaced keys stay live until the file is durable")
            return
    for entry in journal.entries:
        secret = entry.key.get_secret_value()
        label = entry.key_id or "unknown id"
        if secret == current_key:
            # Journalled but never replaced (a crash before step 3): it is the
            # key in use, so the entry is discarded, not revoked.
            forget_journalled_key(key=secret)
            continue
        if _revoke_entry(http=http, entry=entry, timeout=RECOVERY_TIMEOUT_SECONDS):
            forget_journalled_key(key=secret)
            print(f"revoked replaced key {label} at {entry.issuer}", file=sys.stderr)
        else:
            _warn(
                f"replaced key {label} is still live at {entry.issuer} and could "
                "not be revoked; the next `remember` command will retry"
            )


def login(
    *,
    issuer: str,
    http: httpx.Client,
    sleep: Callable[[float], None] | None = None,
    progress: TextIO | None = None,
) -> StoredCredentials:
    """Run the device grant and store the new key, replacing any old one safely."""
    progress = progress if progress is not None else sys.stdout
    issuer = normalize_issuer(issuer)
    with credential_lock():
        retry_journal(http=http)
        metadata = fetch_issuer_metadata(issuer, http=http)
        try:
            existing = load_credentials()
        except CredentialError as error:
            _warn(f"{error}; it will be replaced")
            existing = None
        old: PendingRevocation | None = None
        if existing is not None and existing.issuer and existing.key is not None:
            old = PendingRevocation(
                issuer=existing.issuer, key=existing.key, key_id=existing.key_id
            )
        elif existing is not None:
            _warn(f"replacing the self-hosted entry for {existing.api_url}")

        # Step 1 — journal the old key before anything can replace it.
        if old is not None:
            journal_key(entry=old)
        try:
            # Step 2 — mint.
            authorization = start_device_authorization(http=http, metadata=metadata)
            print(
                f"Open {authorization.verification_uri} and enter the code:",
                file=progress,
            )
            print(f"  {authorization.user_code}", file=progress)
            if authorization.verification_uri_complete:
                print(
                    f"Or open {authorization.verification_uri_complete}", file=progress
                )
            print("Waiting for approval...", file=progress, flush=True)
            issued = poll_for_key(
                http=http, metadata=metadata, authorization=authorization, sleep=sleep
            )
        except BaseException:
            # Not a crash: nothing was minted (or nothing usable), so the old
            # key stays in use and its entry is withdrawn.
            if old is not None:
                forget_journalled_key(key=old.key.get_secret_value())
            raise
        # Step 3 — replace.
        durable = True
        try:
            credentials = _stored_from_issued(issuer=issuer, issued=issued)
            try:
                write_credentials(credentials=credentials)
            except DurabilityUnconfirmed as error:
                # The file names the new key, but a crash could still undo the
                # rename: the old key stays live and journalled until a later
                # command confirms the file durable.
                durable = False
                _warn(f"{error}; the previous key stays live for now")
        except BaseException:
            _withdraw_new_key(http=http, issuer=issuer, issued=issued)
            if old is not None:
                forget_journalled_key(key=old.key.get_secret_value())
            raise
        # Step 4 — revoke the old key.
        if old is not None and durable:
            if _revoke_entry(http=http, entry=old):
                forget_journalled_key(key=old.key.get_secret_value())
            else:
                _warn(
                    f"the previous key {old.key_id or ''} could not be revoked yet; "
                    "it stays journalled and the next `remember` command retries"
                )
        return credentials


def _revoke_entry(
    *, http: httpx.Client, entry: PendingRevocation, timeout: float = 10.0
) -> bool:
    """Revoke a journalled key at **its own** issuer; ``True`` once confirmed."""
    try:
        metadata = fetch_issuer_metadata(entry.issuer, http=http)
        return revoke_key(
            http=http,
            metadata=metadata,
            key=entry.key.get_secret_value(),
            timeout=timeout,
        )
    except IssuerError:
        return False


def _stored_from_issued(*, issuer: str, issued: IssuedKey) -> StoredCredentials:
    """The credential file for a newly issued key, refusing a foreign issuer."""
    claims = signed_key_claims(issued.key.get_secret_value())
    if claims is None or claims.iss.rstrip("/") != issuer:
        raise DeviceGrantError(
            detail=f"the issuer returned a key that is not a signed key issued by {issuer}"
        )
    return StoredCredentials(
        version=2,
        issuer=issuer,
        key=issued.key,
        key_id=issued.key_id,
        expires_at=issued.expires_at,
        default_project=issued.default_project,
    )


def _withdraw_new_key(*, http: httpx.Client, issuer: str, issued: IssuedKey) -> None:
    """Revoke a key that could not be stored; journal it if that fails."""
    secret = issued.key.get_secret_value()
    try:
        metadata = fetch_issuer_metadata(issuer, http=http)
        if revoke_key(http=http, metadata=metadata, key=secret):
            return
    except IssuerError:
        pass
    try:
        journal_key(
            entry=PendingRevocation(issuer=issuer, key=issued.key, key_id=issued.key_id)
        )
    except (CredentialError, OSError):
        _warn(
            f"a new key ({issued.key_id or 'unknown id'}) could be neither stored "
            f"nor revoked; revoke it with {issuer}"
        )


def logout(*, http: httpx.Client) -> bool:
    """Revoke the stored key and return whether a credential file was removed."""
    with credential_lock():
        retry_journal(http=http)
        stored = load_credentials()
        if stored is None:
            return False
        if stored.issuer and stored.key is not None:
            try:
                metadata = fetch_issuer_metadata(stored.issuer, http=http)
                confirmed = revoke_key(
                    http=http, metadata=metadata, key=stored.key.get_secret_value()
                )
            except IssuerError as error:
                raise failure(
                    code="revocation_unconfirmed",
                    detail=f"{error.detail}; the key was kept",
                    action="Check the issuer and network, then rerun `remember logout`.",
                ) from error
            if not confirmed:
                raise failure(
                    code="revocation_unconfirmed",
                    detail="The issuer did not confirm the revocation; the key was kept.",
                    action="Check the issuer and network, then rerun `remember logout`.",
                )
        unlink_credentials()
        return True


def switch(
    *, project: str, http: httpx.Client, machine: bool = False
) -> StoredCredentials:
    """Make ``project`` the stored default after proving the key covers it."""
    with credential_lock():
        stored = load_credentials()
        if stored is None or stored.key is None or not stored.issuer:
            raise failure(
                code="missing_credentials",
                detail="`remember switch` needs a signed key.",
                action="Run `remember login`, then select a project.",
            )
        claims = signed_key_claims(stored.key.get_secret_value())
        if claims is None:
            raise CredentialError("the stored key is not a signed key")
        resolved = resolve_project(
            key=stored.key.get_secret_value(),
            claims=claims,
            project=project,
            http=http,
            refresh=True,
        )
        updated = stored.model_copy(update={"default_project": resolved.project})
        try:
            write_credentials(credentials=updated)
        except DurabilityUnconfirmed as error:
            _warn(str(error))
        if machine:
            print(json.dumps({"project": resolved.project, "name": resolved.name}))
        else:
            print(f"Default project: {resolved.name} ({resolved.project})")
        return updated


def whoami(*, http: httpx.Client, machine: bool = False) -> int:
    """Print the stored key's own claims; add the issuer's view when allowed.

    Never calls the engine. The issuer's account view is fetched only when the
    key carries ``account:read`` and the issuer advertises an account API.
    """
    stored = load_credentials()
    if stored is None:
        raise failure(
            code="missing_credentials",
            detail="No stored credentials. Run `remember login`.",
            action="Run `remember login` for a hosted key or configure a self-hosted engine.",
        )
    if not stored.issuer or stored.key is None:
        if machine:
            print(json.dumps({"api_url": stored.api_url}))
        else:
            print(f"Self-hosted engine: {stored.api_url}")
        return 0
    claims = signed_key_claims(stored.key.get_secret_value())
    if claims is None:
        raise CredentialError("the stored key is not a signed key")
    expires = claims.expires_at or stored.expires_at
    identity: dict[str, object] = {
        "issuer": stored.issuer,
        "key_id": stored.key_id or claims.jti,
        "expires_at": expires.isoformat() if expires else None,
        "projects": claims.projects,
        "permissions": list(claims.permissions),
        "default_project": stored.default_project,
    }
    if not machine:
        projects = (
            claims.projects
            if isinstance(claims.projects, str)
            else ", ".join(claims.projects or [])
        )
        print(f"Issuer: {stored.issuer}")
        print(f"Key: {stored.key_id or claims.jti or 'unknown'}")
        print(f"Expires: {expires.isoformat() if expires else 'unknown'}")
        print(f"Projects: {projects or 'none'}")
        print(f"Permissions: {', '.join(claims.permissions) or 'none'}")
        print(f"Default project: {stored.default_project or '(issuer default)'}")
    if "account:read" in claims.permissions:
        view = _account_view(stored=stored, http=http)
        if view is not None:
            if machine:
                identity["account"] = view
            else:
                print("Account:")
                print(json.dumps(view, indent=2, sort_keys=True))
    if machine:
        print(json.dumps(identity))
    return 0


def _account_view(
    *, stored: StoredCredentials, http: httpx.Client
) -> dict[str, object] | None:
    """Read the optional account view without obscuring local identity claims."""
    assert stored.issuer is not None and stored.key is not None
    try:
        metadata = fetch_issuer_metadata(stored.issuer, http=http)
    except IssuerError as error:
        _warn(error.detail)
        return None
    if not metadata.remember_account_endpoint:
        return None
    from remember.client import AccountApi
    from remember.connection import resolve_connection

    connection = resolve_connection(api_key=stored.key.get_secret_value())
    try:
        view = AccountApi(connection=connection, http=http).whoami()
    except MemoryApiError as error:
        _warn(f"the issuer's account view is unavailable: {error}")
        return None
    return view


__all__ = ("login", "logout", "retry_journal", "switch", "whoami")
