"""P1 purge wrapper that holds D91 table maintain locks around delete+optimize."""

from datetime import timedelta
from pathlib import Path
from uuid import UUID

from sqlalchemy.engine import Engine

from rememberstack.adapters.selfhost.lance import LanceChunkIndex
from rememberstack.spine.p1_maintain_lock import DEFAULT_P1_MAINTAIN_LOCK_WAIT
from rememberstack.spine.p1_maintain_lock import hold_p1_table_maintain_locks


class LockingP1Purge:
    """Honor the shared maintain lock before Lance ``delete_unverified`` prune."""

    def __init__(
        self,
        *,
        index: LanceChunkIndex,
        engine: Engine,
        lance_root: Path,
        lock_timeout: timedelta = DEFAULT_P1_MAINTAIN_LOCK_WAIT,
    ) -> None:
        """Bind the unlocked adapter, engine, and exact lance_root lock identity."""
        self._index = index
        self._engine = engine
        self._lance_root = lance_root
        self._lock_timeout = lock_timeout

    def purge_rows(
        self,
        *,
        deployment_id: UUID,
        chunk_ids: tuple[UUID, ...],
        claim_ids: tuple[UUID, ...],
        fact_ids: tuple[UUID, ...],
        entity_ids: tuple[UUID, ...],
    ) -> None:
        """Delete nominated rows while holding locks for the affected tables.

        Empty ID tuples skip that table's lock. A contended lock that stays
        held past ``lock_timeout`` raises ``P1MaintainLockTimeout`` so the
        forget step can retry instead of waiting forever.
        """
        tables = tuple(
            name
            for name, ids in (
                ("chunks", chunk_ids),
                ("claims", claim_ids),
                ("facts", fact_ids),
                ("entities", entity_ids),
            )
            if ids
        )
        with hold_p1_table_maintain_locks(
            engine=self._engine,
            lance_root=self._lance_root,
            tables=tables,
            timeout=self._lock_timeout,
        ):
            self._index.purge_rows(
                deployment_id=deployment_id,
                chunk_ids=chunk_ids,
                claim_ids=claim_ids,
                fact_ids=fact_ids,
                entity_ids=entity_ids,
            )

    def verify_rows_purged(
        self,
        *,
        deployment_id: UUID,
        chunk_ids: tuple[UUID, ...],
        claim_ids: tuple[UUID, ...],
        fact_ids: tuple[UUID, ...],
        entity_ids: tuple[UUID, ...],
    ) -> None:
        """Delegate verification; no Lance rewrite, so no maintain lock."""
        self._index.verify_rows_purged(
            deployment_id=deployment_id,
            chunk_ids=chunk_ids,
            claim_ids=claim_ids,
            fact_ids=fact_ids,
            entity_ids=entity_ids,
        )
