from __future__ import annotations

from asyncio import FIRST_COMPLETED, FIRST_EXCEPTION, CancelledError, create_task, sleep, wait
from types import TracebackType
from typing import Optional, Type

from rich.console import Console

from brood.command import Event
from brood.config import BroodConfig
from brood.fanout import Fanout
from brood.message import InternalMessage, Message
from brood.monitor import KillOthers, Monitor
from brood.renderer import RENDERERS


class Executor:
    def __init__(self, config: BroodConfig, console: Console):
        self.config = config
        self.console = console

        self.events: Fanout[Event] = Fanout()
        self.messages: Fanout[Message] = Fanout()

        self.renderer = RENDERERS[config.renderer.type](
            config=self.config.renderer,
            console=self.console,
            events=self.events.consumer(),
            messages=self.messages.consumer(),
        )

        self.monitor = Monitor(
            config=self.config,
            events=self.events,
            messages=self.messages,
            events_consumer=self.events.consumer(),
            widths={
                **{
                    config: self.renderer.available_process_width(config)
                    for config in self.config.commands
                },
                **{
                    config.shutdown_config: self.renderer.available_process_width(
                        config.shutdown_config
                    )
                    for config in self.config.commands
                    if config.shutdown_config is not None
                },
            },
        )

    async def run(self) -> None:
        done, pending = await wait(
            (
                self.monitor.run(),
                self.renderer.mount(),
                self.renderer.run(),
            ),
            return_when=FIRST_EXCEPTION,
        )

        for d in done:
            d.result()

    async def __aenter__(self) -> Executor:
        return self

    async def __aexit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> Optional[bool]:
        if exc_type:
            if exc_type is CancelledError:
                text = f"Shutting down due to: keyboard interrupt"
            elif exc_type is KillOthers:
                text = f"Shutting down due to: command failing"
            else:
                text = f"Shutting down due to: {exc_type.__name__}"
            await self.messages.put(InternalMessage(text))

        # Stop the monitor while repeatedly draining the renderer,
        # so that we can emit output during shutdown.
        stop_monitor = create_task(self.monitor.stop())
        drain_renderer = create_task(self.renderer.run(drain=True))
        while True:
            done, pending = await wait((stop_monitor, drain_renderer), return_when=FIRST_COMPLETED)

            if stop_monitor in done:
                await drain_renderer
                break
            else:
                await sleep(0.001)
                drain_renderer = create_task(self.renderer.run(drain=True))

        await self.renderer.unmount()

        return True
