from __future__ import annotations

from abc import ABCMeta, abstractmethod
from asyncio import ALL_COMPLETED, FIRST_EXCEPTION, Queue, wait
from dataclasses import dataclass
from shutil import get_terminal_size
from typing import Literal

from pydantic import BaseModel
from rich.console import Console
from rich.text import Text

from brood.command import CommandConfig
from brood.message import Message


class RendererConfig(BaseModel):
    pass


@dataclass
class Renderer(metaclass=ABCMeta):
    config: RendererConfig
    console: Console

    process_messages: Queue
    internal_messages: Queue

    @abstractmethod
    def available_process_width(self, command: CommandConfig) -> int:
        raise NotImplementedError

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
    config: LogRendererConfig

    def available_process_width(self, command: CommandConfig) -> int:
        text = self.render_process_message(command, Message(""))
        return get_terminal_size().columns - text.cell_len

    async def handle_internal_message(self, message: Message) -> None:
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
        text = self.render_process_message(command, message)

        self.console.print(text, soft_wrap=True)

    def render_process_message(self, command: CommandConfig, message: Message) -> Text:
        return (
            Text("")
            .append_text(
                Text.from_markup(
                    (command.prefix or self.config.prefix).format_map(
                        {
                            "tag": command.tag,
                            "timestamp": message.timestamp,
                        }
                    ),
                    style=command.prefix_style or self.config.prefix_style,
                )
            )
            .append_text(
                Text(
                    message.text,
                    style=command.message_style or self.config.message_style,
                )
            )
        )


class LogRendererConfig(RendererConfig):
    type: Literal["log"] = "log"

    prefix: str = "{timestamp} {tag} "

    prefix_style: str = ""
    message_style: str = ""

    internal_prefix: str = "{timestamp} "
    internal_prefix_style: str = "dim"
    internal_message_style: str = "dim"


RENDERERS = {"log": LogRenderer}
