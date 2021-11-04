from __future__ import annotations

from asyncio import FIRST_EXCEPTION, Queue, create_task, gather, get_running_loop, wait
from dataclasses import dataclass, field
from functools import partial
from typing import List, Mapping, Set

from brood.command import Command, Event, EventType
from brood.config import BroodConfig, CommandConfig, FailureMode, RestartConfig, WatchConfig
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

    managers: Set[Command] = field(default_factory=set)
    watchers: List[FileWatcher] = field(default_factory=list)

    events_consumer: Queue[Event] = field(init=False)

    def __post_init__(self) -> None:
        self.events_consumer = self.events.consumer()

    async def start_commands(self) -> None:
        await gather(*(self.start_command(command) for command in self.config.commands))

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
            if drain and len(self.managers) == 0 and self.events_consumer.qsize() == 0:
                return

            event = await self.events_consumer.get()

            if event.type is EventType.Started:
                self.managers.add(event.manager)
            elif event.type is EventType.Stopped:
                try:
                    self.managers.remove(event.manager)
                except KeyError:
                    return  # it's ok to get multiple stop events for the same manager, e.g., during shutdown

                await self.messages.put(
                    InternalMessage(
                        f"Command exited with code {event.manager.exit_code}: {event.manager.config.command_string!r}",
                        verbosity=Verbosity.INFO,
                    )
                )

                if (
                    self.config.failure_mode == FailureMode.KILL_OTHERS
                    and event.manager.exit_code != 0
                    and not event.manager.was_killed
                ):
                    raise KillOthers(event.manager)

                if isinstance(event.manager.config.starter, RestartConfig):
                    delay(
                        event.manager.config.starter.delay,
                        partial(
                            self.start_command,
                            command_config=event.manager.config,
                        ),
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
                    for manager in self.managers:
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
        await gather(*(manager.wait() for manager in self.managers))

        await self.handle_events(drain=True)

        for watcher in self.watchers:
            watcher.join()

    async def terminate(self) -> None:
        await gather(*(manager.terminate() for manager in self.managers))

        for watcher in self.watchers:
            watcher.stop()

    async def shutdown(self) -> None:
        shutdown_configs = [command.shutdown_config for command in self.config.commands]

        await gather(*(self.start_command(config) for config in shutdown_configs if config))
