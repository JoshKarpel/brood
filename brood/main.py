from rich.console import Console
from typer import Typer

from brood.constants import PACKAGE_NAME, __version__

app = Typer()


@app.command()
def version() -> None:
    """
    Display version and debugging information.
    """
    console = Console()

    console.print(f"{PACKAGE_NAME} {__version__}")
