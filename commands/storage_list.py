import typer
import boto3
from botocore.exceptions import ClientError

from config.store import ConfigStore
from utils.credentials import resolve_credential


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


def storage_list_command():
    store = ConfigStore()

    if not store.exists():
        typer.echo("No configuration found. Run `backfup init` first.")
        raise typer.Exit(1)

    config = store.load()
    storage = config.get("storage")

    if not storage:
        typer.echo("No storage configured. Run `backfup init` first.")
        raise typer.Exit(1)

    typer.echo("Storage configuration\n")
    typer.echo(f"  endpoint  → {storage['endpoint']}")
    typer.echo(f"  bucket    → {storage['bucket']}")
    typer.echo(f"  region    → {storage['region']}")
    typer.echo(f"  access    → {storage['access_key']}")
    typer.echo("")

    typer.echo("Bucket contents\n")
    try:
        client = _get_s3_client(storage)
        response = client.list_objects_v2(Bucket=storage["bucket"], Prefix="backfup/")
        objects = response.get("Contents", [])

        if not objects:
            typer.echo("  (empty)")
            return

        groups: dict[str, list] = {}
        for obj in objects:
            parts = obj["Key"].split("/")
            if len(parts) >= 3:
                db_name = parts[1]
                groups.setdefault(db_name, []).append(obj)

        total_size = 0
        for db_name, objs in sorted(groups.items()):
            db_size = sum(o["Size"] for o in objs)
            total_size += db_size
            typer.echo(f"  {db_name}/   ({len(objs)} backup(s), {_format_size(db_size)})")

        typer.echo(f"\n  total → {_format_size(total_size)} across {len(groups)} database(s)")

    except ClientError as e:
        typer.echo(f"Error: could not reach storage — {e}")
        raise typer.Exit(1)