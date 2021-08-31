import asyncio
import logging
from pathlib import Path

from rich.console import Console
from rich.json import JSON
from rich.panel import Panel
from rich.traceback import install
from typer import Argument, Typer

from brood.config import BroodConfig
from brood.constants import PACKAGE_NAME, __version__
from brood.monitor import Monitor

app = Typer()


@app.command()
def run(
    config: Path = Argument(
        "brood.yaml",
        exists=True,
        readable=True,
        show_default=True,
        envvar="BROOD_CONFIG",
    ),
    verbose: bool = False,
    dry: bool = False,
    debug: bool = False,
) -> None:
    console = Console()
    install(console=console, show_locals=True)

    config_ = BroodConfig.load(config)

    verbose = verbose or debug
    if config_.verbose:
        verbose = True

    if verbose:
        config_.verbose = True
        console.print(
            Panel(
                JSON.from_data(config_.dict()),
                title="Configuration",
                title_align="left",
            )
        )

    if dry:
        return

    if debug:
        logging.basicConfig(level=logging.DEBUG)

    asyncio.run(_run(config_, console), debug=debug)


async def _run(config: BroodConfig, console: Console) -> None:
    async with Monitor(config=config, console=console) as coordinator:
        await coordinator.run()


@app.command()
def version() -> None:
    """
    Display version and debugging information.
    """
    console = Console()

    console.print(f"{PACKAGE_NAME} {__version__}")
