import typer
from typing import Optional

from commands.init import init_command

app = typer.Typer(
    help="Backfup CLI - A tool for backing up data to S3-compatible storage."
)

app.command("init")(init_command)