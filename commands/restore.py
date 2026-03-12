import os
import shutil
import subprocess
import tempfile
from typing import Optional

import boto3
import typer
from botocore.exceptions import ClientError

from config.store import ConfigStore
from utils.credentials import resolve_credential
from commands.backup_list import fetch_backups


def _get_s3_client(storage: dict):
    return boto3.client(
        "s3",
        endpoint_url=storage["endpoint"],
        region_name=storage["region"],
        aws_access_key_id=resolve_credential(storage["access_key"]),
        aws_secret_access_key=resolve_credential(storage["secret_key"]),
    )


def _resolve_backup(backups: list, choice: str) -> Optional[dict]:
    """Accept either a backup ID (e.g. aB3kXz) or a list number (e.g. 1)."""
    # Try as ID first
    by_id = next((b for b in backups if b["id"] == choice), None)
    if by_id:
        return by_id
    # Try as integer index
    try:
        index = int(choice)
        if 1 <= index <= len(backups):
            return backups[index - 1]
    except ValueError:
        pass
    return None


def restore_command(
    name: str = typer.Argument(..., help="Database name as registered with `backfup add`"),
    id: Optional[str] = typer.Option(None, "--id", help="Backup ID to restore directly (skips interactive selection)."),
):
    store = ConfigStore()

    if not store.exists():
        typer.echo("No configuration found. Run `backfup init` first.")
        raise typer.Exit(1)

    config = store.load()

    db = next((d for d in config.get("databases", []) if d["name"] == name), None)
    if not db:
        typer.echo(f"Database '{name}' not found. Run `backfup add` first.")
        raise typer.Exit(1)

    storage = config.get("storage")
    if not storage:
        typer.echo("No storage configured. Run `backfup init` first.")
        raise typer.Exit(1)

    backups = fetch_backups(storage, name)
    if not backups:
        typer.echo(f"No backups found for '{name}'.")
        raise typer.Exit(1)

    # Resolve which backup to restore
    if id:
        backup = next((b for b in backups if b["id"] == id), None)
        if not backup:
            typer.echo(f"No backup found with id '{id}'.")
            typer.echo(f"Run `backfup backup {name} list` to see available backups.")
            raise typer.Exit(1)
    else:
        typer.echo(f"Select a backup to restore for '{name}'\n")
        for i, b in enumerate(backups, start=1):
            typer.echo(f"  {i}) [{b['id']}]  {b['timestamp']}")
        typer.echo("")

        raw = typer.prompt("Enter number or ID")
        backup = _resolve_backup(backups, raw)
        if not backup:
            typer.echo(f"Invalid selection '{raw}'. Enter a list number or a backup ID.")
            raise typer.Exit(1)

    db_type = db.get("type", "postgres")
    connection_url = resolve_credential(db["connection_url"])
    bucket = storage["bucket"]
    key = backup["key"]

    typer.echo(f"\nRestoring backup\n")
    typer.echo(f"  database  → {name} ({db_type})")
    typer.echo(f"  id        → {backup['id']}")
    typer.echo(f"  timestamp → {backup['timestamp']}\n")

    client = _get_s3_client(storage)
    tmp_path = None

    try:
        with tempfile.NamedTemporaryFile(suffix=".sql.gz", delete=False) as tmp:
            tmp_path = tmp.name

        typer.echo("Downloading...", nl=False)
        client.download_file(bucket, key, tmp_path)
        typer.echo(" done")

        typer.echo("Restoring...", nl=False)

        if db_type == "postgres":
            if not shutil.which("psql"):
                typer.echo(" FAILED\nError: psql not found. Install PostgreSQL client tools.")
                raise typer.Exit(1)
            result = subprocess.run(
                f"gunzip -c {tmp_path} | psql {connection_url}",
                shell=True, capture_output=True, text=True,
            )

        elif db_type == "mongodb":
            if not shutil.which("mongorestore"):
                typer.echo(" FAILED\nError: mongorestore not found. Install MongoDB Database Tools.")
                raise typer.Exit(1)
            result = subprocess.run(
                ["mongorestore", f"--uri={connection_url}",
                 f"--archive={tmp_path}", "--gzip", "--drop"],
                capture_output=True, text=True,
            )

        elif db_type == "mysql":
            if not shutil.which("mysql"):
                typer.echo(" FAILED\nError: mysql not found. Install MySQL client tools.")
                raise typer.Exit(1)
            result = subprocess.run(
                f"gunzip -c {tmp_path} | mysql {connection_url}",
                shell=True, capture_output=True, text=True,
            )

        else:
            typer.echo(f" FAILED\nError: unsupported database type '{db_type}'.")
            raise typer.Exit(1)

        if result.returncode != 0:
            typer.echo(f" FAILED\nError: {result.stderr.strip()}")
            raise typer.Exit(1)

        typer.echo(" done")
        typer.echo("\nRestore complete.")

    except ClientError as e:
        typer.echo(f"Error: could not download backup — {e}")
        raise typer.Exit(1)
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass