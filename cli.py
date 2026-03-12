import typer
from typing import Optional

from commands.add import add_command
from commands.backup import backup_command
from commands.init import init_command
from commands.list import list_command
from commands.studio import studio_command
from commands.test import test_command

app = typer.Typer(
    help="Backfup CLI - A tool for backing up data to S3-compatible storage."
)

app.command("init")(init_command)
app.command("add")(add_command)
app.command("test")(test_command)
app.command("studio")(studio_command)
app.command("backup")(backup_command)
app.command("list")(list_command)