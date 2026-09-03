"""Provider-neutral values for the D50/D60 single-deployment auth perimeter."""

from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import SecretBytes


class PerimeterScope(StrEnum):
    """What an authenticated caller may do, as a closed vocabulary.

    Three values, deliberately. ``INGEST`` exists for D62: a browser that may
    add one document must not also be able to create a connector — standing
    configuration that keeps pulling from a third-party system after the tab
    closes. A scope vocabulary that grows by string concatenation fails open
    the first time somebody adds a route, and the perimeter is not the place
    to build a permission language: it exists to answer one narrow question
    cheaply, in front of every request.

    ``WRITE`` includes everything. ``READ`` and ``INGEST`` are disjoint: an
    ingest credential may reach only the one ingest route, not retrieval or
    any other mutation.
    """

    READ = "read"
    INGEST = "ingest"
    WRITE = "write"

    def covers(self, *, required: "PerimeterScope") -> bool:
        """True when this scope satisfies ``required``."""
        if self is PerimeterScope.WRITE:
            return True
        return self is required


class CredentialKind(StrEnum):
    """Which kind of credential authenticated this request (D60).

    Audit needs it, and audit alone: the canonical actor id in a D8 event is
    the kind's marker followed by the credential id — ``dpcred:<jti>`` for a
    deployment token, ``browsercred:<jti>`` for a browser credential — so
    "which credential did this" and "which person did this" stay separate
    questions with separate answers.

    It decides nothing about authority. ``scope`` does that, and a reader who
    finds this value used in an authorisation branch has found a bug.
    """

    DEPLOYMENT = "deployment"
    BROWSER = "browser"

    @property
    def actor_marker(self) -> str:
        """The prefix an audit event puts in front of the credential id."""
        if self is CredentialKind.DEPLOYMENT:
            return "dpcred:"
        return "browsercred:"


class PerimeterCredential(BaseModel):
    """Opaque perimeter credential passed to the configured auth adapter."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scheme: Annotated[str, Field(min_length=1)]
    value: SecretBytes


class AuthenticatedContext(BaseModel):
    """Authenticated principal inside one deployment-wide trust domain.

    ``principal`` names *what kind* of caller this is, not who: it is a
    transport fact. ``subject`` is the person the credential was issued to,
    when the credential names one — a browser credential does, a shared
    self-host secret does not. Keeping them apart is what lets an audit record
    say "this credential, acting for this member" instead of impersonating
    someone.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    deployment_id: UUID
    principal: Annotated[str, Field(min_length=1)]
    #: The delegating human, when the credential names one.
    #:
    #: A browser credential names a person. A deployment token does not — it is
    #: an organisation-wide machine credential, and treating its subject as a
    #: human would put a credential id where an audit expects someone
    #: accountable. So this stays ``None`` for machine credentials, and
    #: :attr:`credential_id` carries what they do name.
    subject: str | None = None
    #: The credential itself, when it names one (a JWT's ``jti``).
    #:
    #: Separate from ``subject`` because they answer different questions: which
    #: credential acted, and on whose behalf. A machine credential answers only
    #: the first; a browser credential answers both.
    credential_id: str | None = None
    #: Which kind of credential this is, for audit attribution only.
    #:
    #: ``None`` for a credential that predates the distinction — the self-host
    #: shared secret, which names neither a person nor an issued credential.
    credential_kind: CredentialKind | None = None
    #: Full authority unless the credential says otherwise. A credential that
    #: predates scopes — the self-host shared secret — is unrestricted, which
    #: is what it has always been.
    scope: PerimeterScope = PerimeterScope.WRITE

    @property
    def actor_id(self) -> str | None:
        """The canonical id a D8 audit event records for this caller.

        ``dpcred:<jti>`` or ``browsercred:<jti>`` — the credential, never the
        person. The delegating member, when there is one, belongs in the
        event's ``resource.scope``, which is where D53 already puts it.
        """
        if self.credential_kind is None or self.credential_id is None:
            return None
        return f"{self.credential_kind.actor_marker}{self.credential_id}"
