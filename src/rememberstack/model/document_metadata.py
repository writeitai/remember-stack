"""D134 general document metadata: the same fields for every format family.

A converter reads these from the file and returns them with its result
(``ConversionResult.metadata``); values are what the source *declares*, not
verified facts.
"""

from typing import Any
from typing import Self

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import model_validator

from rememberstack.model.queue import UTCDateTime


class DocumentPerson(BaseModel):
    """One person in a document's authors or recipients.

    ``name`` is a display name ("Alice Novak"), ``address`` an email address
    or handle (``alice@acme.com``, ``@alice``). Either may be absent, not both.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str | None = None
    address: str | None = None

    @model_validator(mode="after")
    def names_someone(self) -> Self:
        """A person with neither a name nor an address identifies nobody."""
        if not (self.name and self.name.strip()) and not (
            self.address and self.address.strip()
        ):
            raise ValueError("a document person needs a name or an address")
        return self


class DocumentMetadata(BaseModel):
    """The general metadata a converter read from one document (D134 §2)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str | None = None
    authors: tuple[DocumentPerson, ...] = ()
    recipients: tuple[DocumentPerson, ...] = ()
    created_at: UTCDateTime | None = None
    modified_at: UTCDateTime | None = None
    language: str | None = None
    thread_ref: str | None = None
    extra: dict[str, Any] = {}
    """Family-specific fields; returned with results, never a general filter."""
