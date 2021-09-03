from asyncio import Queue
from typing import Tuple

import pytest

from brood.command import CommandManager, Event
from brood.config import CommandConfig, OnceConfig
from brood.constants import ON_WINDOWS
from brood.fanout import Fanout
from brood.message import CommandMessage, Message
from brood.monitor import drain_queue


@pytest.fixture
def once_config(command: str) -> CommandConfig:
    return CommandConfig(
        name="test",
        command=command,
        starter=OnceConfig(),
    )


@pytest.fixture
async def once_manager_(once_config: CommandConfig) -> Tuple[CommandManager, Queue[Message]]:
    process_events_fanout: Fanout[Event] = Fanout()

    messages_fanout: Fanout[Message] = Fanout()
    messages_consumer = messages_fanout.consumer()

    return (
        await CommandManager.start(
            command_config=once_config,
            events=process_events_fanout,
            messages=messages_fanout,
            width=80,
            delay=False,
        ),
        messages_consumer,
    )


@pytest.fixture
def once_manager(once_manager_: Tuple[CommandManager, Queue[Message]]) -> CommandManager:
    return once_manager_[0]


@pytest.fixture
def messages(once_manager_: Tuple[CommandManager, Queue[Message]]) -> Queue[Message]:
    return once_manager_[1]


@pytest.mark.parametrize("command", ["echo hi", "echo hi 1>&2"])
async def test_command_output_captured_as_command_message(
    once_manager: CommandManager, messages: Queue[Message], command: str
) -> None:
    await once_manager.wait()

    command_messages = [
        message for message in await drain_queue(messages) if isinstance(message, CommandMessage)
    ]

    assert len(command_messages) == 1

    message = command_messages[0]

    assert message.text == "hi"
    assert message.command_config is once_manager.command_config


@pytest.mark.parametrize("command, exit_code", [("exit 0", 0), ("exit 1", 1)])
async def test_capture_exit_code(
    once_manager: CommandManager, command: str, exit_code: int
) -> None:
    await once_manager.wait()

    assert once_manager.exit_code == exit_code


@pytest.mark.parametrize("command", ["sleep 1"])
async def test_has_exited_lifecycle(once_manager: CommandManager, command: str) -> None:
    assert not once_manager.has_exited

    await once_manager.wait()

    assert once_manager.has_exited


@pytest.mark.parametrize("command", ["sleep 1000"])
async def test_can_terminate_before_completion(once_manager: CommandManager, command: str) -> None:
    await once_manager.terminate()

    await once_manager.wait()

    assert once_manager.exit_code == (-15 if not ON_WINDOWS else 1)


@pytest.mark.parametrize("command", ["sleep 1000"])
async def test_can_kill_before_completion(once_manager: CommandManager, command: str) -> None:
    await once_manager.kill()

    await once_manager.wait()

    assert once_manager.exit_code == (-9 if not ON_WINDOWS else 1)


@pytest.mark.parametrize("command", ["echo hi"])
async def test_can_stop_after_exit(once_manager: CommandManager, command: str) -> None:
    await once_manager.wait()

    await once_manager.terminate()


@pytest.mark.parametrize("command", ["echo hi"])
async def test_can_kill_after_exit(once_manager: CommandManager, command: str) -> None:
    await once_manager.wait()

    await once_manager.kill()
