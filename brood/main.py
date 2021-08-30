import asyncio
from pathlib import Path

from rich.console import Console
from typer import Argument, Typer

from brood.config import Config
from brood.constants import PACKAGE_NAME, __version__
from brood.run import Coordinator

app = Typer()


@app.command()
def run(path: Path = Argument(..., exists=True, readable=True), verbose: bool = False) -> None:
    console = Console()

    config = Config.from_toml(path)

    if verbose:
        console.print(config)

    asyncio.run(_run(config, console))


async def _run(config, console):
    async with Coordinator(config=config, console=console) as coordinator:
        await coordinator.wait()


@app.command()
def parse(path: Path = Argument(..., exists=True, readable=True)) -> None:
    console = Console()

    console.print(Config.from_toml(path))


@app.command()
def version() -> None:
    """
    Display version and debugging information.
    """
    console = Console()

    console.print(f"{PACKAGE_NAME} {__version__}")
