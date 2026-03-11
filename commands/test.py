import typer
import boto3
from botocore.exceptions import ClientError, EndpointResolutionError
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


def test_command(
    storage: bool = typer.Option(False, "--storage", help="Test storage connectivity and permissions"),
):
    if not storage:
        typer.echo("Specify what to test. Available options: --storage")
        raise typer.Exit(1)

    store = ConfigStore()

    if not store.exists():
        typer.echo("No configuration found. Run `backfup init` first.")
        raise typer.Exit(1)

    config = store.load()
    storage_config = config.get("storage")

    if not storage_config:
        typer.echo("No storage configured. Run `backfup init` first.")
        raise typer.Exit(1)

    bucket = storage_config["bucket"]
    typer.echo(f"Testing storage: {storage_config['endpoint']} / {bucket}\n")

    # Step 1: resolve credentials
    typer.echo("Resolving credentials...", nl=False)
    try:
        client = _get_s3_client(storage_config)
        typer.echo(" OK")
    except KeyError as e:
        typer.echo(f" FAILED\nError: environment variable {e} is not set.")
        raise typer.Exit(1)

    # Step 2: verify bucket access
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

    # Step 3: verify write permissions via probe upload
    typer.echo("Checking write permissions...", nl=False)
    try:
        client.put_object(Bucket=bucket, Key=PROBE_KEY, Body=PROBE_CONTENT)
        typer.echo(" OK")
    except ClientError as e:
        typer.echo(f" FAILED\nError: {e}")
        raise typer.Exit(1)

    # Step 4: clean up probe file
    typer.echo("Cleaning up probe file...", nl=False)
    try:
        client.delete_object(Bucket=bucket, Key=PROBE_KEY)
        typer.echo(" OK")
    except ClientError as e:
        typer.echo(f" WARNING\nCould not delete probe file ({PROBE_KEY}): {e}")

    typer.echo("\nStorage is configured correctly.")