from typer.testing import CliRunner

from brood.constants import PACKAGE_NAME, __version__
from brood.main import app


def test_help(runner: CliRunner) -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0


def test_version(runner: CliRunner) -> None:
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert PACKAGE_NAME in result.output
    assert __version__ in result.output
