from __future__ import annotations

import os
from asyncio import FIRST_COMPLETED, create_task, gather, wait
from asyncio.subprocess import PIPE, Process, create_subprocess_shell
from dataclasses import dataclass
from datetime import datetime
from shutil import get_terminal_size
from typing import Optional, Tuple

from rich.console import Console
from rich.text import Text

from brood.config import Command, Config


@dataclass(frozen=True)
class ProcessLine:
    text: str
    timestamp: datetime
    command: Command


class CommandManager:
    def __init__(self, command: Command, width: int):
        self.command = command
        self.width = width

        self.process: Optional[Process] = None

    async def start(self) -> None:
        if self.process is None:
            self.process = await create_subprocess_shell(
                self.command.command,
                stdout=PIPE,
                stderr=PIPE,
                shell=True,
                env={**os.environ, "FORCE_COLOR": "true", "COLUMNS": str(self.width)},
            )

    def stop(self) -> None:
        if self.process is not None:
            self.process.kill()

    async def wait(self) -> None:
        if self.process is None:
            return None

        await self.process.wait()

    async def readline(self) -> Tuple[CommandManager, ProcessLine]:
        if self.process is None:
            raise Exception(f"{self} has not been started yet.")

        text = (await self.process.stdout.readline()).decode("utf-8").rstrip()
        return self, ProcessLine(text=text, timestamp=datetime.now(), command=self.command)


class Coordinator:
    def __init__(self, config: Config, console: Console):
        self.config = config
        self.console = console

        self.managers = [
            CommandManager(command, width=self.available_width(command))
            for command in config.commands
        ]

    async def start(self) -> None:
        await gather(*(manager.start() for manager in self.managers))

    async def stop(self) -> None:
        for manager in self.managers:
            manager.stop()

        await gather(*(manager.wait() for manager in self.managers))

    def available_width(self, command: Command) -> int:
        example_prefix = Text.from_markup(
            command.prefix.format_map({"tag": command.tag, "timestamp": datetime.now()}),
            style=command.prefix_style,
        )
        prefix_len = example_prefix.cell_len
        term_width = get_terminal_size().columns
        available = term_width - prefix_len
        return available

    def render_line(self, line: ProcessLine) -> None:
        format_params = {
            "tag": line.command.tag,
            "timestamp": line.timestamp,
        }

        text = (
            Text("")
            .append_text(
                Text.from_markup(
                    line.command.prefix.format_map(format_params),
                    style=line.command.prefix_style,
                )
            )
            .append_text(
                Text(
                    line.text,
                    style=line.command.line_style,
                )
            )
        )

        self.console.print(text, soft_wrap=True)

    async def wait(self) -> None:
        pending = {create_task(manager.readline()) for manager in self.managers}

        while True:
            done, pending = await wait(pending, return_when=FIRST_COMPLETED)

            for task in done:
                manager, line = task.result()
                self.render_line(line)
                pending.add(create_task(manager.readline()))

    async def __aenter__(self) -> Coordinator:
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.stop()
