from __future__ import annotations

from asyncio import FIRST_COMPLETED, FIRST_EXCEPTION, Queue, QueueEmpty, gather, sleep, wait
from dataclasses import dataclass, field
from types import TracebackType
from typing import AsyncContextManager, List, Optional, Tuple, Type, TypeVar

from rich.console import Console

from brood.command import CommandManager, ProcessEvent
from brood.config import BroodConfig, CommandConfig, FailureMode, OnceConfig
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

    process_messages: Queue[Tuple[CommandConfig, Message]] = field(default_factory=Queue)
    internal_messages: Queue[Message] = field(default_factory=Queue)
    process_events: Queue[ProcessEvent] = field(default_factory=Queue)

    def __post_init__(self) -> None:
        self.renderer = RENDERERS[self.config.renderer.type](
            config=self.config.renderer,
            console=self.console,
            process_messages=self.process_messages,
            internal_messages=self.internal_messages,
            process_events=self.process_events,
        )

    async def start(self, command_config: CommandConfig, delay: bool = False) -> CommandManager:
        manager = await CommandManager.start(
            command_config=command_config,
            process_messages=self.process_messages,
            internal_messages=self.internal_messages,
            process_events=self.process_events,
            width=self.renderer.available_process_width(command_config),
            delay=delay,
        )
        self.managers.append(manager)
        return manager

    async def run(self) -> None:
        done, pending = await wait(
            (
                self.handle_managers(),
                self.handle_watchers(),
                self.renderer.mount(),
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
                        f"Killing other processes due to command failing with code {manager.exit_code}: {manager.command_config.command_string!r}"
                    )
                )

    async def handle_managers(self) -> None:
        await gather(*(self.start(command) for command in self.config.commands))

        while True:
            if not self.managers:
                await sleep(0.1)
                continue

            done, pending = await wait(
                [manager.wait() for manager in self.managers],
                return_when=FIRST_COMPLETED,
            )

            for task in done:
                manager: CommandManager = task.result()

                self.managers.remove(manager)

                await self.internal_messages.put(
                    Message(
                        f"Command exited with code {manager.exit_code}: {manager.command_config.command_string!r}"
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
                            delay=True,
                        )

    async def handle_watchers(self) -> None:
        queue: Queue = Queue()

        for config in self.config.commands:
            if config.starter.type == "watch":
                handler = StartCommand(config, queue)
                watcher = FileWatcher(config.starter, handler)
                watcher.start()
                self.watchers.append(watcher)

        while True:
            # unique-ify on configs
            starts = {}
            stops = set()
            for config, event in await drain_queue(queue):
                starts[config] = event

                if config.starter.type == "watch" and not config.starter.allow_multiple:
                    for manager in self.managers:
                        if manager.command_config is config:
                            stops.add(manager)

                queue.task_done()

            await gather(*(stop.stop() for stop in stops))

            await gather(
                *(
                    self.internal_messages.put(
                        Message(
                            f"File {event.src_path} was {event.event_type}, starting command: {config.command_string!r}"
                        )
                    )
                    for config, event in starts.items()
                )
            )

            await gather(*(self.start(command_config=config) for config in starts))

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
        await self.shutdown()
        await self.renderer.run(drain=True)
        return None

    async def stop(self) -> None:
        await gather(*(manager.stop() for manager in self.managers))

        for watcher in self.watchers:
            watcher.stop()

    async def wait(self) -> None:
        managers = await gather(*(manager.wait() for manager in self.managers))

        for manager in managers:
            self.managers.remove(manager)
            await self.internal_messages.put(
                Message(
                    f"Command exited with code {manager.exit_code}: {manager.command_config.command_string!r}"
                )
            )

        for watcher in self.watchers:
            watcher.join()

    async def shutdown(self) -> None:
        shutdown_commands = [
            command.copy(update={"command": command.shutdown, "starter": OnceConfig()})
            for command in self.config.commands
            if command.shutdown
        ]

        await gather(*(self.start(command) for command in shutdown_commands))
        await self.wait()


T = TypeVar("T")


async def drain_queue(queue: Queue[T], buffer: Optional[float] = 1) -> List[T]:
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

    return items
