from asyncio import Queue, create_task, sleep

from brood.monitor import drain_queue


async def sleep_then_put(queue: Queue[float], s: float) -> None:
    await sleep(s)
    await queue.put(s)


async def test_drain_queue_with_buffer() -> None:
    queue: Queue[float] = Queue()

    await sleep_then_put(queue, 0)
    create_task(sleep_then_put(queue, 0.5))

    assert queue.qsize() == 1
    assert [0, 0.5] == await drain_queue(queue, buffer=1)


async def test_drain_queue() -> None:
    queue: Queue[float] = Queue()

    await sleep_then_put(queue, 0)
    create_task(sleep_then_put(queue, 0.5))

    assert queue.qsize() == 1
    assert [0] == await drain_queue(queue, buffer=None)
