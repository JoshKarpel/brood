from typing import List, Union

import pytest
from _pytest.tmpdir import TempPathFactory
from hypothesis import given
from hypothesis import strategies as st

from brood.config import BroodConfig, CommandConfig, ConfigFormat
from brood.errors import UnknownFormat


@given(config=st.builds(BroodConfig))
@pytest.mark.parametrize("fmt", BroodConfig.FORMATS)
def test_to_and_from(config: BroodConfig, fmt: ConfigFormat) -> None:
    s = config.to_format(fmt)
    assert config.from_format(s, fmt) == config


@given(config=st.builds(BroodConfig))
@pytest.mark.parametrize("to_fmt", BroodConfig.FORMATS)
@pytest.mark.parametrize("from_fmt", BroodConfig.FORMATS)
def test_save_and_load(
    config: BroodConfig,
    to_fmt: ConfigFormat,
    from_fmt: ConfigFormat,
    tmp_path_factory: TempPathFactory,
) -> None:
    p = tmp_path_factory.mktemp("config") / f"config.{to_fmt}"

    config.save(p)

    assert config == BroodConfig.load(p)


@given(config=st.builds(BroodConfig))
@pytest.mark.parametrize("fmt", ["wrong"])
def test_save_with_unknown_format(
    config: BroodConfig,
    fmt: str,
    tmp_path_factory: TempPathFactory,
) -> None:
    p = tmp_path_factory.mktemp("config") / f"config.{fmt}"

    with pytest.raises(UnknownFormat):
        config.save(p)


@given(config=st.builds(BroodConfig))
@pytest.mark.parametrize("fmt", ["wrong"])
def test_load_with_unknown_format(
    config: BroodConfig,
    fmt: str,
    tmp_path_factory: TempPathFactory,
) -> None:
    p = tmp_path_factory.mktemp("config") / f"config.{fmt}"
    p.touch()

    with pytest.raises(UnknownFormat):
        assert config == BroodConfig.load(p)


@pytest.mark.parametrize(
    "cmd, expected",
    [
        ("foo", "foo"),
        (["foo"], "foo"),
        (["foo", "bar"], "foo bar"),
    ],
)
def test_command_string(cmd: Union[str, List[str]], expected: str) -> None:
    config = CommandConfig(
        name="test",
        command=cmd,
    )

    assert config.command_string == expected
