from __future__ import annotations

import os
from asyncio import FIRST_EXCEPTION, Queue, create_task, gather, sleep, wait
from asyncio.subprocess import PIPE, Process, create_subprocess_shell
from dataclasses import dataclass, field
from datetime import datetime
from shutil import get_terminal_size
from types import TracebackType
from typing import Dict, Optional, Type

from rich.console import Console
from rich.text import Text

from brood.config import Command, Config, FailureMode


class KillOthers(Exception):
    pass


@dataclass(frozen=True)
class Message:
    text: str
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass(frozen=True)
class CommandManager:
    command: Command
    width: int
    process: Process
    process_messages: Queue
    internal_messages: Queue

    @classmethod
    async def start(
        cls, command: Command, width: int, process_messages: Queue, internal_messages: Queue
    ) -> CommandManager:
        process = await create_subprocess_shell(
            command.command,
            stdout=PIPE,
            stderr=PIPE,
            shell=True,
            env={**os.environ, "FORCE_COLOR": "true", "COLUMNS": str(width)},
        )

        return cls(
            command=command,
            width=width,
            process=process,
            process_messages=process_messages,
            internal_messages=internal_messages,
        )

    def __post_init__(self) -> None:
        create_task(self.read())

        self.internal_messages.put_nowait(Message(f"Started command: {self.command.command!r}"))

    @property
    def has_exited(self) -> bool:
        return self.process.returncode is not None

    async def stop(self) -> None:
        if self.has_exited:
            return None

        await self.internal_messages.put(Message(f"Terminating command: {self.command.command!r}"))

        self.process.terminate()

    async def wait(self) -> None:
        if self.has_exited:
            return None

        await self.internal_messages.put(
            Message(f"Waiting for command to exit: {self.command.command!r}")
        )

        code = await self.process.wait()

        await self.internal_messages.put(
            Message(f"Command exited with code {code}: {self.command.command!r}")
        )

    async def read(self) -> None:
        if self.process.stdout is None:
            raise Exception(f"{self.process} does not have an associated stream reader")

        while True:
            line = await self.process.stdout.readline()
            if not line:
                return

            await self.process_messages.put((self.command, Message(line.decode("utf-8").rstrip())))


@dataclass(frozen=True)
class Monitor:
    config: Config
    console: Console
    managers: Dict[int, CommandManager] = field(default_factory=dict)
    process_messages: Queue = field(default_factory=Queue)
    internal_messages: Queue = field(default_factory=Queue)

    async def start(self, command: Command, delay: int = 0) -> None:
        await sleep(delay)

        previous_manager = self.managers.get(id(command), None)
        if previous_manager is not None:
            await previous_manager.stop()

        self.managers[id(command)] = await CommandManager.start(
            command=command,
            width=self.available_width(command),
            process_messages=self.process_messages,
            internal_messages=self.internal_messages,
        )

    def available_width(self, command: Command) -> int:
        example_prefix = Text.from_markup(
            command.prefix.format_map({"tag": command.tag, "timestamp": datetime.now()}),
            style=command.prefix_style,
        )
        prefix_len = example_prefix.cell_len
        term_width = get_terminal_size().columns
        available = term_width - prefix_len
        return available

    async def run(self) -> None:
        done, pending = await wait(
            (
                self.handle_managers(),
                self.handle_process_messages(),
                self.handle_internal_messages(),
            ),
            return_when=FIRST_EXCEPTION,
        )

        for d in done:
            try:
                d.result()
            except KillOthers as e:
                await self.internal_messages.put(
                    Message(
                        f"Killing other processes due to command failing: {e.args[0].command!r}"
                    )
                )

    async def handle_managers(self) -> None:
        await gather(*(self.start(command) for command in self.config.commands))

        while True:
            for manager in list(self.managers.values()):
                if manager.has_exited:
                    if self.config.failure_mode is FailureMode.KILL_OTHERS:
                        raise KillOthers(manager.command)

                    if manager.command.restart_on_exit:
                        create_task(
                            self.start(manager.command, delay=manager.command.restart_delay)
                        )
            await sleep(1)

    async def handle_internal_messages(self, drain: bool = False) -> None:
        while not drain or not self.internal_messages.empty():
            message = await self.internal_messages.get()
            self.render_internal_message(message)
            self.internal_messages.task_done()

    def render_internal_message(self, message: Message) -> None:
        pass

    async def handle_process_messages(self, drain: bool = False) -> None:
        while not drain or not self.process_messages.empty():
            command, message = await self.process_messages.get()
            self.render_process_message(command, message)
            self.process_messages.task_done()

    def render_process_message(self, process: Command, message: Message) -> None:
        pass

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
        await gather(
            self.handle_internal_messages(drain=True), self.handle_process_messages(drain=True)
        )
        return None

    async def stop(self) -> None:
        await gather(*(manager.stop() for manager in self.managers.values()))

    async def wait(self) -> None:
        await gather(*(manager.wait() for manager in self.managers.values()))


class LoggingMonitor(Monitor):
    def render_internal_message(self, message: Message) -> None:
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

    def render_process_message(self, command: Command, message: Message) -> None:
        format_params = {
            "tag": command.tag,
            "timestamp": message.timestamp,
        }

        text = (
            Text("")
            .append_text(
                Text.from_markup(
                    command.prefix.format_map(format_params),
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
