from __future__ import annotations

from asyncio import FIRST_EXCEPTION, Queue, create_task, gather, get_running_loop, wait
from dataclasses import dataclass, field
from functools import partial
from typing import Dict, List, Mapping, Set

from brood.command import Command
from brood.config import (
    AfterConfig,
    BroodConfig,
    CommandConfig,
    FailureMode,
    RestartConfig,
    Starter,
    WatchConfig,
)
from brood.event import Event, EventType
from brood.fanout import Fanout
from brood.message import InternalMessage, Message, Verbosity
from brood.utils import delay, drain_queue
from brood.watch import FileWatcher, StartCommandHandler, WatchEvent


class KillOthers(Exception):
    pass


@dataclass
class Monitor:
    config: BroodConfig

    events: Fanout[Event]
    messages: Fanout[Message]

    widths: Mapping[CommandConfig, int]

    commands: Set[Command] = field(default_factory=set)
    watchers: List[FileWatcher] = field(default_factory=list)

    starters: Dict[CommandConfig, Starter] = field(init=False)
    events_consumer: Queue[Event] = field(init=False)

    def __post_init__(self) -> None:
        self.starters = {
            command_config: command_config.starter.starter()
            for command_config in self.config.commands
        }
        self.events_consumer = self.events.consumer()

    async def start_commands(self) -> None:
        await gather(
            *(
                self.start_command(command)
                for command in self.config.commands
                if not isinstance(command.starter, AfterConfig)
            )
        )

    async def start_command(self, command_config: CommandConfig) -> None:
        await Command.start(
            config=command_config,
            events=self.events,
            messages=self.messages,
            width=self.widths[command_config],
        )

    async def run(self) -> None:
        await self.start_commands()

        done, pending = await wait(
            (
                create_task(self.handle_events(), name=f"{type(self).__name__} event handler"),
                create_task(
                    self.handle_file_events(), name=f"{type(self).__name__} file event handler"
                ),
            ),
            return_when=FIRST_EXCEPTION,
        )

        for d in done:
            d.result()

    async def handle_events(self, drain: bool = False) -> None:
        while True:
            if drain and len(self.commands) == 0 and self.events_consumer.qsize() == 0:
                return

            event = await self.events_consumer.get()

            await self.messages.put(
                InternalMessage(
                    f"Got event for command '{event.command.config.name}' of type {event.type}",
                    verbosity=Verbosity.DEBUG,
                )
            )

            if event.type is EventType.Started:
                self.commands.add(event.command)
            elif event.type is EventType.Stopped:
                try:
                    self.commands.remove(event.command)
                except KeyError:
                    return  # it's ok to get multiple stop events for the same manager, e.g., during shutdown

                await self.messages.put(
                    InternalMessage(
                        f"Command exited with code {event.command.exit_code}: {event.command.config.command_string!r}",
                        verbosity=Verbosity.INFO,
                    )
                )

                if (
                    self.config.failure_mode == FailureMode.KILL_OTHERS
                    and event.command.exit_code != 0
                    and not event.command.was_killed
                ):
                    raise KillOthers(event.command)

            for command_config, starter in self.starters.items():
                starter.handle_event(event)

                can_start = starter.can_start()
                not_already_running = not any(
                    command.config is command_config for command in self.commands
                )
                if can_start and not_already_running:
                    await self.messages.put(
                        InternalMessage(
                            f"Command {command_config.name!r} is ready to start via {command_config.starter}",
                            verbosity=Verbosity.DEBUG,
                        )
                    )
                    starter.was_started()
                    delay(
                        command_config.starter.delay
                        if isinstance(command_config.starter, RestartConfig)
                        else 0,
                        partial(
                            self.start_command,
                            command_config=command_config,
                        ),
                    )
                else:
                    await self.messages.put(
                        InternalMessage(
                            f"Command {command_config.name!r} is not ready to start: {type(starter).__name__}.can_start() = {starter.can_start()} && not_already_running = {not_already_running})",
                            verbosity=Verbosity.DEBUG,
                        )
                    )

            self.events_consumer.task_done()

    async def handle_file_events(self) -> None:
        watch_events: Queue[WatchEvent] = Queue()

        for config in self.config.commands:
            if isinstance(config.starter, WatchConfig):
                handler = StartCommandHandler(get_running_loop(), config, watch_events)
                watcher = FileWatcher(config.starter, handler)
                watcher.start()
                self.watchers.append(watcher)

        if not self.watchers:
            return

        while True:
            # unique-ify on configs
            starts = {}
            stops = set()
            for watch_event in await drain_queue(watch_events, buffer=1):
                starts[watch_event.command_config] = watch_event.event

                if isinstance(watch_event.command_config.starter, WatchConfig):
                    for manager in self.commands:
                        if manager.config is watch_event.command_config:
                            stops.add(manager)

                watch_events.task_done()

            await gather(*(stop.terminate() for stop in stops))

            await gather(
                *(
                    self.messages.put(
                        InternalMessage(
                            f"Path {event.src_path} was {event.event_type}, starting command: {config.command_string!r}",
                            verbosity=Verbosity.INFO,
                        )
                    )
                    for config, event in starts.items()
                )
            )

            await gather(*(self.start_command(command_config=config) for config in starts))

    async def stop(self) -> None:
        await self.terminate()
        await self.wait()
        await self.shutdown()
        await self.wait()

    async def wait(self) -> None:
        await gather(*(manager.wait() for manager in self.commands))

        await self.handle_events(drain=True)

        for watcher in self.watchers:
            watcher.join()

    async def terminate(self) -> None:
        await gather(*(manager.terminate() for manager in self.commands))

        for watcher in self.watchers:
            watcher.stop()

    async def shutdown(self) -> None:
        shutdown_configs = [command.shutdown_config for command in self.config.commands]

        await gather(*(self.start_command(config) for config in shutdown_configs if config))
