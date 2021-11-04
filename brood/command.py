from __future__ import annotations

import os
import time
from asyncio import CancelledError, Task, create_subprocess_shell, create_task, sleep
from asyncio.subprocess import PIPE, STDOUT, Process
from dataclasses import dataclass, field
from functools import cached_property
from signal import SIGKILL, SIGTERM
from typing import Any, Dict, Optional

import psutil

from brood.config import CommandConfig
from brood.event import Event, EventType
from brood.fanout import Fanout
from brood.message import CommandMessage, InternalMessage, Message, Verbosity


@dataclass
class Command:
    config: CommandConfig

    events: Fanout[Event] = field(repr=False)
    messages: Fanout[Message] = field(repr=False)

    process: Process = field(repr=False)
    start_time: float
    stop_time: Optional[float] = None

    # TODO: replace with TypedDict
    stats: Dict[str, Any] = field(repr=False, default_factory=dict)

    width: int = 80

    was_killed: bool = False

    reader: Optional[Task[None]] = None
    statser: Optional[Task[None]] = None

    @classmethod
    async def start(
        cls,
        config: CommandConfig,
        events: Fanout[Event],
        messages: Fanout[Message],
        width: int = 80,
    ) -> Command:
        await messages.put(
            InternalMessage(
                f"Starting command: {config.command_string!r}", verbosity=Verbosity.INFO
            )
        )

        process = await create_subprocess_shell(
            config.command_string,
            stdout=PIPE,
            stderr=STDOUT,
            env={**os.environ, "FORCE_COLOR": "true", "COLUMNS": str(width)},
            preexec_fn=os.setsid,
        )

        command = cls(
            config=config,
            width=width,
            process=process,
            start_time=time.time(),
            events=events,
            messages=messages,
        )

        await events.put(Event(command=command, type=EventType.Started))

        return command

    def __post_init__(self) -> None:
        self.reader = create_task(
            self.read_output(),
            name=f"Read output for {self.config.command_string!r} (pid {self.process.pid})",
        )
        self.statser = create_task(
            self.get_stats(), name=f"Collect stats for {self.config.command_string!r}"
        )
        create_task(
            self.wait(), name=f"Wait for {self.config.command_string!r} (pid {self.process.pid})"
        )

    @property
    def pid(self) -> int:
        return self.process.pid

    @property
    def exit_code(self) -> Optional[int]:
        return self.process.returncode

    @property
    def has_exited(self) -> bool:
        return self.exit_code is not None

    @property
    def elapsed_time(self) -> float:
        if self.stop_time is None:
            return time.time() - self.start_time
        else:
            return self.stop_time - self.start_time

    def _send_signal(self, signal: int) -> None:
        os.killpg(os.getpgid(self.process.pid), signal)

    async def terminate(self) -> None:
        if self.has_exited:
            return None

        self.was_killed = True

        await self.messages.put(
            InternalMessage(
                f"Terminating command: {self.config.command_string!r} (pid {self.process.pid})",
                verbosity=Verbosity.INFO,
            )
        )

        self._send_signal(SIGTERM)

    async def kill(self) -> None:
        if self.has_exited:
            return None

        self.was_killed = True

        await self.messages.put(
            InternalMessage(
                f"Killing command: {self.config.command_string!r} (pid {self.process.pid})",
                verbosity=Verbosity.INFO,
            )
        )

        self._send_signal(SIGKILL)

    async def wait(self) -> Command:
        await self.process.wait()
        self.stop_time = time.time()

        if self.reader:
            try:
                await self.reader
            except CancelledError:
                pass

        if self.statser:
            self.statser.cancel()

        await self.events.put(Event(command=self, type=EventType.Stopped))

        return self

    async def read_output(self) -> None:
        if self.process.stdout is None:  # pragma: unreachable
            raise Exception(f"{self.process} does not have an associated stream reader")

        while True:
            line = await self.process.stdout.readline()
            if not line:
                break

            await self.messages.put(
                CommandMessage(
                    text=line.decode("utf-8").rstrip(),
                    command_config=self.config,
                )
            )

    @cached_property
    def ps(self) -> Optional[psutil.Process]:
        try:
            return psutil.Process(self.pid).children()[0]
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, IndexError):
            return None

    async def get_stats(self) -> None:
        while True:
            if self.has_exited:
                break

            p = self.ps
            if p is None:
                break
            else:
                self.stats = p.as_dict()
            await sleep(2)

    def __hash__(self) -> int:
        return hash((self.__class__, self.config, self.pid))
