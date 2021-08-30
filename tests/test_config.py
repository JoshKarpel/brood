import pytest
from _pytest.tmpdir import TempPathFactory
from hypothesis import given
from hypothesis import strategies as st

from brood.config import BroodConfig, ConfigFormat


@given(config=st.builds(BroodConfig))
@pytest.mark.parametrize("fmt", BroodConfig.FORMATS)
def test_to_and_from(config: BroodConfig, fmt: ConfigFormat) -> None:
    s = config.to_fmt(fmt)
    assert config.from_fmt(s, fmt) == config


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
    config.to_file(p)

    assert config == BroodConfig.from_file(p)
