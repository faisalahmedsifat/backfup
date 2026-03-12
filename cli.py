import typer

from commands.add import add_command
from commands.init import init_command
from commands.test import test_command
from commands.studio import studio_command
from commands.restore import restore_command
from commands.list import list_command
from commands.backup import backup_run_command
from commands.backup_list import backup_list_command
from commands.storage_list import storage_list_command
from commands.schedule import (
    schedule_create_command,
    schedule_edit_command,
    schedule_remove_command,
    schedule_list_command,
)

app = typer.Typer(
    help="Backfup CLI - A tool for backing up data to S3-compatible storage.",
    no_args_is_help=True,
)

# ─── backup sub-app ───────────────────────────────────────────────────────────

backup_app = typer.Typer(help="Run and manage backups.")

@backup_app.callback(invoke_without_command=True)
def backup_callback(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Database name as registered with `backfup add`"),
):
    ctx.ensure_object(dict)
    ctx.obj = name
    if ctx.invoked_subcommand is None:
        backup_run_command(name)

@backup_app.command("list")
def _backup_list(ctx: typer.Context):
    """List available backups for a database."""
    backup_list_command(ctx.obj)

# ─── storage sub-app ──────────────────────────────────────────────────────────

storage_app = typer.Typer(help="Inspect storage.", no_args_is_help=True)
storage_app.command("list")(storage_list_command)

# ─── schedule sub-app ─────────────────────────────────────────────────────────

schedule_app = typer.Typer(help="Manage backup schedules.", no_args_is_help=True)
schedule_app.command("create")(schedule_create_command)
schedule_app.command("list")(schedule_list_command)
schedule_app.command("edit")(schedule_edit_command)
schedule_app.command("remove")(schedule_remove_command)

# ─── Wire up ──────────────────────────────────────────────────────────────────

app.add_typer(backup_app,   name="backup")
app.add_typer(storage_app,  name="storage")
app.add_typer(schedule_app, name="schedule")

app.command("init")(init_command)
app.command("add")(add_command)
app.command("list")(list_command)
app.command("restore")(restore_command)
app.command("test")(test_command)
app.command("studio")(studio_command)