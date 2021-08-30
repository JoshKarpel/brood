from __future__ import annotations

from asyncio import FIRST_COMPLETED, FIRST_EXCEPTION, Queue, gather, wait
from dataclasses import dataclass, field
from types import TracebackType
from typing import AsyncContextManager, List, Optional, Type

from rich.console import Console

from brood.command import CommandConfig, CommandManager, RestartConfig
from brood.config import BroodConfig, FailureMode
from brood.message import Message
from brood.renderer import RENDERERS, Renderer
from brood.watch import FileWatcher, StartCommand


class KillOthers(Exception):
    pass


@dataclass
class Monitor(AsyncContextManager):
    config: BroodConfig

    console: Console
    renderer: Renderer = field(init=False)

    managers: List[CommandManager] = field(default_factory=list)
    watchers: List[FileWatcher] = field(default_factory=list)

    process_messages: Queue = field(default_factory=Queue)
    internal_messages: Queue = field(default_factory=Queue)

    def __post_init__(self) -> None:
        self.renderer = RENDERERS[self.config.renderer.type](
            self.config.renderer,
            console=self.console,
            process_messages=self.process_messages,
            internal_messages=self.internal_messages,
        )

    async def start(self, command_config: CommandConfig, restart: bool = False) -> None:
        self.managers.append(
            await CommandManager.start(
                command_config=command_config,
                process_messages=self.process_messages,
                internal_messages=self.internal_messages,
                width=self.renderer.available_process_width(command_config),
                restart=restart,
            )
        )

    async def run(self) -> None:
        done, pending = await wait(
            (
                self.handle_managers(),
                self.handle_watchers(),
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

                if (
                    self.config.failure_mode is FailureMode.KILL_OTHERS
                    and manager.exit_code != 0
                    and not manager.was_killed
                ):
                    raise KillOthers(manager)

                if manager.command_config.starter.type == "restart":
                    if manager.command_config.starter.restart_on_exit:
                        await self.start(
                            command_config=manager.command_config,
                            restart=True,
                        )

    async def handle_watchers(self) -> None:
        queue = Queue()
        for command in self.config.commands:
            if command.starter.type == "watch":
                handler = StartCommand(command, queue)
                watcher = FileWatcher(command.starter, handler)
                watcher.start()
                self.watchers.append(watcher)

        while True:
            command, event = await queue.get()

            if command.starter.type == "watch":
                if not command.starter.allow_multiple:
                    for manager in self.managers:
                        if manager.command_config is command:
                            await manager.stop()
                            await manager.wait()

            await self.internal_messages.put(
                Message(
                    f"File {event.src_path} was {event.event_type}, starting command: {command.command!r}"
                )
            )
            await self.start(command_config=command)

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
        for watcher in self.watchers:
            watcher.stop()

    async def wait(self) -> None:
        managers = await gather(*(manager.wait() for manager in self.managers))

        for manager in managers:
            await self.internal_messages.put(
                Message(
                    f"Command exited with code {manager.exit_code}: {manager.command_config.command!r}"
                )
            )

        for watcher in self.watchers:
            watcher.join()
