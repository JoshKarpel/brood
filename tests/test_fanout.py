from brood.fanout import Fanout
from brood.monitor import drain_queue


async def test_each_subscriber_gets_each_message() -> None:
    fq: Fanout[int] = Fanout()

    a = fq.consumer()
    b = fq.consumer()

    await fq.put(0)
    await fq.put(1)

    assert await drain_queue(a, buffer=None) == [0, 1]
    assert await drain_queue(b, buffer=None) == [0, 1]
