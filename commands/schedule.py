import shutil
import sys
from datetime import datetime, timezone
from typing import Optional

import typer

from config.store import ConfigStore

# ─── Interval → cron conversion ───────────────────────────────────────────────

_INTERVAL_MAP = {
    "30m":    "*/30 * * * *",
    "1h":     "0 * * * *",
    "2h":     "0 */2 * * *",
    "3h":     "0 */3 * * *",
    "6h":     "0 */6 * * *",
    "12h":    "0 */12 * * *",
    "24h":    "0 2 * * *",
    "daily":  "0 2 * * *",
    "weekly": "0 2 * * 0",
}

_INTERVAL_LABELS = {v: k for k, v in _INTERVAL_MAP.items()}


def _every_to_cron(every: str) -> str:
    cron = _INTERVAL_MAP.get(every.lower())
    if not cron:
        supported = ", ".join(_INTERVAL_MAP.keys())
        typer.echo(f"Error: unrecognised interval '{every}'.")
        typer.echo(f"Supported values: {supported}")
        typer.echo("For anything else use --cron directly.")
        raise typer.Exit(1)
    return cron


def _cron_label(cron: str) -> str:
    return _INTERVAL_LABELS.get(cron, cron)


# ─── Cron line builder ────────────────────────────────────────────────────────

def _backfup_bin() -> str:
    """Return the path to the running backfup executable."""
    return shutil.which("backfup") or sys.executable + " main.py"


def _cron_line(db_name: str, cron: str) -> str:
    return f"{cron} {_backfup_bin()} backup {db_name}"


# ─── Config helpers ───────────────────────────────────────────────────────────

def _load_schedules(config: dict) -> list:
    return config.get("schedules", [])


def _save_schedules(config: dict, schedules: list, store: ConfigStore):
    config["schedules"] = schedules
    store.save(config)


# ─── Command implementations ──────────────────────────────────────────────────

def schedule_create(name: str, cron: str, store: ConfigStore, config: dict):
    schedules = _load_schedules(config)

    existing = next((s for s in schedules if s["database"] == name), None)
    if existing:
        typer.echo(f"A schedule already exists for '{name}'.")
        typer.echo(f"Use `backfup schedule {name} edit` to update it.")
        raise typer.Exit(1)

    schedules.append({
        "database": name,
        "cron": cron,
        "label": _cron_label(cron),
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    _save_schedules(config, schedules, store)

    line = _cron_line(name, cron)

    typer.echo("Schedule created\n")
    typer.echo(f"  database  → {name}")
    typer.echo(f"  interval  → {_cron_label(cron)}")
    typer.echo(f"  cron      → {cron}")
    typer.echo(f"\nAdd this to your crontab (crontab -e):\n")
    typer.echo(f"  {line}\n")


def schedule_edit(name: str, cron: str, store: ConfigStore, config: dict):
    schedules = _load_schedules(config)

    existing = next((s for s in schedules if s["database"] == name), None)
    if not existing:
        typer.echo(f"No schedule found for '{name}'.")
        typer.echo(f"Use `backfup schedule {name} --every <interval>` to create one.")
        raise typer.Exit(1)

    old_cron = existing["cron"]
    existing["cron"] = cron
    existing["label"] = _cron_label(cron)
    _save_schedules(config, schedules, store)

    line = _cron_line(name, cron)

    typer.echo("Schedule updated\n")
    typer.echo(f"  database  → {name}")
    typer.echo(f"  old cron  → {old_cron}")
    typer.echo(f"  new cron  → {cron}")
    typer.echo(f"  interval  → {_cron_label(cron)}")
    typer.echo(f"\nUpdate your crontab (crontab -e) to:\n")
    typer.echo(f"  {line}\n")


def schedule_remove(name: str, store: ConfigStore, config: dict):
    schedules = _load_schedules(config)

    existing = next((s for s in schedules if s["database"] == name), None)
    if not existing:
        typer.echo(f"No schedule found for '{name}'.")
        raise typer.Exit(1)

    schedules = [s for s in schedules if s["database"] != name]
    _save_schedules(config, schedules, store)

    typer.echo(f"Schedule for '{name}' removed from config.")
    typer.echo(f"\nRemember to also remove it from your crontab (crontab -e):\n")
    typer.echo(f"  {_cron_line(name, existing['cron'])}\n")


def schedule_list(store: ConfigStore, config: dict):
    schedules = _load_schedules(config)

    if not schedules:
        typer.echo("No schedules configured.")
        typer.echo("Use `backfup schedule <database> --every <interval>` to create one.")
        raise typer.Exit(0)

    typer.echo("Active schedules\n")
    for s in schedules:
        created = s.get("created_at", "unknown")
        try:
            created = datetime.fromisoformat(created).strftime("%Y-%m-%d %H:%M UTC")
        except Exception:
            pass

        typer.echo(f"  {s['database']}")
        typer.echo(f"    interval  → {s['label']}")
        typer.echo(f"    cron      → {s['cron']}")
        typer.echo(f"    created   → {created}")
        typer.echo(f"    command   → {_cron_line(s['database'], s['cron'])}")
        typer.echo("")

    typer.echo(f"  {len(schedules)} schedule(s) total")


# ─── Typer entry points ───────────────────────────────────────────────────────

def schedule_create_command(
    name: str = typer.Argument(..., help="Database name as registered with `backfup add`"),
    every: Optional[str] = typer.Option(None, "--every", help="Human-friendly interval: 30m, 1h, 6h, 12h, daily, weekly."),
    cron: Optional[str] = typer.Option(None, "--cron", help='Raw cron expression e.g. "0 */6 * * *".'),
):
    """Create a backup schedule for a database."""
    if every and cron:
        typer.echo("Use either --every or --cron, not both.")
        raise typer.Exit(1)
    if not every and not cron:
        typer.echo("Specify a schedule with --every <interval> or --cron <expression>.")
        raise typer.Exit(1)

    store = ConfigStore()
    if not store.exists():
        typer.echo("No configuration found. Run `backfup init` first.")
        raise typer.Exit(1)

    config = store.load()

    db = next((d for d in config.get("databases", []) if d["name"] == name), None)
    if not db:
        typer.echo(f"Database '{name}' not found. Run `backfup add` first.")
        raise typer.Exit(1)

    resolved_cron = _every_to_cron(every) if every else cron
    schedule_create(name, resolved_cron, store, config)


def schedule_edit_command(
    name: str = typer.Argument(..., help="Database name."),
    every: Optional[str] = typer.Option(None, "--every", help="New interval: 30m, 1h, 6h, 12h, daily, weekly."),
    cron: Optional[str] = typer.Option(None, "--cron", help='New raw cron expression.'),
):
    """Update the timing of an existing schedule."""
    if every and cron:
        typer.echo("Use either --every or --cron, not both.")
        raise typer.Exit(1)
    if not every and not cron:
        typer.echo("Specify a new timing with --every or --cron.")
        raise typer.Exit(1)

    store = ConfigStore()
    if not store.exists():
        typer.echo("No configuration found. Run `backfup init` first.")
        raise typer.Exit(1)

    config = store.load()
    resolved_cron = _every_to_cron(every) if every else cron
    schedule_edit(name, resolved_cron, store, config)


def schedule_remove_command(
    name: str = typer.Argument(..., help="Database name."),
):
    """Remove a backup schedule."""
    store = ConfigStore()
    if not store.exists():
        typer.echo("No configuration found. Run `backfup init` first.")
        raise typer.Exit(1)

    config = store.load()
    schedule_remove(name, store, config)


def schedule_list_command():
    """List all active backup schedules."""
    store = ConfigStore()
    if not store.exists():
        typer.echo("No configuration found. Run `backfup init` first.")
        raise typer.Exit(1)

    config = store.load()
    schedule_list(store, config)