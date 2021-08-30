from __future__ import annotations

from asyncio import FIRST_COMPLETED, FIRST_EXCEPTION, Queue, gather, sleep, wait
from dataclasses import dataclass, field
from datetime import datetime
from shutil import get_terminal_size
from types import TracebackType
from typing import List, Optional, Type

from rich.console import Console
from rich.text import Text

from brood.command import CommandConfig, CommandManager
from brood.config import BroodConfig, FailureMode
from brood.message import Message
from brood.renderer import RENDERERS, Renderer


@dataclass
class Monitor:
    config: BroodConfig

    console: Console
    renderer: Renderer = field(init=False)

    managers: List[CommandManager] = field(default_factory=list)

    process_messages: Queue = field(default_factory=Queue)
    internal_messages: Queue = field(default_factory=Queue)

    def __post_init__(self) -> None:
        self.renderer = RENDERERS[self.config.renderer.type](
            self.config.renderer,
            console=self.console,
            process_messages=self.process_messages,
            internal_messages=self.internal_messages,
        )

    async def start(self, command_config: CommandConfig, delay: int = 0) -> None:
        await sleep(delay)

        self.managers.append(
            await CommandManager.start(
                command_config=command_config,
                width=self.renderer.available_process_width(command_config),
                process_messages=self.process_messages,
                internal_messages=self.internal_messages,
            )
        )

    async def run(self) -> None:
        done, pending = await wait(
            (
                self.handle_managers(),
                self.renderer.run(),
            ),
            return_when=FIRST_EXCEPTION,
        )

        for d in done:
            try:
                d.result()
            except KillOthers as e:
                manager = e.args[0]
                await self.internal_messages.put(
                    Message(
                        f"Killing other processes due to command failing with code {manager.exit_code}: {manager.command_config.command!r}"
                    )
                )

    async def handle_managers(self) -> None:
        await gather(*(self.start(command) for command in self.config.commands))

        while True:
            done, pending = await wait(
                [manager.wait() for manager in self.managers],
                return_when=FIRST_COMPLETED,
            )

            for task in done:
                manager: CommandManager = task.result()

                self.managers.remove(manager)

                await self.internal_messages.put(
                    Message(
                        f"Command exited with code {manager.exit_code}: {manager.command_config.command!r}"
                    )
                )

                if self.config.failure_mode is FailureMode.KILL_OTHERS and manager.exit_code != 0:
                    raise KillOthers(manager)

                if manager.command_config.restart_on_exit:
                    await self.start(
                        command_config=manager.command_config,
                        delay=manager.command_config.restart_delay,
                    )

    async def __aenter__(self) -> Monitor:
        return self

    async def __aexit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> Optional[bool]:
        await self.stop()
        await self.wait()
        await self.renderer.run(drain=True)
        return None

    async def stop(self) -> None:
        await gather(*(manager.stop() for manager in self.managers))

    async def wait(self) -> None:
        managers = await gather(*(manager.wait() for manager in self.managers))

        for manager in managers:
            await self.internal_messages.put(
                Message(
                    f"Command exited with code {manager.exit_code}: {manager.command_config.command!r}"
                )
            )


class KillOthers(Exception):
    pass
