from asyncio import Queue
from unittest.mock import call

import pytest
from pytest_mock import MockerFixture

from brood.command import CommandManager
from brood.config import CommandConfig
from brood.constants import ON_WINDOWS


@pytest.mark.parametrize("command", ["echo hi", "echo hi 1>&2"])
async def test_command_output_to_process_message(
    once_manager: CommandManager, command: str
) -> None:
    config, message = await once_manager.process_messages.get()

    assert config is once_manager.command_config
    assert message.text == "hi"


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


@pytest.mark.parametrize("command", ["echo hi"])
async def test_delay_induces_sleep(
    once_config: CommandConfig, command: str, mocker: MockerFixture
) -> None:
    mock = mocker.patch("brood.command.sleep")

    await CommandManager.start(
        command_config=once_config,
        process_messages=Queue(),
        internal_messages=Queue(),
        process_events=Queue(),
        width=80,
        delay=True,
    )

    assert mock.call_args == call(once_config.starter.delay)
