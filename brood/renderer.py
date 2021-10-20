from __future__ import annotations

import asyncio
import re
import shutil
import time
from asyncio import (
    ALL_COMPLETED,
    FIRST_EXCEPTION,
    AbstractEventLoop,
    Queue,
    all_tasks,
    create_task,
    current_task,
    get_running_loop,
    wait,
)
from dataclasses import dataclass
from datetime import timedelta
from functools import cached_property
from pathlib import Path
from shutil import get_terminal_size
from typing import Dict, Literal, Mapping, Optional, Type

import psutil
from colorama import Fore
from colorama import Style as CStyle
from rich.console import Console, ConsoleRenderable, Group
from rich.live import Live
from rich.rule import Rule
from rich.spinner import Spinner
from rich.style import Style
from rich.table import Column, Table
from rich.text import Text

from brood.command import Command, Event, EventType
from brood.config import CommandConfig, LogRendererConfig, RendererConfig
from brood.message import CommandMessage, InternalMessage, Message, Verbosity

DIM_RULE = Rule(style="dim")

NULL_STYLE = Style.null()
RE_ANSI_ESCAPE = re.compile(r"(\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]))")
ANSI_COLOR_TO_STYLE = {
    CStyle.RESET_ALL: NULL_STYLE,
    CStyle.NORMAL: NULL_STYLE,
    CStyle.BRIGHT: Style(bold=True),
    CStyle.DIM: Style(dim=True),
    Fore.RED: Style(color="red"),
    Fore.GREEN: Style(color="green"),
    Fore.BLUE: Style(color="blue"),
    Fore.CYAN: Style(color="cyan"),
    Fore.YELLOW: Style(color="yellow"),
    Fore.MAGENTA: Style(color="magenta"),
    Fore.BLACK: Style(color="black"),
    Fore.WHITE: Style(color="white"),
}


def ansi_to_text(s: str) -> Text:
    text = Text()
    buffer = ""
    style = NULL_STYLE
    for char in RE_ANSI_ESCAPE.split(s):
        if char in ANSI_COLOR_TO_STYLE:
            # close current buffer
            text = text.append(buffer, style=style)

            # set up next buffer
            new_style = ANSI_COLOR_TO_STYLE[char]
            style = Style.combine((style, new_style)) if new_style is not NULL_STYLE else new_style
            buffer = ""
        else:
            buffer += char

    # catch leftover buffer
    text.append(buffer, style=style)

    return text


@dataclass
class Renderer:
    config: RendererConfig
    commands: Dict[CommandConfig, Optional[Command]]
    console: Console

    verbosity: Verbosity

    messages: Queue[Message]
    events: Queue[Event]

    def available_process_width(self, command_config: CommandConfig) -> int:
        raise NotImplementedError

    async def mount(self) -> None:
        pass

    async def unmount(self) -> None:
        pass

    async def run(self, drain: bool = False) -> None:
        done, pending = await wait(
            (
                create_task(
                    self.handle_events(drain=drain), name=f"{type(self).__name__} event handler"
                ),
                create_task(
                    self.handle_messages(drain=drain), name=f"{type(self).__name__} message handler"
                ),
            ),
            return_when=ALL_COMPLETED if drain else FIRST_EXCEPTION,
        )

        for d in done:
            d.result()

    async def handle_events(self, drain: bool = False) -> None:
        while True:
            if drain and self.events.empty():
                return

            event = await self.events.get()

            if event.type is EventType.Started:
                await self.handle_started_event(event)
            elif event.type is EventType.Stopped:
                await self.handle_stopped_event(event)

            self.events.task_done()

    async def handle_started_event(self, event: Event) -> None:
        self.commands[event.manager.config] = event.manager

    async def handle_stopped_event(self, event: Event) -> None:
        pass

    async def handle_messages(self, drain: bool = False) -> None:
        while True:
            if drain and self.messages.empty():
                return

            message = await self.messages.get()

            if isinstance(message, InternalMessage):
                if message.verbosity <= self.verbosity:
                    await self.handle_internal_message(message)
            elif isinstance(message, CommandMessage):
                await self.handle_command_message(message)

            self.messages.task_done()

    async def handle_internal_message(self, message: InternalMessage) -> None:
        pass

    async def handle_command_message(self, message: CommandMessage) -> None:
        pass


@dataclass
class NullRenderer(Renderer):
    def available_process_width(self, command_config: CommandConfig) -> int:
        return shutil.get_terminal_size().columns


GREEN_CHECK = Text("✔", style="green")
RED_X = Text("✘", style="red")


@dataclass
class LogRenderer(Renderer):
    config: LogRendererConfig

    def prefix_width(self, command_config: CommandConfig) -> int:
        return self.render_command_prefix(
            CommandMessage(text="", command_config=command_config)
        ).cell_len

    def available_process_width(self, command_config: CommandConfig) -> int:
        return get_terminal_size().columns - self.prefix_width(command_config)

    @cached_property
    def live(self) -> Live:
        return Live(
            console=self.console,
            renderable=StatusTable(
                loop=get_running_loop(),
                config=self.config,
                commands=self.commands,
                show_task_status=self.verbosity.is_debug,
            ),
            transient=True,
        )

    async def mount(self) -> None:
        if not self.config.status_tracker:
            return

        self.live.start()

        while True:
            await asyncio.sleep(1 / 20)
            self.live.refresh()

    async def unmount(self) -> None:
        self.live.stop()

    async def handle_internal_message(self, message: InternalMessage) -> None:
        self.console.print(self.render_internal_message(message), soft_wrap=True)

    def render_internal_message(self, message: InternalMessage) -> ConsoleRenderable:
        prefix = Text.from_markup(
            self.config.internal_prefix.format_map({"timestamp": message.timestamp}),
            style=self.config.internal_prefix_style,
        )
        body = Text(
            message.text,
            style=self.config.internal_message_style,
        )

        g = Table.grid()
        g.add_row(prefix, body)

        return g

    async def handle_command_message(self, message: CommandMessage) -> None:
        self.console.print(self.render_command_message(message), soft_wrap=True)

    def render_command_message(self, message: CommandMessage) -> ConsoleRenderable:
        g = Table.grid()
        g.add_row(self.render_command_prefix(message), ansi_to_text(message.text))

        return g

    def render_command_prefix(self, message: CommandMessage) -> Text:
        return Text.from_markup(
            (message.command_config.prefix or self.config.prefix).format_map(
                {
                    "name": message.command_config.name,
                    "timestamp": message.timestamp,
                }
            ),
            style=message.command_config.prefix_style or self.config.prefix_style,
        )


@dataclass(frozen=True)
class StatusTable:
    loop: AbstractEventLoop
    config: LogRendererConfig
    commands: Dict[CommandConfig, Optional[Command]]
    show_task_status: bool

    def __rich__(self) -> ConsoleRenderable:
        table = Table(
            Column(""),
            Column("$?", justify="right", width=3),
            Column("pid", justify="right", width=5),
            Column("ΔT", justify="right"),
            Column("Command", justify="left"),
            Column("Starter", justify="left"),
            expand=False,
            padding=(0, 1),
            show_edge=False,
            box=None,
            header_style=Style(bold=True),
        )
        for config, command in self.commands.items():
            if command:
                try:
                    p = psutil.Process(command.process.pid).as_dict()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    p = {}
                if command.has_exited:
                    if command.exit_code == 0:
                        spinner = GREEN_CHECK
                        exit_style = Style(color="green")
                    else:
                        spinner = RED_X
                        exit_style = Style(color="red")
                else:
                    s = Spinner("dots")
                    s.start_time = command.start_time
                    spinner = s.render(time.time())
                    exit_style = NULL_STYLE
                elapsed = Text(str(timedelta(seconds=int(command.elapsed_time))))
            else:
                spinner = Text("-", style="dim")
                elapsed = Text("-:--:--", style="dim")
                exit_style = Style(dim=True)
                p = {}

            table.add_row(
                spinner,
                Text(
                    str(command.exit_code if command and command.exit_code is not None else "?"),
                    style=exit_style,
                ),
                Text(
                    str(command.process.pid if command else "-"),
                    style="dim" if not command else None,
                ),
                elapsed,
                Text(
                    config.command_string,
                    style=config.prefix_style or self.config.prefix_style,
                ),
                Text(
                    config.starter.pretty(),
                    style=Style.parse(self.config.prefix_style).chain(Style(dim=True, italic=True)),
                ),
            )

        tables = [table]

        if self.show_task_status:
            debug_table = Table.grid(expand=False, padding=(0, 1))
            active_task = current_task(self.loop)
            for task in sorted(all_tasks(self.loop), key=lambda task: task.get_name()):
                frame = task.get_stack(limit=1)[0]
                code = frame.f_code

                debug_table.add_row(
                    Text(task.get_name()),
                    Text(f"{Path(code.co_filename).name}:{frame.f_lineno}::{code.co_name}"),
                    style=Style(dim=task is not active_task),
                )

            tables.append(debug_table)

        ubertable = Table.grid(expand=True, padding=(0, 2))
        ubertable.add_row(*tables)

        return Group(DIM_RULE, ubertable)


RENDERERS: Mapping[Literal["null", "log"], Type[Renderer]] = {
    "null": NullRenderer,
    "log": LogRenderer,
}
