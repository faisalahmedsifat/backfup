import gzip
import shutil
import subprocess
import threading
from datetime import datetime, timezone
from io import BytesIO
from typing import Optional

import boto3
import typer
from botocore.exceptions import ClientError
from loguru import logger

from config.store import ConfigStore
from utils.credentials import resolve_credential

# S3 multipart minimum part size is 5MB — use 6MB to stay safely above
CHUNK_SIZE = 6 * 1024 * 1024
TIMESTAMP_FORMAT = "%Y-%m-%d-%H-%M-%S"


# ─── S3 helpers ───────────────────────────────────────────────────────────────

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


# ─── Dump commands ────────────────────────────────────────────────────────────

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
        # mongodump streams to stdout with --archive flag
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


# ─── Streaming upload ─────────────────────────────────────────────────────────

def _stream_to_s3(
    process: subprocess.Popen,
    client,
    bucket: str,
    key: str,
    compress: bool = True,
) -> int:
    """
    Read from process stdout, optionally gzip-compress, and upload to S3
    via multipart upload. Returns total bytes uploaded.
    """
    mpu = client.create_multipart_upload(Bucket=bucket, Key=key)
    upload_id = mpu["UploadId"]
    parts = []
    part_number = 1
    total_bytes = 0

    try:
        buffer = BytesIO()

        if compress:
            gz = gzip.GzipFile(fileobj=buffer, mode="wb")
        else:
            gz = None

        def flush_part():
            nonlocal part_number, total_bytes
            buffer.seek(0)
            data = buffer.read()
            if not data:
                return
            response = client.upload_part(
                Bucket=bucket,
                Key=key,
                UploadId=upload_id,
                PartNumber=part_number,
                Body=data,
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

        # Finalise gzip stream
        if gz:
            gz.close()

        # Flush remaining data — S3 allows the last part to be < 5MB
        if buffer.tell() > 0:
            flush_part()

        process.wait()
        stderr_output = process.stderr.read().decode("utf-8", errors="replace").strip()

        if process.returncode != 0:
            client.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_id)
            typer.echo(f"\nError: dump process failed.\n{stderr_output}")
            raise typer.Exit(1)

        client.complete_multipart_upload(
            Bucket=bucket,
            Key=key,
            UploadId=upload_id,
            MultipartUpload={"Parts": parts},
        )
        return total_bytes

    except (ClientError, Exception) as e:
        client.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_id)
        typer.echo(f"\nError: upload failed — {e}")
        raise typer.Exit(1)


# ─── Command ──────────────────────────────────────────────────────────────────

def backup_command(
    name: str = typer.Argument(..., help="Database name as registered with `backfup add`"),
):
    store = ConfigStore()

    if not store.exists():
        typer.echo("No configuration found. Run `backfup init` first.")
        raise typer.Exit(1)

    config = store.load()

    # Resolve database
    databases = config.get("databases", [])
    db = next((d for d in databases if d["name"] == name), None)
    if not db:
        typer.echo(f"Database '{name}' not found. Run `backfup add` first.")
        raise typer.Exit(1)

    # Resolve storage
    storage = config.get("storage")
    if not storage:
        typer.echo("No storage configured. Run `backfup init` first.")
        raise typer.Exit(1)

    connection_url = resolve_credential(db["connection_url"])
    db_type = db.get("type", "postgres")
    bucket = storage["bucket"]

    timestamp = datetime.now(timezone.utc).strftime(TIMESTAMP_FORMAT)
    key = _s3_key(name, timestamp)

    # MongoDB uses its own gzip via --archive --gzip, others we compress ourselves
    compress = db_type != "mongodb"

    typer.echo(f"Starting backup\n")
    typer.echo(f"  database  → {name} ({db_type})")
    typer.echo(f"  storage   → {storage['endpoint']} / {bucket}")
    typer.echo(f"  key       → {key}\n")

    # Step 1: start dump process
    typer.echo("Dumping database...", nl=False)
    try:
        process = _start_dump(db, connection_url)
        typer.echo(" started")
    except Exception as e:
        typer.echo(f" FAILED\nError: {e}")
        raise typer.Exit(1)

    # Step 2 + 3: compress and stream to S3 simultaneously
    typer.echo("Compressing and uploading...", nl=False)
    client = _get_s3_client(storage)

    size_bytes = _stream_to_s3(process, client, bucket, key, compress=compress)
    size_kb = size_bytes / 1024

    typer.echo(" done")

    typer.echo(f"""
Backup complete

  location  → s3://{bucket}/{key}
  size      → {size_kb:.1f} KB
  timestamp → {timestamp}
""")