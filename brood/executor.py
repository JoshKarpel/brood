from __future__ import annotations

import traceback
from asyncio import FIRST_COMPLETED, FIRST_EXCEPTION, CancelledError, create_task, sleep, wait
from types import TracebackType
from typing import Optional, Type

from rich.console import Console

from brood.config import BroodConfig
from brood.event import Event
from brood.fanout import Fanout
from brood.message import InternalMessage, Message, Verbosity
from brood.monitor import KillOthers, Monitor
from brood.renderer import RENDERERS


class Executor:
    def __init__(self, config: BroodConfig, console: Console, verbosity: Verbosity):
        self.config = config
        self.console = console
        self.verbosity = verbosity

        self.events: Fanout[Event] = Fanout()
        self.messages: Fanout[Message] = Fanout()

        self.renderer = RENDERERS[config.renderer.type](
            config=self.config.renderer,
            console=self.console,
            verbosity=self.verbosity,
            events=self.events.consumer(),
            messages=self.messages.consumer(),
        )

        self.monitor = Monitor(
            config=self.config,
            events=self.events,
            messages=self.messages,
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
                create_task(self.monitor.run(), name=f"Run {type(self.monitor).__name__}"),
                create_task(self.renderer.mount(), name=f"Mount {type(self.renderer).__name__}"),
                create_task(self.renderer.run(), name=f"Run {type(self.renderer).__name__}"),
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
                await self.messages.put(
                    InternalMessage(
                        f"Shutting down due to: keyboard interrupt", verbosity=Verbosity.INFO
                    )
                )
            elif exc_type is KillOthers:
                await self.messages.put(
                    InternalMessage(
                        f"Shutting down due to: command failing", verbosity=Verbosity.INFO
                    )
                )
            else:
                await self.messages.put(
                    InternalMessage(
                        f"Shutting down due to internal error.\n{traceback.format_exc()}",
                        verbosity=Verbosity.ERROR,
                    )
                )

        # Stop the monitor while repeatedly draining the renderer,
        # so that we can emit output during shutdown.
        stop_monitor = create_task(self.monitor.stop(), name=f"Stop {type(self.monitor).__name__}")
        drain_renderer = create_task(
            self.renderer.run(drain=True), name=f"Drain {type(self.renderer)}"
        )
        while True:
            done, pending = await wait((stop_monitor, drain_renderer), return_when=FIRST_COMPLETED)
            if stop_monitor in done:
                await drain_renderer
                break
            else:
                await sleep(0.001)
                drain_renderer = create_task(
                    self.renderer.run(drain=True), name=f"Drain {type(self.renderer).__name__}"
                )

        await create_task(self.renderer.unmount(), name=f"Unmount {type(self.renderer).__name__}")

        return True
