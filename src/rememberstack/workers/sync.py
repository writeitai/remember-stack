"""The watch loop (lifecycle §2, D55): one recorded poll cycle per pass.

The efficiency ladder's cheapest exits live here: unchanged revision → no
fetch; a file being actively edited coalesces to one ingested version per
stability window (the debounce discipline); identical bytes are the
content-hash no-op downstream. Deletion observations tombstone the lineage
loudly; the cascade is the delete worker's job.
"""

from collections.abc import Mapping
from datetime import datetime
from datetime import timedelta
from datetime import UTC
from typing import Final
from uuid import UUID

from pydantic import Field
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict

from rememberstack.model import DocumentUpload
from rememberstack.model import ProcessingLane
from rememberstack.model import SourceItem
from rememberstack.model import SyncCycleSummary
from rememberstack.ports.connector import WatchedSourcePort
from rememberstack.spine.sync import SyncCatalog
from rememberstack.workers.e0 import UploadIngestor

DEFAULT_VERSIONING_MODE_BY_KIND: Final = {
    # spike 6's starting heuristic: edit-in-place leans living;
    # replace-whole-file and rolling logs lean archival (snapshot)
    "watched_directory": "living",
    "google_drive": "living",
    "upload": "snapshot",
}


class SyncSettings(BaseSettings):
    """The watch loop's knobs (starting points, D22)."""

    model_config = SettingsConfigDict(env_prefix="REMEMBERSTACK_SYNC_")

    debounce_quiet_seconds: float = Field(default=120.0, ge=0.0)
    lane: ProcessingLane = ProcessingLane.STEADY


class SyncCycleRunner:
    """Run one recorded poll cycle for one (deployment, source kind)."""

    def __init__(
        self, *, catalog: SyncCatalog, ingestor: UploadIngestor, settings: SyncSettings
    ) -> None:
        """Bind the runner to the cycle catalog, ingest path, and settings."""
        self._catalog = catalog
        self._ingestor = ingestor
        self._settings = settings

    def run_cycle(
        self, *, deployment_id: UUID, source_kind: str, source: WatchedSourcePort
    ) -> SyncCycleSummary:
        """One poll pass: observe, debounce, ingest, tombstone, record.

        The cycle row always reaches ``completed_at`` — one bad item (or a
        failed poll) must not strand a cycle that already stamped versions,
        because an eternally open cycle can never pass reconciliation's
        finalization barrier. Lost items are counted on the row instead.
        """
        known = self._catalog.known_revisions(
            deployment_id=deployment_id, source_kind=source_kind
        )
        cycle_id = self._catalog.open_cycle(
            deployment_id=deployment_id, source_kind=source_kind
        )
        items: tuple[SourceItem, ...] = ()
        ingested: list[UUID] = []
        deletions: list[UUID] = []
        unchanged = 0
        debounced = 0
        failed = 0
        now = datetime.now(tz=UTC)
        quiet = timedelta(seconds=self._settings.debounce_quiet_seconds)
        versioning_mode = DEFAULT_VERSIONING_MODE_BY_KIND.get(source_kind, "snapshot")
        try:
            items = source.poll(known=known)
            for item in items:
                try:
                    outcome = self._observe_item(
                        deployment_id=deployment_id,
                        source_kind=source_kind,
                        cycle_id=cycle_id,
                        source=source,
                        item=item,
                        known=known,
                        now=now,
                        quiet=quiet,
                        versioning_mode=versioning_mode,
                    )
                except Exception:  # noqa: BLE001 — the pass must stay recorded
                    failed += 1
                    continue
                kind, ref = outcome
                if kind == "ingested" and ref is not None:
                    ingested.append(ref)
                elif kind == "deleted" and ref is not None:
                    deletions.append(ref)
                elif kind == "unchanged":
                    unchanged += 1
                elif kind == "debounced":
                    debounced += 1
        finally:
            self._catalog.complete_cycle(
                cycle_id=cycle_id, observed=len(items), failed=failed
            )
        return SyncCycleSummary(
            cycle_id=cycle_id,
            observed=len(items),
            ingested=tuple(ingested),
            unchanged=unchanged,
            debounced=debounced,
            deletions_observed=tuple(deletions),
            failed=failed,
        )

    def _observe_item(
        self,
        *,
        deployment_id: UUID,
        source_kind: str,
        cycle_id: UUID,
        source: WatchedSourcePort,
        item: SourceItem,
        known: Mapping[str, str],
        now: datetime,
        quiet: timedelta,
        versioning_mode: str,
    ) -> tuple[str, UUID | None]:
        """Route one observed item down the efficiency ladder."""
        if item.deleted:
            lineage = self._catalog.observe_deletion(
                deployment_id=deployment_id,
                source_kind=source_kind,
                source_ref=item.source_ref,
                cycle_id=cycle_id,
            )
            return ("deleted", lineage)
        if known.get(item.source_ref) == item.revision:
            return ("unchanged", None)  # revision no-op: no fetch, no bytes moved
        if now - item.modified_at < quiet:
            return ("debounced", None)  # actively edited: coalesce to a later cycle
        content = source.fetch(source_ref=item.source_ref)
        result = self._ingestor.ingest_observed(
            deployment_id=deployment_id,
            source_kind=source_kind,
            source_ref=item.source_ref,
            upload=DocumentUpload(
                filename=item.filename or item.source_ref,
                mime=item.mime,
                content=content,
                source_path=item.source_path,
            ),
            versioning_mode=versioning_mode,
            source_modified_at=item.modified_at,
            source_version_ref=item.revision,
            sync_cycle_id=cycle_id,
            lane=self._settings.lane,
        )
        if result.created:
            return ("ingested", result.version_id)
        return ("unchanged", None)  # content-hash no-op (revision churn, same bytes)
