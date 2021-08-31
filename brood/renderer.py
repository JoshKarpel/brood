from __future__ import annotations

import math
import shutil
from asyncio import ALL_COMPLETED, FIRST_EXCEPTION, Queue, wait
from collections import defaultdict, deque
from dataclasses import dataclass, field
from shutil import get_terminal_size
from typing import DefaultDict, Deque, Literal, Mapping, Type

from rich.abc import RichRenderable
from rich.console import Console, Group
from rich.text import Text
from textual import events
from textual.app import App
from textual.widgets import Footer, Header, Placeholder, ScrollView

from brood.config import CommandConfig, LogRendererConfig, RendererConfig
from brood.message import Message


@dataclass(frozen=True)
class Renderer:
    config: RendererConfig
    console: Console

    process_messages: Queue
    internal_messages: Queue

    def available_process_width(self, command: CommandConfig) -> int:
        raise NotImplementedError

    async def mount(self) -> None:
        pass

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

    async def handle_process_messages(self, drain: bool = False) -> None:
        while not drain or not self.process_messages.empty():
            command, message = await self.process_messages.get()
            await self.handle_process_message(command, message)
            self.process_messages.task_done()

    async def handle_internal_message(self, message: Message) -> None:
        pass

    async def handle_process_message(self, process: CommandConfig, message: Message) -> None:
        pass


@dataclass(frozen=True)
class NullRenderer(Renderer):
    def available_process_width(self, command: CommandConfig) -> int:
        return shutil.get_terminal_size().columns


@dataclass(frozen=True)
class LogRenderer(Renderer):
    config: LogRendererConfig

    def available_process_width(self, command: CommandConfig) -> int:
        text = self.render_process_message(command, Message(""))
        return get_terminal_size().columns - text.cell_len

    async def handle_internal_message(self, message: Message) -> None:
        text = self.render_internal_message(message)

        self.console.print(text, soft_wrap=True)

    async def handle_process_message(self, command: CommandConfig, message: Message) -> None:
        text = self.render_process_message(command, message)

        self.console.print(text, soft_wrap=True)

    def render_internal_message(self, message):
        return (
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


@dataclass(frozen=True)
class TUIRenderer(LogRenderer):
    internal_message_log: Deque[Message] = field(default_factory=lambda: deque(maxlen=1000))
    process_message_log: DefaultDict[CommandConfig, Deque[Message]] = field(
        default_factory=lambda: defaultdict(lambda: deque(maxlen=1000))
    )

    def available_process_width(self, command: CommandConfig) -> int:
        return super().available_process_width(command) - 20

    async def handle_internal_message(self, message: Message) -> None:
        self.internal_message_log.append(message)

    async def handle_process_message(self, command: CommandConfig, message: Message) -> None:
        self.process_message_log[command].append(message)

    async def mount(self):
        app = RendererApp(console=self.console, view_logs=self.view_logs, log="foo.log")
        await app.process_messages()

    def view_logs(self) -> Text:
        messages = list(
            (m.timestamp, self.render_internal_message(m)) for m in self.internal_message_log
        )

        for p, ms in self.process_message_log.items():
            messages.extend((m.timestamp, self.render_process_message(p, m)) for m in ms)

        return Text("\n").join(v[1] for v in sorted(messages, key=lambda v: v[0]))


class RendererApp(App):
    def __init__(self, *args, view_logs, **kwargs):
        self.view_logs = view_logs

        super().__init__(*args, **kwargs)

    async def on_load(self, event: events.Load) -> None:
        """Bind keys with the app loads (but before entering application mode)"""
        await self.bind("q", "quit", "Quit")

    async def on_mount(self, event: events.Mount) -> None:
        """Create and dock the widgets."""

        # A scrollview to contain the markdown file
        body = ScrollView()

        # Header / footer / dock
        await self.view.dock(Header(), edge="top")
        await self.view.dock(Footer(), edge="bottom")

        # Dock the body in the remaining space
        await self.view.dock(body, edge="right")

        async def get_markdown() -> None:
            await body.update(self.view_logs())

        self.set_interval(1, get_markdown)


RENDERERS: Mapping[Literal["null", "log", "tui"], Type[Renderer]] = {
    "null": NullRenderer,
    "log": LogRenderer,
    "tui": TUIRenderer,
}
