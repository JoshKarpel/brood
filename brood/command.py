from __future__ import annotations

import os
from asyncio import Queue, create_subprocess_shell, create_task
from asyncio.subprocess import PIPE, Process
from dataclasses import dataclass
from typing import Optional

from pydantic import BaseModel, Field

from brood.message import Message


class CommandConfig(BaseModel):
    command: str

    tag: str = ""

    prefix: Optional[str] = None
    prefix_style: Optional[str] = None
    message_style: Optional[str] = None

    restart_on_exit: bool = True
    restart_delay: int = 5


@dataclass(frozen=True)
class CommandManager:
    command_config: CommandConfig
    width: int
    process: Process
    process_messages: Queue
    internal_messages: Queue

    @classmethod
    async def start(
        cls,
        command_config: CommandConfig,
        width: int,
        process_messages: Queue,
        internal_messages: Queue,
    ) -> CommandManager:
        process = await create_subprocess_shell(
            command_config.command,
            stdout=PIPE,
            stderr=PIPE,
            shell=True,
            env={**os.environ, "FORCE_COLOR": "true", "COLUMNS": str(width)},
        )

        return cls(
            command_config=command_config,
            width=width,
            process=process,
            process_messages=process_messages,
            internal_messages=internal_messages,
        )

    def __post_init__(self) -> None:
        create_task(self.read())

        self.internal_messages.put_nowait(
            Message(f"Started command: {self.command_config.command!r}")
        )

    @property
    def exit_code(self) -> Optional[int]:
        return self.process.returncode

    @property
    def has_exited(self) -> bool:
        return self.exit_code is not None

    async def stop(self) -> None:
        if self.has_exited:
            return None

        await self.internal_messages.put(
            Message(f"Terminating command: {self.command_config.command!r}")
        )

        self.process.terminate()

    async def wait(self) -> CommandManager:
        await self.process.wait()
        return self

    async def read(self) -> None:
        if self.process.stdout is None:
            raise Exception(f"{self.process} does not have an associated stream reader")

        while True:
            line = await self.process.stdout.readline()
            if not line:
                return

            await self.process_messages.put(
                (self.command_config, Message(line.decode("utf-8").rstrip()))
            )
