"""Identity and failure types for bounded reads of one immutable source.

A media source is too large to carry as `bytes`. These types describe what a
converter is allowed to know about a source it has not read, and how a read
refuses when it would exceed the bounds the caller accepted.
"""

from typing import Annotated

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from rememberstack.model.object_store import ObjectKey

Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class SourceIdentity(BaseModel):
    """What a converter may know about a source without reading it.

    The hash and size are the E0 facts recorded when the raw object was
    written, not values a converter or a caller supplies. `mime` is the
    caller's declared type and stays an untrusted hint: routing and rating
    belong to a structural probe of the actual bytes, never to this field.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    object_key: ObjectKey
    content_hash: Sha256Hex
    byte_size: int = Field(ge=0)
    mime: str = Field(min_length=1)


class SourceTooLargeError(Exception):
    """A read or materialization would exceed the bound the caller accepted.

    Raised *before* the bytes are moved. A converter declares what it can
    afford and is refused up front, rather than discovering the cost by
    exhausting the worker.
    """


class SourceHashMismatchError(Exception):
    """Materialized bytes did not hash to the source's recorded content hash.

    Immutable objects cannot legitimately change, so this is corruption or a
    wrong key — never a retryable condition on the same inputs.
    """


class SourceRangeError(Exception):
    """A byte range that is empty, reversed, or outside the source."""
