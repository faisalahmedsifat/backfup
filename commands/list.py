from datetime import datetime, timezone
from typing import Optional

import boto3
import typer
from botocore.exceptions import ClientError

from config.store import ConfigStore
from utils.credentials import resolve_credential

TIMESTAMP_FORMAT = "%Y-%m-%d-%H-%M-%S"


def _get_s3_client(storage: dict):
    return boto3.client(
        "s3",
        endpoint_url=storage["endpoint"],
        region_name=storage["region"],
        aws_access_key_id=resolve_credential(storage["access_key"]),
        aws_secret_access_key=resolve_credential(storage["secret_key"]),
    )


def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"


def _format_timestamp(key: str) -> str:
    """Parse the timestamp from the S3 key and return a human-readable string."""
    try:
        filename = key.split("/")[-1].replace(".sql.gz", "")
        dt = datetime.strptime(filename, TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except ValueError:
        return key.split("/")[-1]


def fetch_backups(storage: dict, db_name: str) -> list[dict]:
    """Return backups sorted newest first."""
    client = _get_s3_client(storage)
    bucket = storage["bucket"]
    prefix = f"backfup/{db_name}/"

    try:
        response = client.list_objects_v2(Bucket=bucket, Prefix=prefix)
    except ClientError as e:
        typer.echo(f"Error: could not list backups — {e}")
        raise typer.Exit(1)

    objects = response.get("Contents", [])
    backups = []
    for obj in objects:
        if not obj["Key"].endswith(".sql.gz"):
            continue
        # Fetch object metadata to get the backfup-id tag
        try:
            head = client.head_object(Bucket=bucket, Key=obj["Key"])
            backup_id = head.get("Metadata", {}).get("backfup-id", "—")
        except ClientError:
            backup_id = "—"

        backups.append({
            "key": obj["Key"],
            "size": obj["Size"],
            "timestamp": _format_timestamp(obj["Key"]),
            "id": backup_id,
        })

    # Newest first
    backups.sort(key=lambda b: b["key"], reverse=True)
    return backups


def list_command(
    name: str = typer.Argument(..., help="Database name as registered with `backfup add`"),
):
    store = ConfigStore()

    if not store.exists():
        typer.echo("No configuration found. Run `backfup init` first.")
        raise typer.Exit(1)

    config = store.load()

    databases = config.get("databases", [])
    db = next((d for d in databases if d["name"] == name), None)
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
        raise typer.Exit(0)

    typer.echo(f"Available backups for '{name}'\n")

    for i, backup in enumerate(backups, start=1):
        size = _format_size(backup["size"])
        typer.echo(f"  {i}) [{backup['id']}]  {backup['timestamp']}  ({size})")

    typer.echo(f"\n  {len(backups)} backup(s) total")