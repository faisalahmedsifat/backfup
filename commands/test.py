import shutil
import subprocess
import typer
from typing import Optional
from botocore.exceptions import ClientError, EndpointResolutionError
import boto3
from loguru import logger

from config.store import ConfigStore
from utils.credentials import resolve_credential

PROBE_KEY = "backfup/.probe"
PROBE_CONTENT = b"backfup-storage-probe"


def _get_s3_client(storage: dict):
    access_key = resolve_credential(storage["access_key"])
    secret_key = resolve_credential(storage["secret_key"])

    return boto3.client(
        "s3",
        endpoint_url=storage["endpoint"],
        region_name=storage["region"],
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )


def _test_storage(config: dict):
    storage_config = config.get("storage")

    if not storage_config:
        typer.echo("No storage configured. Run `backfup init` first.")
        raise typer.Exit(1)

    bucket = storage_config["bucket"]
    typer.echo(f"Testing storage: {storage_config['endpoint']} / {bucket}\n")

    typer.echo("Resolving credentials...", nl=False)
    try:
        client = _get_s3_client(storage_config)
        typer.echo(" OK")
    except KeyError as e:
        typer.echo(f" FAILED\nError: environment variable {e} is not set.")
        raise typer.Exit(1)

    typer.echo("Checking bucket access...", nl=False)
    try:
        client.head_bucket(Bucket=bucket)
        typer.echo(" OK")
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "403":
            typer.echo(" FAILED\nError: credentials are valid but access to the bucket is denied.")
        elif code == "404":
            typer.echo(f" FAILED\nError: bucket '{bucket}' does not exist.")
        else:
            typer.echo(f" FAILED\nError: {e}")
        raise typer.Exit(1)
    except (EndpointResolutionError, Exception) as e:
        typer.echo(f" FAILED\nError: could not connect to endpoint — {e}")
        raise typer.Exit(1)

    typer.echo("Checking write permissions...", nl=False)
    try:
        client.put_object(Bucket=bucket, Key=PROBE_KEY, Body=PROBE_CONTENT)
        typer.echo(" OK")
    except ClientError as e:
        typer.echo(f" FAILED\nError: {e}")
        raise typer.Exit(1)

    typer.echo("Cleaning up probe file...", nl=False)
    try:
        client.delete_object(Bucket=bucket, Key=PROBE_KEY)
        typer.echo(" OK")
    except ClientError as e:
        typer.echo(f" WARNING\nCould not delete probe file ({PROBE_KEY}): {e}")

    typer.echo("\nStorage is configured correctly.")


# ─── Per-engine test implementations ─────────────────────────────────────────

def _test_postgres(connection_url: str):
    typer.echo("Checking pg_dump availability...", nl=False)
    if not shutil.which("pg_dump"):
        typer.echo(" FAILED\nError: pg_dump not found. Install PostgreSQL client tools.")
        raise typer.Exit(1)
    typer.echo(" OK")

    typer.echo("Connecting to database...", nl=False)
    try:
        result = subprocess.run(
            ["psql", connection_url, "-c", "SELECT 1"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            typer.echo(f" FAILED\nError: {result.stderr.strip()}")
            raise typer.Exit(1)
        typer.echo(" OK")
    except FileNotFoundError:
        typer.echo(" FAILED\nError: psql not found. Install PostgreSQL client tools.")
        raise typer.Exit(1)
    except subprocess.TimeoutExpired:
        typer.echo(" FAILED\nError: connection timed out.")
        raise typer.Exit(1)


def _test_mongodb(connection_url: str):
    typer.echo("Checking pymongo availability...", nl=False)
    try:
        import pymongo
        typer.echo(" OK")
    except ImportError:
        typer.echo(" FAILED\nError: pymongo not installed. Run: uv add pymongo")
        raise typer.Exit(1)

    typer.echo("Connecting to database...", nl=False)
    try:
        client = pymongo.MongoClient(connection_url, serverSelectionTimeoutMS=5000)
        client.admin.command("ping")
        client.close()
        typer.echo(" OK")
    except Exception as e:
        typer.echo(f" FAILED\nError: {e}")
        raise typer.Exit(1)


def _test_mysql(connection_url: str):
    typer.echo("Checking mysqldump availability...", nl=False)
    if not shutil.which("mysqldump"):
        typer.echo(" FAILED\nError: mysqldump not found. Install MySQL client tools.")
        raise typer.Exit(1)
    typer.echo(" OK")

    typer.echo("Connecting to database...", nl=False)
    try:
        result = subprocess.run(
            ["mysql", "--connect-timeout=10", connection_url, "-e", "SELECT 1"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            typer.echo(f" FAILED\nError: {result.stderr.strip()}")
            raise typer.Exit(1)
        typer.echo(" OK")
    except FileNotFoundError:
        typer.echo(" FAILED\nError: mysql not found. Install MySQL client tools.")
        raise typer.Exit(1)
    except subprocess.TimeoutExpired:
        typer.echo(" FAILED\nError: connection timed out.")
        raise typer.Exit(1)


# ─── Dispatcher ───────────────────────────────────────────────────────────────

_DB_TESTERS = {
    "postgres": _test_postgres,
    "mongodb":  _test_mongodb,
    "mysql":    _test_mysql,
}


def _test_database(config: dict, name: str):
    databases = config.get("databases", [])
    db = next((d for d in databases if d["name"] == name), None)

    if not db:
        typer.echo(f"Database '{name}' not found. Run `backfup add` first.")
        raise typer.Exit(1)

    db_type = db.get("type", "postgres")
    connection_url = resolve_credential(db["connection_url"])

    typer.echo(f"Testing database: {name} ({db_type})\n")

    tester = _DB_TESTERS.get(db_type)
    if not tester:
        typer.echo(f"Error: unsupported database type '{db_type}'.")
        raise typer.Exit(1)

    tester(connection_url)
    typer.echo(f"\nDatabase '{name}' is reachable.")


# ─── Command ──────────────────────────────────────────────────────────────────

def test_command(
    storage: bool = typer.Option(False, "--storage", help="Test storage connectivity and permissions"),
    database: Optional[str] = typer.Option(None, "--database", help="Test database connection by name (e.g. appdb)"),
):
    if not storage and not database:
        typer.echo("Specify what to test. Available options: --storage, --database <n>")
        raise typer.Exit(1)

    store = ConfigStore()

    if not store.exists():
        typer.echo("No configuration found. Run `backfup init` first.")
        raise typer.Exit(1)

    config = store.load()

    if storage:
        _test_storage(config)

    if database:
        if storage:
            typer.echo("")
        _test_database(config, database)