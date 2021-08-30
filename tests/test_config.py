import pytest
from _pytest.tmpdir import TempPathFactory
from hypothesis import given
from hypothesis import strategies as st

from brood.config import Config, ConfigFormat


@given(config=st.builds(Config))
@pytest.mark.parametrize("fmt", Config.FORMATS)
def test_to_and_from(config: Config, fmt: ConfigFormat) -> None:
    s = config.to_fmt(fmt)
    assert config.from_fmt(s, fmt) == config


@given(config=st.builds(Config))
@pytest.mark.parametrize("to_fmt", Config.FORMATS)
@pytest.mark.parametrize("from_fmt", Config.FORMATS)
def test_save_and_load(
    config: Config,
    to_fmt: ConfigFormat,
    from_fmt: ConfigFormat,
    tmp_path_factory: TempPathFactory,
) -> None:
    p = tmp_path_factory.mktemp("config") / f"config.{to_fmt}"
    config.to_file(p)

    assert config == Config.from_file(p)
