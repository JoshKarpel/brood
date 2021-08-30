from __future__ import annotations

import io
import pty
from asyncio import StreamReader, as_completed, gather
from asyncio.subprocess import Process, create_subprocess_shell
from dataclasses import dataclass
from datetime import datetime
from subprocess import PIPE
from typing import Optional

from rich.console import Console
from rich.text import Text

from brood.config import Command, Config


@dataclass(frozen=True)
class ProcessLine:
    text: str
    timestamp: datetime
    command: Command


class CommandManager:
    def __init__(self, command: Command):
        self.command = command

        self.process: Optional[Process] = None

    async def start(self) -> None:
        if self.process is None:
            self.process = await create_subprocess_shell(
                self.command.command,
                stdout=PIPE,
                stderr=PIPE,
                shell=True,
            )

    def stop(self) -> None:
        if self.process is not None:
            self.process.kill()

    async def readline(self) -> ProcessLine:
        text = (await self.process.stdout.readline()).decode("utf-8").rstrip()
        return ProcessLine(text=text, timestamp=datetime.now(), command=self.command)


class Coordinator:
    def __init__(self, config: Config, console: Console):
        self.config = config
        self.console = console

        self.managers = [CommandManager(command) for command in config.commands]

    async def start(self) -> None:
        await gather(*(manager.start() for manager in self.managers))

    async def stop(self) -> None:
        for manager in self.managers:
            manager.stop()

        await gather(*(manager.process.wait() for manager in self.managers))

    async def wait(self) -> None:
        while True:
            for l in as_completed([manager.readline() for manager in self.managers]):
                line = await l

                format_params = {
                    "tag": line.command.tag,
                    "timestamp": line.timestamp,
                }

                text = (
                    Text("")
                    .append_text(
                        Text.from_markup(
                            self.config.prefix.format_map(format_params),
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

                self.console.print(text)

    async def __aenter__(self) -> Coordinator:
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.stop()
