from __future__ import annotations

import asyncio
import re
import shutil
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
from dataclasses import dataclass, field
from datetime import timedelta
from functools import cached_property, partial
from pathlib import Path
from random import choice
from shutil import get_terminal_size
from typing import Dict, List, Literal, Mapping, Type

from colorama import Fore
from colorama import Style as CStyle
from rich.console import Console, ConsoleRenderable, Group
from rich.live import Live
from rich.progress import Progress, ProgressColumn, RenderableColumn, SpinnerColumn, Task, TaskID
from rich.rule import Rule
from rich.style import Style
from rich.table import Table
from rich.text import Text

from brood.command import Command, Event, EventType
from brood.config import CommandConfig, LogRendererConfig, RendererConfig
from brood.message import CommandMessage, InternalMessage, Message, Verbosity
from brood.utils import delay

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


@dataclass(frozen=True)
class Renderer:
    config: RendererConfig
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
        pass

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


@dataclass(frozen=True)
class NullRenderer(Renderer):
    def available_process_width(self, command_config: CommandConfig) -> int:
        return shutil.get_terminal_size().columns


DOTS = ["dots"] + [f"dots{n}" for n in range(2, 12)]
GREEN_CHECK = Text("✔", style="green")
RED_X = Text("✘", style="red")


class TimeElapsedColumn(ProgressColumn):
    """Renders time elapsed."""

    def render(self, task: "Task") -> Text:
        """Show time remaining."""
        elapsed = task.finished_time if task.finished else task.elapsed
        if elapsed is None:
            return Text("-:--:--", style="dim")
        delta = timedelta(seconds=int(elapsed))
        return Text(str(delta), style="dim")


@dataclass(frozen=True)
class LogRenderer(Renderer):
    config: LogRendererConfig

    status_bars: Dict[Command, Progress] = field(default_factory=dict)
    stop_tasks: List[asyncio.Task[None]] = field(default_factory=list)

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
                status_bars=self.status_bars,
                show_task_status=self.verbosity.is_debug,
            ),
            transient=True,
        )

    async def mount(self) -> None:
        if not self.config.status_tracker:
            return

        self.live.start()

        while True:
            # TODO: I think the "leaving behind" is actually from the status area shrinking, and thus leaving behind messages
            await asyncio.sleep(1 / 30)
            self.live.refresh()

    async def unmount(self) -> None:
        for task in self.stop_tasks:
            task.cancel()

        self.live.stop()

    async def handle_started_event(self, event: Event) -> None:
        if not self.config.status_tracker:
            return

        p = Progress(
            SpinnerColumn(spinner_name=choice(DOTS), style=NULL_STYLE),
            RenderableColumn(Text("  ?", style="dim")),
            RenderableColumn(Text(str(event.manager.process.pid).rjust(5), style="dim")),
            TimeElapsedColumn(),
            RenderableColumn(
                Text(
                    event.manager.config.command_string,
                    style=event.manager.config.prefix_style or self.config.prefix_style,
                )
            ),
            console=self.console,
        )
        p.add_task("", total=1)

        self.status_bars[event.manager] = p

    async def handle_stopped_event(self, event: Event) -> None:
        if not self.config.status_tracker:
            return

        p = self.status_bars.get(event.manager, None)
        if p is None:
            return

        p.columns[0].finished_text = GREEN_CHECK if event.manager.exit_code == 0 else RED_X  # type: ignore
        p.columns[1].renderable = Text(  # type: ignore
            str(event.manager.exit_code).rjust(3),
            style="green" if event.manager.exit_code == 0 else "red",
        )
        p.update(TaskID(0), completed=1)

        self.stop_tasks.append(
            delay(
                delay=10,
                fn=partial(self.remove_status_bar, manager=event.manager),
                name=f"Remove status entry for pid {event.manager.process.pid}",
            )
        )

    async def remove_status_bar(self, manager: Command) -> None:
        self.status_bars.pop(manager, None)

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
    status_bars: Dict[Command, Progress]
    show_task_status: bool
    shutting_down: bool = False

    @property
    def rule(self) -> Rule:
        running_commands = [command for command in self.status_bars if not command.has_exited]
        num_running_commands = len(running_commands)
        return Rule(
            title=Text(
                f"{num_running_commands} running",
                style=Style(dim=True),
            ),
            style=Style(dim=True),
        )

    def __rich__(self) -> ConsoleRenderable:
        table = Table.grid(expand=False, padding=(0, 1))
        for k, v in sorted(self.status_bars.items(), key=lambda kv: kv[0].process.pid):
            table.add_row(v)  # type: ignore

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

        return Group(self.rule, ubertable)


RENDERERS: Mapping[Literal["null", "log"], Type[Renderer]] = {
    "null": NullRenderer,
    "log": LogRenderer,
}
