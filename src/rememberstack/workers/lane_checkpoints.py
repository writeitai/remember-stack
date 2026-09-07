"""Provider-neutral durable storage for conversion-lane checkpoints.

The convert worker keeps these objects under the source identity prefix so a
retry can reuse a successful lane without importing a concrete converter
adapter. Keys are not representation objects and must not be indexed for
search. Source deletion purges ``{doc_id}/{content_hash}/conversion-checkpoints``.
"""

import hashlib
import re

from rememberstack.model import ObjectAlreadyExistsError
from rememberstack.model import ObjectKey
from rememberstack.ports.object_store import ObjectStorePort

_UNSAFE_KEY_CHARS = re.compile(r"[^A-Za-z0-9._-]")


class ObjectStoreLaneCheckpoints:
    """Private conversion artifacts under one source identity prefix.

    Keys are ``{prefix}/{lane}/{fingerprint-digest}.json``.
    """

    def __init__(self, *, object_store: ObjectStorePort, prefix: str) -> None:
        """Bind one artifact store to the source's checkpoint directory."""
        self._store = object_store
        self._prefix = prefix.strip("/")

    def load(self, *, lane: str, fingerprint: str) -> bytes | None:
        """Read one checkpoint, treating a missing object as a miss."""
        try:
            return self._store.read_bytes(
                key=self._key(lane=lane, fingerprint=fingerprint)
            )
        except FileNotFoundError:
            return None

    def save(self, *, lane: str, fingerprint: str, payload: bytes) -> None:
        """Write once. An occupied key means a prior attempt already persisted."""
        try:
            self._store.write_bytes(
                key=self._key(lane=lane, fingerprint=fingerprint), content=payload
            )
        except ObjectAlreadyExistsError:
            return

    def _key(self, *, lane: str, fingerprint: str) -> ObjectKey:
        """One filesystem-safe object key for this lane fingerprint."""
        digest = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:32]
        lane_segment = _UNSAFE_KEY_CHARS.sub("-", lane).strip("-") or "lane"
        return ObjectKey(f"{self._prefix}/{lane_segment}/{digest}.json")
