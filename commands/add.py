import random
import string
import typer
from typing import Optional

from config.store import ConfigStore
from utils.credentials import encode_env_ref


def _generate_id(length: int = 6) -> str:
    """Generate a random case-sensitive alphanumeric ID."""
    charset = string.ascii_letters + string.digits  # a-z, A-Z, 0-9
    return "".join(random.choices(charset, k=length))


def add_command(
    connection_url: Optional[str] = typer.Argument(
        None,
        help="Database connection URL (e.g. postgres://user:pass@localhost:5432/app)"
    ),
    name: Optional[str] = typer.Option(
        None,
        "--name",
        help="Alias for this database. Auto-generated if not provided."
    ),
    from_env: Optional[str] = typer.Option(
        None,
        "--from-env",
        help="Read connection URL from this environment variable name (e.g. DATABASE_URL)."
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Overwrite existing database entry with the same name."
    ),
):
    # Guard: must provide either connection_url or --from-env, but not both
    if connection_url and from_env:
        typer.echo("Cannot provide both a connection URL and --from-env. Choose one.")
        raise typer.Exit(1)

    if not connection_url and not from_env:
        typer.echo("A connection URL is required. Pass it as an argument or use --from-env DATABASE_URL.")
        raise typer.Exit(1)

    store = ConfigStore()
    config = store.load() if store.exists() else {}

    # Auto-generate name if not provided
    if not name:
        name = _generate_id()
        typer.echo(f"No name provided. Using auto-generated ID: {name}")

    # Guard: don't silently overwrite an existing entry
    databases = config.get("databases", [])
    existing = next((db for db in databases if db["name"] == name), None)
    if existing and not force:
        typer.echo(f"Database '{name}' already exists. Use --force to overwrite.")
        raise typer.Exit(1)

    # Encode connection string only if reading from env, otherwise store directly
    stored_connection_url = encode_env_ref(from_env) if from_env else connection_url

    entry = {
        "name": name,
        "type": "postgres",  #
        "connection_url": stored_connection_url,
    }

    # TODO: detect database type from connection URL prefix (postgres://, mysql://, mongodb://)

    # Replace existing entry or append
    if existing:
        config["databases"] = [entry if db["name"] == name else db for db in databases]
    else:
        config.setdefault("databases", []).append(entry)

    store.save(config)
    typer.echo(f"Database '{name}' added successfully.")