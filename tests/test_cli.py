from typer.testing import CliRunner

from brood.main import app


def test_help(runner: CliRunner) -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
