from __future__ import annotations

import os
import shlex
from asyncio import Queue, create_subprocess_shell, create_task, sleep
from asyncio.subprocess import PIPE, Process
from dataclasses import dataclass
from functools import cached_property
from typing import List, Literal, Optional, Union

from pydantic import BaseModel, Field, PositiveFloat

from brood.message import Message


class RestartConfig(BaseModel):
    type: Literal["restart"] = "restart"

    restart_on_exit: bool = True
    delay: PositiveFloat = 5


class WatchConfig(BaseModel):
    type: Literal["watch"] = "watch"

    paths: List[str] = Field(default_factory=list)
    poll: bool = False

    allow_multiple: bool = False


class OnceConfig(BaseModel):
    type: Literal["once"] = "once"


class CommandConfig(BaseModel):
    command: Union[str, List[str]]
    shutdown: Optional[Union[str, List[str]]]

    tag: str = ""

    prefix: Optional[str] = None
    prefix_style: Optional[str] = None
    message_style: Optional[str] = None

    starter: Union[RestartConfig, WatchConfig] = RestartConfig()

    @property
    def command_string(self) -> str:
        return normalize_command(self.command)

    @property
    def shutdown_string(self) -> Optional[str]:
        if self.shutdown is None:
            return None

        return normalize_command(self.shutdown)


def normalize_command(command: Union[str, List[str]]) -> str:
    if isinstance(command, list):
        return shlex.join(command)
    else:
        return command


@dataclass
class CommandManager:
    command_config: CommandConfig
    process_messages: Queue
    internal_messages: Queue
    width: int
    process: Process

    was_killed: bool = False

    @classmethod
    async def start(
        cls,
        command_config: CommandConfig,
        process_messages: Queue,
        internal_messages: Queue,
        width: int,
        restart: bool,
    ) -> CommandManager:
        if restart and command_config.starter.type == "restart":
            await sleep(command_config.starter.delay)

        await internal_messages.put(Message(f"Started command: {command_config.command_string!r}"))

        process = await create_subprocess_shell(
            command_config.command_string,
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

    @property
    def exit_code(self) -> Optional[int]:
        return self.process.returncode

    @property
    def has_exited(self) -> bool:
        return self.exit_code is not None

    async def stop(self) -> None:
        if self.has_exited:
            return None

        self.was_killed = True

        await self.internal_messages.put(
            Message(f"Terminating command: {self.command_config.command_string!r}")
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
