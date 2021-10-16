from asyncio import Queue
from typing import Tuple

import pytest

from brood.command import Command
from brood.config import CommandConfig, OnceConfig
from brood.constants import ON_WINDOWS
from brood.event import Event
from brood.fanout import Fanout
from brood.message import CommandMessage, Message
from brood.utils import drain_queue


@pytest.fixture
def once_config(command: str) -> CommandConfig:
    return CommandConfig(
        name="test",
        command=command,
        starter=OnceConfig(),
    )


PackedManagerFixtureOutput = Tuple[Command, Queue[Event], Queue[Message]]


@pytest.fixture
async def once_manager_(once_config: CommandConfig) -> PackedManagerFixtureOutput:
    events: Fanout[Event] = Fanout()
    events_consumer = events.consumer()

    messages: Fanout[Message] = Fanout()
    messages_consumer = messages.consumer()

    return (
        await Command.start(
            config=once_config,
            events=events,
            messages=messages,
            width=80,
        ),
        events_consumer,
        messages_consumer,
    )


@pytest.fixture
def once_manager(once_manager_: PackedManagerFixtureOutput) -> Command:
    return once_manager_[0]


@pytest.fixture
def events(once_manager_: PackedManagerFixtureOutput) -> Queue[Event]:
    return once_manager_[1]


@pytest.fixture
def messages(once_manager_: PackedManagerFixtureOutput) -> Queue[Message]:
    return once_manager_[2]


@pytest.mark.parametrize("command", ["echo hi", "echo hi 1>&2"])
async def test_command_output_captured_as_command_message(
    once_manager: Command, messages: Queue[Message], command: str
) -> None:
    await once_manager.wait()

    drained = await drain_queue(messages)
    print(drained)

    command_messages = [message for message in drained if isinstance(message, CommandMessage)]
    print(command_messages)

    assert len(command_messages) == 1

    message = command_messages[0]

    assert message.text == "hi"
    assert message.command_config is once_manager.config


@pytest.mark.parametrize("command, exit_code", [("exit 0", 0), ("exit 1", 1)])
async def test_capture_exit_code(once_manager: Command, command: str, exit_code: int) -> None:
    await once_manager.wait()

    assert once_manager.exit_code == exit_code


@pytest.mark.parametrize("command", ["sleep 1"])
async def test_has_exited_lifecycle(once_manager: Command, command: str) -> None:
    assert not once_manager.has_exited

    await once_manager.wait()

    assert once_manager.has_exited


@pytest.mark.parametrize("command", ["sleep 1000"])
async def test_can_terminate_before_completion(once_manager: Command, command: str) -> None:
    await once_manager.terminate()

    await once_manager.wait()

    assert once_manager.exit_code == (-15 if not ON_WINDOWS else 1)


@pytest.mark.parametrize("command", ["sleep 1000"])
async def test_can_kill_before_completion(once_manager: Command, command: str) -> None:
    await once_manager.kill()

    await once_manager.wait()

    assert once_manager.exit_code == (-9 if not ON_WINDOWS else 1)


@pytest.mark.parametrize("command", ["echo hi"])
async def test_can_stop_after_exit(once_manager: Command, command: str) -> None:
    await once_manager.wait()

    await once_manager.terminate()


@pytest.mark.parametrize("command", ["echo hi"])
async def test_can_kill_after_exit(once_manager: Command, command: str) -> None:
    await once_manager.wait()

    await once_manager.kill()
