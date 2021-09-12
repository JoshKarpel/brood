import asyncio
import logging
from asyncio import create_task
from pathlib import Path

from click.exceptions import Exit
from rich.console import Console
from rich.json import JSON
from rich.panel import Panel
from typer import Argument, Option, Typer

from brood.config import BroodConfig
from brood.constants import PACKAGE_NAME, __version__
from brood.executor import Executor
from brood.message import Verbosity

app = Typer()


@app.command()
def run(
    config_path: Path = Argument(
        default="brood.yaml",
        metavar="config",
        exists=True,
        readable=True,
        show_default=True,
        envvar="BROOD_CONFIG",
        help="The path to the configuration file to execute.",
    ),
    verbosity: Verbosity = Option(
        Verbosity.INFO,
        "-v",
        case_sensitive=False,
        help="Set the verbosity level for Brood's monitoring and error displays.",
    ),
    dry: bool = Option(
        False,
        help="If enabled, do not run actually run any commands.",
    ),
) -> None:
    """
    Execute a configuration.

    This command exits with code 0 as long as no internal errors occurred.
    For example, using Ctrl-C to stop Brood from running will still result in an exit code of 0.
    """
    console = Console()

    config = BroodConfig.load(config_path)

    if verbosity.is_debug:
        console.print(
            Panel(
                JSON.from_data(config.dict()),
                title="Configuration",
                title_align="left",
            )
        )

        logging.basicConfig(filename="logging.log", level=logging.DEBUG)

    if dry:
        return

    try:
        asyncio.run(execute(config, console, verbosity), debug=verbosity.is_debug)
    except KeyboardInterrupt:
        raise Exit(code=0)


async def execute(config: BroodConfig, console: Console, verbosity: Verbosity) -> None:
    async with Executor(config=config, console=console, verbosity=verbosity) as executor:
        await create_task(executor.run(), name=f"Run {type(executor).__name__}")


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
