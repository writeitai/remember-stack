"""Self-host composition root for the optional local operational commands."""

from datetime import datetime
from pathlib import Path
from typing import Self
from uuid import UUID

import sqlalchemy
from sqlalchemy.engine import Engine

from rememberstack.adapters.selfhost import LocalFSObjectStore
from rememberstack.adapters.selfhost import SelfHostTaskQueue
from rememberstack.model import DeadLetterReplayResult
from rememberstack.model import OperationalReport
from rememberstack.model import ProcessingLane
from rememberstack.spine import ForgetCatalog
from rememberstack.spine import OperationalCatalog
from rememberstack.spine import OperationalSettings
from rememberstack.spine import ProjectionCatalog
from rememberstack.spine import WorkLedger
from rememberstack.spine import WorkLedgerSettings
from rememberstack.spine.settings import load_database_settings
from rememberstack.workers import CorpusFsBuilder
from rememberstack.workers import DeadLetterReplayer


class SelfHostOperations:
    """Compose local adapters around one explicitly owned database engine."""

    def __init__(self, *, engine: Engine) -> None:
        """Take ownership of an engine created for one CLI invocation."""
        self._engine = engine

    @classmethod
    def from_settings(cls) -> Self:
        """Create the local composition from the typed database setting."""
        return cls(
            engine=sqlalchemy.create_engine(load_database_settings().sqlalchemy_url())
        )

    def close(self) -> None:
        """Dispose the command-owned connection pool."""
        self._engine.dispose()

    def inspect(self, *, deployment_id: UUID) -> OperationalReport:
        """Build one bounded typed report."""
        return OperationalCatalog(
            engine=self._engine, settings=OperationalSettings()
        ).inspect(deployment_id=deployment_id)

    def replay(
        self,
        *,
        deployment_id: UUID,
        processing_id: UUID,
        attempt_allowance: int,
        lane: ProcessingLane | None,
        not_before: datetime | None,
    ) -> DeadLetterReplayResult:
        """Compose the authoritative replay transition with local delivery."""
        ForgetCatalog(engine=self._engine).assert_available(deployment_id=deployment_id)
        ledger = WorkLedger(engine=self._engine, settings=WorkLedgerSettings())
        return DeadLetterReplayer(
            ledger=ledger, queue=SelfHostTaskQueue(ledger=ledger)
        ).replay(
            deployment_id=deployment_id,
            processing_id=processing_id,
            attempt_allowance=attempt_allowance,
            lane=lane,
            not_before=not_before,
        )

    def rebuild(
        self, *, deployment_id: UUID, snapshot_root: Path, version: str
    ) -> dict[str, object]:
        """Invoke the P3 CorpusFS whole-rebuild implementation."""
        ForgetCatalog(engine=self._engine).assert_available(deployment_id=deployment_id)
        catalog = ProjectionCatalog(engine=self._engine)
        store = LocalFSObjectStore(root=snapshot_root)
        return CorpusFsBuilder(catalog=catalog, snapshot_store=store).build(
            deployment_id=deployment_id, version=version
        )
