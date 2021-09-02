from __future__ import annotations

import os
from asyncio import Queue, create_subprocess_shell, create_task, sleep
from asyncio.subprocess import PIPE, STDOUT, Process
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

from brood.config import CommandConfig
from brood.message import Message


class EventType(Enum):
    Started = "started"
    Stopped = "stopped"


@dataclass(frozen=True)
class ProcessEvent:
    manager: CommandManager
    type: EventType


@dataclass
class CommandManager:
    command_config: CommandConfig

    process_messages: Queue[Tuple[CommandConfig, Message]]
    internal_messages: Queue[Message]
    process_events: Queue[ProcessEvent]

    width: int
    process: Process

    was_killed: bool = False

    @classmethod
    async def start(
        cls,
        command_config: CommandConfig,
        process_messages: Queue[Tuple[CommandConfig, Message]],
        internal_messages: Queue[Message],
        process_events: Queue[ProcessEvent],
        width: int,
        delay: bool,
    ) -> CommandManager:
        if delay:
            await sleep(command_config.starter.delay)

        await internal_messages.put(Message(f"Started command: {command_config.command_string!r}"))

        process = await create_subprocess_shell(
            command_config.command_string,
            stdout=PIPE,
            stderr=STDOUT,
            env={**os.environ, "FORCE_COLOR": "true", "COLUMNS": str(width)},
        )

        manager = cls(
            command_config=command_config,
            width=width,
            process=process,
            process_messages=process_messages,
            internal_messages=internal_messages,
            process_events=process_events,
        )

        await process_events.put(ProcessEvent(manager=manager, type=EventType.Started))

        return manager

    def __post_init__(self) -> None:
        create_task(self.read_output())

    @property
    def exit_code(self) -> Optional[int]:
        return self.process.returncode

    @property
    def has_exited(self) -> bool:
        return self.exit_code is not None

    async def terminate(self) -> None:
        if self.has_exited:
            return None

        self.was_killed = True

        await self.internal_messages.put(
            Message(f"Stopping command: {self.command_config.command_string!r}")
        )

        self.process.terminate()

    async def kill(self) -> None:
        if self.has_exited:
            return None

        self.was_killed = True

        await self.internal_messages.put(
            Message(f"Killing command: {self.command_config.command_string!r}")
        )

        self.process.kill()

    async def wait(self) -> CommandManager:
        await self.process.wait()
        await self.process_events.put(ProcessEvent(manager=self, type=EventType.Stopped))
        return self

    async def read_output(self) -> None:
        if self.process.stdout is None:  # pragma: unreachable
            raise Exception(f"{self.process} does not have an associated stream reader")

        while True:
            line = await self.process.stdout.readline()
            if not line:
                break

            await self.process_messages.put(
                (self.command_config, Message(line.decode("utf-8").rstrip()))
            )

    def __hash__(self) -> int:
        return hash((self.__class__, self.command_config, self.process.pid))
