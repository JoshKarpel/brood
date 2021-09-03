from asyncio import Queue
from io import StringIO

import pytest
from rich.console import Console
from typer.testing import CliRunner

from brood.command import CommandManager
from brood.config import BroodConfig, CommandConfig, OnceConfig
from brood.monitor import Monitor


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def output() -> StringIO:
    return StringIO()


@pytest.fixture
def console(output: StringIO) -> Console:
    return Console(
        file=output,
        force_terminal=True,
        width=80,
    )


@pytest.fixture
def once_config(command: str) -> CommandConfig:
    return CommandConfig(
        name="test",
        command=command,
        starter=OnceConfig(),
    )


@pytest.fixture
async def once_manager(once_config: CommandConfig) -> CommandManager:
    return await CommandManager.start(
        command_config=once_config,
        process_messages=Queue(),
        internal_messages=Queue(),
        process_events=Queue(),
        width=80,
        delay=False,
    )


@pytest.fixture
async def once_monitor(once_config: CommandConfig, console: Console) -> Monitor:
    return Monitor(
        config=BroodConfig(commands=[once_config]),
        console=console,
    )
