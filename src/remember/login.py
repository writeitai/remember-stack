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

import httpx

from remember.connection import resolve_project
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
    for entry in journal.entries:
        secret = entry.key.get_secret_value()
        label = entry.key_id or "unknown id"
        if secret == current_key:
            # Journalled but never replaced (a crash before step 3): it is the
            # key in use, so the entry is discarded, not revoked.
            forget_journalled_key(key=secret)
            continue
        try:
            metadata = fetch_issuer_metadata(entry.issuer, http=http)
            confirmed = revoke_key(
                http=http,
                metadata=metadata,
                key=secret,
                timeout=RECOVERY_TIMEOUT_SECONDS,
            )
        except IssuerError:
            confirmed = False
        if confirmed:
            forget_journalled_key(key=secret)
            print(f"revoked replaced key {label} at {entry.issuer}", file=sys.stderr)
        else:
            _warn(
                f"replaced key {label} is still live at {entry.issuer} and could "
                "not be revoked; the next `remember` command will retry"
            )


def login(
    *, issuer: str, http: httpx.Client, sleep: Callable[[float], None] | None = None
) -> StoredCredentials:
    """Run the device grant and store the new key, replacing any old one safely."""
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
            print(f"Open {authorization.verification_uri} and enter the code:")
            print(f"  {authorization.user_code}")
            if authorization.verification_uri_complete:
                print(f"Or open {authorization.verification_uri_complete}")
            print("Waiting for approval...", flush=True)
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
        try:
            credentials = _stored_from_issued(issuer=issuer, issued=issued)
            try:
                write_credentials(credentials=credentials)
            except DurabilityUnconfirmed as error:
                # The file is in place and names the new key: carry on.
                _warn(str(error))
        except BaseException:
            _withdraw_new_key(http=http, issuer=issuer, issued=issued)
            if old is not None:
                forget_journalled_key(key=old.key.get_secret_value())
            raise
        # Step 4 — revoke the old key.
        if old is not None:
            if revoke_key(http=http, metadata=metadata, key=old.key.get_secret_value()):
                forget_journalled_key(key=old.key.get_secret_value())
            else:
                _warn(
                    f"the previous key {old.key_id or ''} could not be revoked yet; "
                    "it stays journalled and the next `remember` command retries"
                )
        return credentials


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


def logout(*, http: httpx.Client) -> int:
    """Revoke the stored key, then remove the file. Returns the exit code.

    Confirmed (``2xx``, ``401``, ``404``) → removed, ``0``; unconfirmed →
    kept, ``1``; no file → ``0``. A self-hosted entry is just removed.
    """
    with credential_lock():
        retry_journal(http=http)
        stored = load_credentials()
        if stored is None:
            return 0
        if stored.issuer and stored.key is not None:
            try:
                metadata = fetch_issuer_metadata(stored.issuer, http=http)
                confirmed = revoke_key(
                    http=http, metadata=metadata, key=stored.key.get_secret_value()
                )
            except IssuerError as error:
                print(f"error: {error.detail}; the key was kept", file=sys.stderr)
                return 1
            if not confirmed:
                print(
                    f"error: {stored.issuer} did not confirm the revocation; "
                    "the key was kept",
                    file=sys.stderr,
                )
                return 1
        unlink_credentials()
        return 0


def switch(*, project: str, http: httpx.Client) -> StoredCredentials:
    """Make ``project`` the stored default after proving the key covers it."""
    with credential_lock():
        stored = load_credentials()
        if stored is None or stored.key is None or not stored.issuer:
            raise CredentialError("`remember switch` needs a key from `remember login`")
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
        print(f"Default project: {resolved.name} ({resolved.project})")
        return updated


def whoami(*, http: httpx.Client) -> int:
    """Print the stored key's own claims; add the issuer's view when allowed.

    Never calls the engine. The issuer's account view is fetched only when the
    key carries ``account:read`` and the issuer advertises an account API.
    """
    stored = load_credentials()
    if stored is None:
        print("Not signed in. Run `remember login`.")
        return 1
    if not stored.issuer or stored.key is None:
        print(f"Self-hosted engine: {stored.api_url}")
        return 0
    claims = signed_key_claims(stored.key.get_secret_value())
    if claims is None:
        raise CredentialError("the stored key is not a signed key")
    projects = (
        claims.projects
        if isinstance(claims.projects, str)
        else ", ".join(claims.projects or [])
    )
    expires = claims.expires_at or stored.expires_at
    print(f"Issuer: {stored.issuer}")
    print(f"Key: {stored.key_id or claims.jti or 'unknown'}")
    print(f"Expires: {expires.isoformat() if expires else 'unknown'}")
    print(f"Projects: {projects or 'none'}")
    print(f"Permissions: {', '.join(claims.permissions) or 'none'}")
    print(f"Default project: {stored.default_project or '(issuer default)'}")
    if "account:read" not in claims.permissions:
        return 0
    try:
        metadata = fetch_issuer_metadata(stored.issuer, http=http)
    except IssuerError as error:
        _warn(error.detail)
        return 0
    if not metadata.remember_account_endpoint:
        return 0
    from remember.client import AccountApi
    from remember.connection import resolve_connection

    connection = resolve_connection(api_key=stored.key.get_secret_value())
    try:
        view = AccountApi(connection=connection, http=http).whoami()
    except MemoryApiError as error:
        _warn(f"the issuer's account view is unavailable: {error}")
        return 0
    print("Account:")
    print(json.dumps(view, indent=2, sort_keys=True))
    return 0


__all__ = ("login", "logout", "retry_journal", "switch", "whoami")
