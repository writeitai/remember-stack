"""Read an E0 provenance sidecar without dropping persisted labels or locators."""

import hashlib

from rememberstack.model import NonRetryableHandlerError
from rememberstack.model import ObjectKey
from rememberstack.model.conversion import SourceMapEntry
from rememberstack.model.occurrence_provenance import parse_persisted_source_map
from rememberstack.model.occurrence_provenance import PersistedSourceMapRef
from rememberstack.model.occurrence_provenance import ProvenanceMetadataCorruptError
from rememberstack.model.occurrence_provenance import ProvenanceMetadataMissingError
from rememberstack.ports.object_store import ObjectStorePort


def read_provenance_bytes(
    *, artifact_store: ObjectStorePort, uri: str, kind: str
) -> bytes:
    """Read a referenced provenance object; absence is a retryable failure."""
    try:
        return artifact_store.read_bytes(key=ObjectKey(uri))
    except FileNotFoundError as error:
        raise ProvenanceMetadataMissingError(
            f"{kind} {uri} is missing; cannot publish occurrence provenance"
        ) from error


def read_source_map(
    *, artifact_store: ObjectStorePort, ref: PersistedSourceMapRef
) -> tuple[SourceMapEntry, ...]:
    """Check the persisted checksum before returning the map's source intervals."""
    payload = read_provenance_bytes(
        artifact_store=artifact_store, uri=ref.uri, kind="source map"
    )
    if ref.sha256 is not None:
        digest = hashlib.sha256(payload).hexdigest()
        if digest != ref.sha256:
            raise NonRetryableHandlerError(
                f"source map {ref.uri} sha256 mismatch:"
                f" expected {ref.sha256}, got {digest}"
            )
    try:
        return parse_persisted_source_map(payload=payload, uri=ref.uri).entries
    except ProvenanceMetadataCorruptError as error:
        raise NonRetryableHandlerError(str(error)) from error
