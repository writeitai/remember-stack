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

    Two values, deliberately. A scope vocabulary that grows by string
    concatenation fails open the first time somebody adds a route, and the
    perimeter is not the place to build a permission language: it exists to
    answer one narrow question cheaply, in front of every request.

    ``WRITE`` includes everything ``READ`` allows. A credential that may change
    the memory may obviously also look at it.
    """

    READ = "read"
    WRITE = "write"

    def covers(self, *, required: "PerimeterScope") -> bool:
        """True when this scope satisfies ``required``."""
        if self is PerimeterScope.WRITE:
            return True
        return required is PerimeterScope.READ


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
    subject: str | None = None
    #: Full authority unless the credential says otherwise. A credential that
    #: predates scopes — the self-host shared secret — is unrestricted, which
    #: is what it has always been.
    scope: PerimeterScope = PerimeterScope.WRITE
