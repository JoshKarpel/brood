from asyncio import Queue, QueueEmpty, Task, create_task, sleep
from typing import Awaitable, Callable, List, Optional, TypeVar

T = TypeVar("T")


def delay(delay: float, fn: Callable[[], Awaitable[T]]) -> Task[T]:
    async def delayed() -> T:
        await sleep(delay)
        return await fn()

    return create_task(delayed())


async def drain_queue(queue: Queue[T], *, buffer: Optional[float] = None) -> List[T]:
    items = [await queue.get()]

    while True:
        try:
            items.append(queue.get_nowait())
        except QueueEmpty:
            if buffer:
                await sleep(buffer)

                if not queue.empty():
                    continue
                else:
                    break
            else:
                break

    return items
