from asyncio import Queue, create_task, sleep

import pytest

from brood.monitor import Monitor, drain_queue


async def sleep_then_put(queue: Queue[float], s: float) -> None:
    await sleep(s)
    await queue.put(s)


async def test_drain_queue() -> None:
    queue: Queue[float] = Queue()

    await sleep_then_put(queue, 0)
    create_task(sleep_then_put(queue, 0.5))

    assert queue.qsize() == 1
    assert [0, 0.5] == await drain_queue(queue, buffer=1)


@pytest.mark.parametrize("command", ["sleep 1"])
async def test_queues_are_shared(once_monitor: Monitor, command: str) -> None:
    async with once_monitor:
        manager = once_monitor.managers[0]
        renderer = once_monitor.renderer

        assert once_monitor.process_messages is manager.process_messages
        assert once_monitor.internal_messages is manager.internal_messages
        assert once_monitor.process_events is manager.process_events

        assert once_monitor.process_messages is renderer.process_messages
        assert once_monitor.internal_messages is renderer.internal_messages
        assert once_monitor.process_events is renderer.process_events
