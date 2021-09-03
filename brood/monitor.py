from __future__ import annotations

from asyncio import FIRST_EXCEPTION, Queue, QueueEmpty, gather, get_running_loop, sleep, wait
from dataclasses import dataclass, field
from types import TracebackType
from typing import AsyncContextManager, List, Optional, Tuple, Type, TypeVar

from rich.console import Console
from watchdog.events import FileSystemEvent

from brood.command import CommandManager, EventType, InternalMessage, ProcessEvent, ProcessMessage
from brood.config import BroodConfig, CommandConfig, FailureMode, OnceConfig
from brood.fanout import Fanout
from brood.message import Message
from brood.renderer import RENDERERS, Renderer
from brood.watch import FileWatcher, StartCommandHandler


class KillOthers(Exception):
    pass


@dataclass
class Monitor(AsyncContextManager["Monitor"]):
    config: BroodConfig

    renderer: Renderer

    process_events: Fanout[ProcessEvent]
    internal_messages: Fanout[Message]
    process_messages: Fanout[Tuple[CommandConfig, Message]]

    managers: List[CommandManager] = field(default_factory=list)
    watchers: List[FileWatcher] = field(default_factory=list)

    @classmethod
    def create(cls, config: BroodConfig, console: Console) -> Monitor:
        renderer_type = RENDERERS[config.renderer.type]

        process_events: Fanout[ProcessEvent] = Fanout()
        internal_messages: Fanout[InternalMessage] = Fanout()
        process_messages: Fanout[ProcessMessage] = Fanout()

        renderer = renderer_type(
            config=config.renderer,
            console=console,
            process_events=process_events.consumer(),
            internal_messages=internal_messages.consumer(),
            process_messages=process_messages.consumer(),
        )

        return cls(
            config=config,
            renderer=renderer,
            process_events=process_events,
            internal_messages=internal_messages,
            process_messages=process_messages,
        )

    async def start(self) -> None:
        await gather(*(self.start_command(command) for command in self.config.commands))

    async def start_command(
        self, command_config: CommandConfig, delay: bool = False
    ) -> CommandManager:
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
        # We must create this consumer before we start the commands,
        # to make sure it doesn't miss any process events.
        process_events_consumer = self.process_events.consumer()

        await self.start()

        done, pending = await wait(
            (
                self.handle_managers(process_events_consumer),
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

    async def handle_managers(self, process_events: Queue[ProcessEvent]) -> None:
        while True:
            event = await process_events.get()

            if event.type is EventType.Stopped:
                self.managers.remove(event.manager)

                await self.internal_messages.put(
                    Message(
                        f"Command exited with code {event.manager.exit_code}: {event.manager.command_config.command_string!r}"
                    )
                )

                if (
                    self.config.failure_mode is FailureMode.KILL_OTHERS
                    and event.manager.exit_code != 0
                    and not event.manager.was_killed
                ):
                    raise KillOthers(event.manager)

                if event.manager.command_config.starter.type == "restart":
                    if event.manager.command_config.starter.restart_on_exit:
                        await self.start_command(
                            command_config=event.manager.command_config,
                            delay=True,
                        )

            process_events.task_done()

    async def handle_watchers(self) -> None:
        queue: Queue[Tuple[CommandConfig, FileSystemEvent]] = Queue()

        for config in self.config.commands:
            if config.starter.type == "watch":
                handler = StartCommandHandler(get_running_loop(), config, queue)
                watcher = FileWatcher(config.starter, handler)
                watcher.start()
                self.watchers.append(watcher)

        while True:
            # unique-ify on configs
            starts = {}
            stops = set()
            for config, event in await drain_queue(queue, buffer=1):
                starts[config] = event

                if config.starter.type == "watch" and not config.starter.allow_multiple:
                    for manager in self.managers:
                        if manager.command_config is config:
                            stops.add(manager)

                queue.task_done()

            await gather(*(stop.terminate() for stop in stops))

            await gather(
                *(
                    self.internal_messages.put(
                        Message(
                            f"Path {event.src_path} was {event.event_type}, starting command: {config.command_string!r}"
                        )
                    )
                    for config, event in starts.items()
                )
            )

            await gather(*(self.start_command(command_config=config) for config in starts))

    async def __aenter__(self) -> Monitor:
        return self

    async def __aexit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> Optional[bool]:
        await self.terminate()
        await self.wait()
        await self.shutdown()
        await self.renderer.run(drain=True)
        return None

    async def terminate(self) -> None:
        await gather(*(manager.terminate() for manager in self.managers))

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

        await gather(*(self.start_command(command) for command in shutdown_commands))
        await self.wait()


T = TypeVar("T")


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
