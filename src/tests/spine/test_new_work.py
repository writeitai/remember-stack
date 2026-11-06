"""D147: rows created inside a collector are announced once, after the work."""

from datetime import datetime
from datetime import UTC
from uuid import UUID

import pytest

from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingLane
from rememberstack.model import QueueRoute
from rememberstack.spine.new_work import announce_new_work
from rememberstack.spine.new_work import collect_new_work
from rememberstack.spine.new_work import NewWork
from rememberstack.spine.new_work import record_new_work

_ROUTE = QueueRoute(
    deployment_id=UUID(int=1), stage=PipelineStage.CONVERT, lane=ProcessingLane.STEADY
)


class _Queue:
    def __init__(self, *, fail: bool = False) -> None:
        self.announced: list[tuple[UUID, QueueRoute, datetime]] = []
        self.fail = fail

    def announce(
        self,
        *,
        processing_id: UUID,
        route_snapshot: QueueRoute,
        not_before_snapshot: datetime,
    ) -> None:
        if self.fail:
            raise RuntimeError("queue down")
        self.announced.append((processing_id, route_snapshot, not_before_snapshot))


def test_only_rows_created_inside_the_collector_are_announced_once() -> None:
    later = datetime(2026, 11, 7, tzinfo=UTC)
    record_new_work(
        work=NewWork(processing_id=UUID(int=9), route=_ROUTE, not_before=None)
    )
    with collect_new_work() as created:
        for processing_id, not_before in ((1, None), (2, later), (1, None)):
            record_new_work(
                work=NewWork(
                    processing_id=UUID(int=processing_id),
                    route=_ROUTE,
                    not_before=not_before,
                )
            )
    record_new_work(
        work=NewWork(processing_id=UUID(int=8), route=_ROUTE, not_before=None)
    )

    queue = _Queue()
    announce_new_work(queue=queue, work=created)

    assert [item[0] for item in queue.announced] == [UUID(int=1), UUID(int=2)]
    assert queue.announced[1][2] == later
    assert queue.announced[0][2].tzinfo is not None


def test_a_failed_announcement_is_logged_not_raised(
    caplog: pytest.LogCaptureFixture,
) -> None:
    announce_new_work(
        queue=_Queue(fail=True),
        work=(NewWork(processing_id=UUID(int=1), route=_ROUTE, not_before=None),),
    )
    assert "janitor" in caplog.text
