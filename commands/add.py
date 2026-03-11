
from typing import Optional

import typer


def add_command(
    name: Optional[str] = typer.Option(
        None,
        help="Name of the item to add"
    )
):
    pass