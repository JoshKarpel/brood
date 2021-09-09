from __future__ import annotations

import os
from asyncio import CancelledError, Task, create_subprocess_shell, create_task
from asyncio.subprocess import PIPE, STDOUT, Process
from dataclasses import dataclass, field
from enum import Enum
from signal import SIGKILL, SIGTERM
from typing import Optional

from brood.config import CommandConfig
from brood.fanout import Fanout
from brood.message import CommandMessage, InternalMessage, Message


class EventType(Enum):
    Started = "started"
    Stopped = "stopped"


@dataclass(frozen=True)
class Event:
    manager: Command
    type: EventType


@dataclass
class Command:
    config: CommandConfig

    events: Fanout[Event] = field(repr=False)
    messages: Fanout[Message] = field(repr=False)

    process: Process = field(repr=False)

    width: int = 80

    was_killed: bool = False

    reader: Optional[Task[None]] = None

    @classmethod
    async def start(
        cls,
        config: CommandConfig,
        events: Fanout[Event],
        messages: Fanout[Message],
        width: int = 80,
    ) -> Command:
        await messages.put(InternalMessage(f"Starting command: {config.command_string!r}"))

        process = await create_subprocess_shell(
            config.command_string,
            stdout=PIPE,
            stderr=STDOUT,
            env={**os.environ, "FORCE_COLOR": "true", "COLUMNS": str(width)},
            preexec_fn=os.setsid,
        )

        manager = cls(
            config=config,
            width=width,
            process=process,
            events=events,
            messages=messages,
        )

        await events.put(Event(manager=manager, type=EventType.Started))

        return manager

    def __post_init__(self) -> None:
        self.reader = create_task(
            self.read_output(), name=f"Read output for {self.config.command_string!r}"
        )
        create_task(self.wait(), name=f"Wait for {self.config.command_string!r}")

    @property
    def exit_code(self) -> Optional[int]:
        return self.process.returncode

    @property
    def has_exited(self) -> bool:
        return self.exit_code is not None

    def _send_signal(self, signal: int) -> None:
        os.killpg(os.getpgid(self.process.pid), signal)

    async def terminate(self) -> None:
        if self.has_exited:
            return None

        self.was_killed = True

        await self.messages.put(
            InternalMessage(f"Terminating command: {self.config.command_string!r}")
        )

        self._send_signal(SIGTERM)

    async def kill(self) -> None:
        if self.has_exited:
            return None

        self.was_killed = True

        await self.messages.put(InternalMessage(f"Killing command: {self.config.command_string!r}"))

        self._send_signal(SIGKILL)

    async def wait(self) -> Command:
        await self.process.wait()

        if self.reader:
            try:
                await self.reader
            except CancelledError:
                pass

        await self.events.put(Event(manager=self, type=EventType.Stopped))

        return self

    async def read_output(self) -> None:
        if self.process.stdout is None:  # pragma: unreachable
            raise Exception(f"{self.process} does not have an associated stream reader")

        while True:
            line = await self.process.stdout.readline()
            if not line:
                break

            await self.messages.put(
                CommandMessage(
                    text=line.decode("utf-8").rstrip(),
                    command_config=self.config,
                )
            )

    def __hash__(self) -> int:
        return hash((self.__class__, self.config, self.process.pid))
