import asyncio
import logging
import sys
from pathlib import Path

from click.exceptions import Exit
from rich.console import Console
from rich.json import JSON
from rich.panel import Panel
from typer import Argument, Option, Typer

from brood.config import BroodConfig
from brood.constants import PACKAGE_NAME, __version__
from brood.executor import Executor

app = Typer()


@app.command()
def run(
    config_path: Path = Argument(
        "brood.yaml",
        metavar="config",
        exists=True,
        readable=True,
        show_default=True,
        envvar="BROOD_CONFIG",
        help="The path to the configuration file to execute.",
    ),
    dry: bool = Option(False, help="If enabled, do not run actually run any commands."),
    verbose: bool = Option(
        False, help=f"If enabled, {PACKAGE_NAME} will print extra information as it runs."
    ),
    debug: bool = Option(
        False,
        help=f"If enabled, {PACKAGE_NAME} will run with the underlying event loop in debug mode.",
    ),
) -> None:
    """
    Execute a configuration.

    This command exits with code 0 as long as no internal errors occurred.
    For example, using Ctrl-C to stop Brood from running will still result in an exit code of 0.
    """
    console = Console()

    config = BroodConfig.load(config_path)

    verbose = verbose or debug

    if verbose:
        console.print(
            Panel(
                JSON.from_data(config.dict()),
                title="Configuration",
                title_align="left",
            )
        )

    if dry:
        return

    if debug:
        logging.basicConfig(level=logging.DEBUG)

    try:
        asyncio.run(execute(config, console), debug=debug)
    except KeyboardInterrupt:
        raise Exit(code=0)


async def execute(config: BroodConfig, console: Console) -> None:
    async with Executor(config=config, console=console) as executor:
        await executor.run()


@app.command()
def schema(plain: bool = Option(False)) -> None:
    """
    Display the Brood configuration file schema.
    """
    console = Console()

    j = BroodConfig.schema_json(indent=2)

    if plain:
        print(j)
    else:
        console.print(
            Panel(
                JSON(j),
                title="Configuration Schema",
                title_align="left",
            ),
        )


@app.command()
def version() -> None:
    """
    Display version and debugging information.
    """
    console = Console()

    console.print(f"{PACKAGE_NAME} {__version__}")
