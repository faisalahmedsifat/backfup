import gzip
import random
import shutil
import string
import subprocess
from datetime import datetime, timezone
from io import BytesIO

import boto3
import typer
from botocore.exceptions import ClientError

from config.store import ConfigStore
from utils.credentials import resolve_credential

CHUNK_SIZE = 6 * 1024 * 1024
TIMESTAMP_FORMAT = "%Y-%m-%d-%H-%M-%S"


def _generate_id(length: int = 6) -> str:
    charset = string.ascii_letters + string.digits
    return "".join(random.choices(charset, k=length))


def _get_s3_client(storage: dict):
    return boto3.client(
        "s3",
        endpoint_url=storage["endpoint"],
        region_name=storage["region"],
        aws_access_key_id=resolve_credential(storage["access_key"]),
        aws_secret_access_key=resolve_credential(storage["secret_key"]),
    )


def _s3_key(db_name: str, timestamp: str) -> str:
    return f"backfup/{db_name}/{timestamp}.sql.gz"


def _start_dump(db: dict, connection_url: str) -> subprocess.Popen:
    db_type = db.get("type", "postgres")

    if db_type == "postgres":
        if not shutil.which("pg_dump"):
            typer.echo("Error: pg_dump not found. Install PostgreSQL client tools.")
            raise typer.Exit(1)
        cmd = ["pg_dump", "--no-password", connection_url]

    elif db_type == "mongodb":
        if not shutil.which("mongodump"):
            typer.echo("Error: mongodump not found. Install MongoDB Database Tools.")
            raise typer.Exit(1)
        cmd = ["mongodump", f"--uri={connection_url}", "--archive", "--gzip"]

    elif db_type == "mysql":
        if not shutil.which("mysqldump"):
            typer.echo("Error: mysqldump not found. Install MySQL client tools.")
            raise typer.Exit(1)
        cmd = ["mysqldump", connection_url]

    else:
        typer.echo(f"Error: unsupported database type '{db_type}'.")
        raise typer.Exit(1)

    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _stream_to_s3(process, client, bucket, key, backup_id, compress=True) -> int:
    mpu = client.create_multipart_upload(
        Bucket=bucket, Key=key, Metadata={"backfup-id": backup_id}
    )
    upload_id = mpu["UploadId"]
    parts = []
    part_number = 1
    total_bytes = 0

    try:
        buffer = BytesIO()
        gz = gzip.GzipFile(fileobj=buffer, mode="wb") if compress else None

        def flush_part():
            nonlocal part_number, total_bytes
            buffer.seek(0)
            data = buffer.read()
            if not data:
                return
            response = client.upload_part(
                Bucket=bucket, Key=key, UploadId=upload_id,
                PartNumber=part_number, Body=data,
            )
            parts.append({"PartNumber": part_number, "ETag": response["ETag"]})
            total_bytes += len(data)
            part_number += 1
            buffer.seek(0)
            buffer.truncate(0)

        while True:
            chunk = process.stdout.read(CHUNK_SIZE)
            if not chunk:
                break
            if gz:
                gz.write(chunk)
                gz.flush()
            else:
                buffer.write(chunk)
            if buffer.tell() >= CHUNK_SIZE:
                flush_part()

        if gz:
            gz.close()
        if buffer.tell() > 0:
            flush_part()

        process.wait()
        stderr_output = process.stderr.read().decode("utf-8", errors="replace").strip()

        if process.returncode != 0:
            client.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_id)
            typer.echo(f"\nError: dump process failed.\n{stderr_output}")
            raise typer.Exit(1)

        client.complete_multipart_upload(
            Bucket=bucket, Key=key, UploadId=upload_id,
            MultipartUpload={"Parts": parts},
        )
        return total_bytes

    except (ClientError, Exception) as e:
        client.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_id)
        typer.echo(f"\nError: upload failed — {e}")
        raise typer.Exit(1)


def backup_run_command(
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

    connection_url = resolve_credential(db["connection_url"])
    db_type = db.get("type", "postgres")
    bucket = storage["bucket"]

    timestamp = datetime.now(timezone.utc).strftime(TIMESTAMP_FORMAT)
    backup_id = _generate_id()
    key = _s3_key(name, timestamp)
    compress = db_type != "mongodb"

    typer.echo("Starting backup\n")
    typer.echo(f"  database  → {name} ({db_type})")
    typer.echo(f"  storage   → {storage['endpoint']} / {bucket}")
    typer.echo(f"  id        → {backup_id}")
    typer.echo(f"  key       → {key}\n")

    typer.echo("Dumping database...", nl=False)
    try:
        process = _start_dump(db, connection_url)
        typer.echo(" started")
    except Exception as e:
        typer.echo(f" FAILED\nError: {e}")
        raise typer.Exit(1)

    typer.echo("Compressing and uploading...", nl=False)
    client = _get_s3_client(storage)
    size_bytes = _stream_to_s3(process, client, bucket, key, backup_id=backup_id, compress=compress)
    size_kb = size_bytes / 1024
    typer.echo(" done")

    typer.echo(f"\nBackup complete\n")
    typer.echo(f"  location  → s3://{bucket}/{key}")
    typer.echo(f"  size      → {size_kb:.1f} KB")
    typer.echo(f"  timestamp → {timestamp}\n")