"""Attributed programmatic raw reads (provider mounts enforce their own audit)."""

from datetime import datetime
from datetime import UTC
import json
from pathlib import Path
from uuid import UUID

from rememberstack.core import storage_class_for
from rememberstack.model import ObjectKey
from rememberstack.ports.object_store import ObjectStorePort


class RawAccessDenied(Exception):
    """An unattributed raw read was refused."""


class AuditedRawReader:
    """The ONLY way to read an original — because reads must be logged.

    D51's audit property comes from logging, not from keeping raw
    unmounted: a mount read is still a read. So raw access is offered
    exclusively through this reader, which records the accessor and the
    stated purpose before returning bytes and refuses an unattributed
    request outright rather than logging a blank.
    """

    def __init__(self, *, raw_store: ObjectStorePort, audit_log: Path) -> None:
        """Bind the reader to the raw store and its append-only audit log."""
        self._raw_store = raw_store
        self._audit_log = audit_log

    def read(
        self, *, deployment_id: UUID, raw_uri: str, accessor: str, purpose: str
    ) -> bytes:
        """Read one original, recording who read it and why."""
        if not accessor.strip() or not purpose.strip():
            raise RawAccessDenied(
                "raw access requires an accessor and a stated purpose:"
                " originals are audited, never anonymously readable"
            )
        content = self._raw_store.read_bytes(key=ObjectKey(raw_uri))
        self._append(
            {
                "at": datetime.now(tz=UTC).isoformat(),
                "deployment_id": str(deployment_id),
                "raw_uri": raw_uri,
                "accessor": accessor,
                "purpose": purpose,
                "bytes": len(content),
            }
        )
        return content

    def entries(self) -> tuple[dict[str, object], ...]:
        """The audit trail, oldest first (the operator's read surface)."""
        if not self._audit_log.exists():
            return ()
        return tuple(
            json.loads(line)
            for line in self._audit_log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )

    def _append(self, entry: dict[str, object]) -> None:
        """Append one audit record (the log is append-only by construction)."""
        self._audit_log.parent.mkdir(parents=True, exist_ok=True)
        with self._audit_log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")


__all__ = ("AuditedRawReader", "RawAccessDenied", "storage_class_for")
