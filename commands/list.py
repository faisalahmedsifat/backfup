import typer
from urllib.parse import urlparse, urlunparse

from config.store import ConfigStore
from utils.credentials import is_env_ref


def _mask_url(url: str) -> str:
    try:
        parsed = urlparse(url)
        if parsed.password:
            masked = parsed._replace(
                netloc=parsed.netloc.replace(f":{parsed.password}@", ":***@")
            )
            return urlunparse(masked)
    except Exception:
        pass
    return url


def list_command():
    store = ConfigStore()

    if not store.exists():
        typer.echo("No configuration found. Run `backfup init` first.")
        raise typer.Exit(1)

    config = store.load()
    databases = config.get("databases", [])

    if not databases:
        typer.echo("No databases registered. Run `backfup add` first.")
        raise typer.Exit(0)

    typer.echo("Registered databases\n")
    for db in databases:
        url = db["connection_url"]
        display_url = url if is_env_ref(url) else _mask_url(url)
        typer.echo(f"  {db['name']}")
        typer.echo(f"    type  → {db['type']}")
        typer.echo(f"    url   → {display_url}")
        typer.echo("")

    typer.echo(f"  {len(databases)} database(s) total")