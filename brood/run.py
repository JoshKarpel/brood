from __future__ import annotations

import os
from asyncio import (
    ALL_COMPLETED,
    FIRST_COMPLETED,
    FIRST_EXCEPTION,
    Queue,
    create_task,
    gather,
    sleep,
    wait,
)
from asyncio.subprocess import PIPE, Process, create_subprocess_shell
from dataclasses import dataclass, field
from datetime import datetime
from shutil import get_terminal_size
from types import TracebackType
from typing import Dict, List, Optional, Type

from rich.console import Console
from rich.text import Text

from brood.config import CommandConfig, Config, FailureMode


class KillOthers(Exception):
    pass


@dataclass(frozen=True)
class Message:
    text: str
    timestamp: datetime = field(default_factory=datetime.now)


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


@dataclass(frozen=True)
class Monitor:
    config: Config
    renderer: Renderer

    managers: List[CommandManager] = field(default_factory=list)

    process_messages: Queue = field(default_factory=Queue)
    internal_messages: Queue = field(default_factory=Queue)

    async def start(self, command_config: CommandConfig, delay: int = 0) -> None:
        await sleep(delay)

        self.managers.append(
            await CommandManager.start(
                command_config=command_config,
                width=self.available_width(command_config),
                process_messages=self.process_messages,
                internal_messages=self.internal_messages,
            )
        )

    def available_width(self, command: CommandConfig) -> int:
        example_prefix = Text.from_markup(
            command.prefix.format_map({"tag": command.tag, "timestamp": datetime.now()}),
            style=command.prefix_style,
        )
        prefix_len = example_prefix.cell_len
        term_width = get_terminal_size().columns
        available = term_width - prefix_len
        return available

    async def run(self) -> None:
        self.renderer.internal_messages = self.internal_messages
        self.renderer.process_messages = self.process_messages

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


@dataclass
class Renderer:
    config: Config
    console: Console

    process_messages: Queue = field(default_factory=Queue)
    internal_messages: Queue = field(default_factory=Queue)

    async def run(self, drain: bool = False) -> None:
        done, pending = await wait(
            (
                self.handle_internal_messages(drain=drain),
                self.handle_process_messages(drain=drain),
            ),
            return_when=ALL_COMPLETED if drain else FIRST_EXCEPTION,
        )

        for d in done:
            d.result()

    async def handle_internal_messages(self, drain: bool = False) -> None:
        while not drain or not self.internal_messages.empty():
            message = await self.internal_messages.get()
            await self.handle_internal_message(message)
            self.internal_messages.task_done()

    async def handle_internal_message(self, message: Message) -> None:
        pass

    async def handle_process_messages(self, drain: bool = False) -> None:
        while not drain or not self.process_messages.empty():
            command, message = await self.process_messages.get()
            await self.handle_process_message(command, message)
            self.process_messages.task_done()

    async def handle_process_message(self, process: CommandConfig, message: Message) -> None:
        pass


@dataclass
class LogRenderer(Renderer):
    async def handle_internal_message(self, message: Message) -> None:
        if self.config.verbose:
            text = (
                Text("")
                .append_text(
                    Text.from_markup(
                        self.config.internal_prefix.format_map({"timestamp": message.timestamp}),
                        style=self.config.internal_prefix_style,
                    )
                )
                .append(
                    message.text,
                    style=self.config.internal_message_style,
                )
            )

            self.console.print(text, soft_wrap=True)

    async def handle_process_message(self, command: CommandConfig, message: Message) -> None:
        text = (
            Text("")
            .append_text(
                Text.from_markup(
                    command.prefix.format_map(
                        {
                            "tag": command.tag,
                            "timestamp": message.timestamp,
                        }
                    ),
                    style=command.prefix_style,
                )
            )
            .append_text(
                Text(
                    message.text,
                    style=command.message_style,
                )
            )
        )

        self.console.print(text, soft_wrap=True)
