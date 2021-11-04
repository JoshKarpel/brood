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

from colorama import Fore
from colorama import Style as CStyle
from rich.console import Console, ConsoleRenderable, Group, RenderableType
from rich.live import Live
from rich.rule import Rule
from rich.spinner import Spinner
from rich.style import Style
from rich.table import Column, Table
from rich.text import Text

from brood.command import Command
from brood.config import CommandConfig, LogRendererConfig, RendererConfig
from brood.event import Event, EventType
from brood.message import CommandMessage, InternalMessage, Message, Verbosity

GREEN_STYLE = Style(color="green")
RED_STYLE = Style(color="red")
BOLD_STYLE = Style(bold=True)
DIM_STYLE = Style(dim=True)
DIM_RULE = Rule(style=DIM_STYLE)
DASH_TEXT = Text("-")
TIME_DASH_TEXT = Text("-:--:--")

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
        self.commands[event.command.config] = event.command

    async def handle_stopped_event(self, event: Event) -> None:
        pass

    async def handle_messages(self, drain: bool = False) -> None:
        while True:
            if drain and self.messages.empty():
                return

            message = await self.messages.get()

            if isinstance(message, InternalMessage):
                if message.verbosity >= self.verbosity:
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


def make_spinner(start_time: float) -> RenderableType:
    s = Spinner("dots")
    s.start_time = start_time
    return s.render(time.time())


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
            Column("PID", justify="right", width=5),
            Column("ΔT", justify="right"),
            Column("CPU", justify="right", min_width=5),
            Column("MEM", justify="right", min_width=5),
            Column("Command", justify="left"),
            Column("Starter", justify="left"),
            expand=False,
            padding=(0, 1),
            show_edge=False,
            box=None,
            header_style=BOLD_STYLE,
        )

        for config, command in self.commands.items():
            if command:
                good_exit = command.exit_code == 0

                spinner_column = (
                    (GREEN_CHECK if good_exit else RED_X)
                    if command.has_exited
                    else make_spinner(command.start_time)
                )

                exit_code_column = (
                    Text(str(command.exit_code), style=GREEN_STYLE if good_exit else RED_STYLE)
                    if command.has_exited
                    else DASH_TEXT
                )

                pid_column = Text(str(command.process.pid))

                elapsed_column = Text(str(timedelta(seconds=int(command.elapsed_time))))

                cpu_percent = command.stats.get("cpu_percent")
                cpu_column = Text(f"{cpu_percent:>4.1f}%") if cpu_percent else DASH_TEXT

                memory_info = command.stats.get("memory_full_info")
                memory_column = (
                    Text(f"{memory_info.uss / (1024**2):.0f} MB") if memory_info else DASH_TEXT
                )
            else:
                spinner_column = DASH_TEXT
                exit_code_column = DASH_TEXT
                pid_column = DASH_TEXT
                elapsed_column = TIME_DASH_TEXT
                cpu_column = DASH_TEXT
                memory_column = DASH_TEXT

            table.add_row(
                spinner_column,
                exit_code_column,
                pid_column,
                elapsed_column,
                cpu_column,
                memory_column,
                Text(config.command_string, style=config.prefix_style or self.config.prefix_style),
                Text(config.starter.description, style=Style(dim=True, italic=True)),
                style=Style(dim=command is None),
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
