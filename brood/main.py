import asyncio
from pathlib import Path

from rich.console import Console
from typer import Argument, Typer

from brood.config import Config
from brood.constants import PACKAGE_NAME, __version__
from brood.run import Coordinator

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
) -> None:
    console = Console()

    config_ = Config.from_yaml(config)

    if verbose:
        console.print_json(config_.json())

    if dry:
        return

    asyncio.run(_run(config_, console))


async def _run(config, console):
    async with Coordinator(config=config, console=console) as coordinator:
        await coordinator.wait()


@app.command()
def version() -> None:
    """
    Display version and debugging information.
    """
    console = Console()

    console.print(f"{PACKAGE_NAME} {__version__}")
