from __future__ import annotations

import shutil
from asyncio import ALL_COMPLETED, FIRST_EXCEPTION, Queue, create_task, sleep, wait
from dataclasses import dataclass
from datetime import timedelta
from random import choice
from shutil import get_terminal_size
from typing import Dict, Literal, Mapping, Tuple, Type

from rich.console import Console, Group
from rich.live import Live
from rich.progress import Progress, ProgressColumn, RenderableColumn, SpinnerColumn, Task, TaskID
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from brood.command import CommandManager, EventType, ProcessEvent
from brood.config import CommandConfig, LogRendererConfig, RendererConfig
from brood.message import Message


@dataclass(frozen=True)
class Renderer:
    config: RendererConfig
    console: Console

    process_messages: Queue[Tuple[CommandConfig, Message]]
    internal_messages: Queue[Message]
    process_events: Queue[ProcessEvent]

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

    async def handle_internal_message(self, message: Message) -> None:
        pass

    async def handle_process_messages(self, drain: bool = False) -> None:
        while not drain or not self.process_messages.empty():
            command, message = await self.process_messages.get()
            await self.handle_process_message(command, message)
            self.process_messages.task_done()

    async def handle_process_message(self, process: CommandConfig, message: Message) -> None:
        pass


@dataclass(frozen=True)
class NullRenderer(Renderer):
    def available_process_width(self, command: CommandConfig) -> int:
        return shutil.get_terminal_size().columns


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

    def available_process_width(self, command: CommandConfig) -> int:
        text = self.render_process_message(command, Message(""))
        return get_terminal_size().columns - text.cell_len

    async def mount(self) -> None:
        state: Dict[CommandManager, Progress] = {}

        async def done(manager: CommandManager) -> None:
            p = state.get(manager, None)
            if p is None:
                return

            p.columns[0].finished_text = GREEN_CHECK if manager.exit_code == 0 else RED_X  # type: ignore
            p.columns[1].renderable = Text(  # type: ignore
                str(manager.exit_code).rjust(3), style="green" if manager.exit_code == 0 else "red"
            )
            p.update(TaskID(0), completed=1)  # type: ignore

            await sleep(10)

            state.pop(manager, None)

            refresh()

        rule = Rule(style="dim")

        def refresh() -> None:
            table = Table.grid(expand=True)
            for k, v in sorted(state.items(), key=lambda kv: kv[0].process.pid):
                table.add_row(v)  # type: ignore

            live.update(Group(rule, table))

        with Live(
            console=self.console,
            auto_refresh=True,
            refresh_per_second=30,
            transient=True,
            screen=False,
        ) as live:
            while True:
                event = await self.process_events.get()

                if event.type is EventType.Started:
                    p = Progress(
                        SpinnerColumn(
                            spinner_name=choice(["dots"] + [f"dots{n}" for n in range(2, 12)])
                        ),
                        RenderableColumn(Text("  ?", style="dim")),
                        RenderableColumn(
                            Text(str(event.manager.process.pid).rjust(5), style="dim")
                        ),
                        TimeElapsedColumn(),
                        RenderableColumn(
                            Text(
                                event.manager.command_config.command_string,
                                style=event.manager.command_config.prefix_style
                                or self.config.prefix_style,
                            )
                        ),
                        console=self.console,
                    )
                    p.add_task("", total=1)
                    state[event.manager] = p
                if event.type is EventType.Stopped:
                    create_task(done(event.manager))

                refresh()

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


RENDERERS: Mapping[Literal["null", "log"], Type[Renderer]] = {
    "null": NullRenderer,
    "log": LogRenderer,
}
