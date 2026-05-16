# 🗃️ backfup

**Postgres backup CLI — wrap `pg_dump`/`pg_restore` with multi-database and S3 support.**

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

---

`backfup` is a lightweight TypeScript CLI that shells out to the official `pg_dump` and `pg_restore` tools, adding multi-database batching, local compressed storage, and optional S3 uploads. It requires only `pg_dump`/`pg_restore` to be installed on the host — no database drivers, no native modules.

Built for [Bun](https://bun.sh) (>=1.1.0).

## Quick Start

```bash
npm install -g backfup

# First-time setup
backfup init

# Back up all configured databases
backfup backup

# Or back up a single database ad-hoc
backfup backup --url postgresql://user:pass@host:5432/mydb
```

## Prerequisites

- **Bun >= 1.1.0** (or Node.js >= 18)
- **PostgreSQL client tools** — `pg_dump` and `pg_restore` ***must be >= your server version***

### PostgreSQL Client Setup

`backfup` shells out to `pg_dump` and `pg_restore`. These **must be at least as new as your PostgreSQL server**. For example, if your server is PostgreSQL 18, a v14 client will fail with a version mismatch error.

#### Debian / Ubuntu

```bash
# 1. Add the PostgreSQL APT repository (one-time setup)
sudo sh -c 'echo "deb http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" > /etc/apt/sources.list.d/pgdg.list'
curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | sudo gpg --dearmor -o /etc/apt/trusted.gpg.d/pgdg.gpg

# 2. Install the client matching your server version (e.g. 18 -> postgresql-client-18)
sudo apt update && sudo apt install postgresql-client-18

# 3. Verify
pg_dump --version
```

> Available packages: `postgresql-client-14` through `postgresql-client-18`. Replace `18` with your server's major version.

#### macOS

```bash
brew install libpq
brew link --force libpq
pg_dump --version
```

#### Docker

```dockerfile
FROM oven/bun:1
ARG PG_VERSION=18
RUN apt update && apt install -y curl gpg
RUN sh -c 'echo "deb http://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" > /etc/apt/sources.list.d/pgdg.list'
RUN curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | gpg --dearmor -o /etc/apt/trusted.gpg.d/pgdg.gpg
RUN apt update && apt install -y postgresql-client-${PG_VERSION}
RUN npm install -g backfup
ENTRYPOINT ["backfup"]
```

#### How version checking works

`backfup backup` queries the server version **before** attempting a dump. If your local `pg_dump` is older than the server, it fails immediately with a clear error:

```
✖ Version mismatch for "app": server is PostgreSQL 18.3, but local pg_dump is 14.22.
pg_dump must be >= the server version. Install PostgreSQL 18 client:

  # Debian/Ubuntu:
  sudo apt install postgresql-client-18
  # macOS:
  brew upgrade libpq
```

The version number in the install instructions is always the **actual server version** detected at runtime — no hardcoded assumptions. No more 178-byte files and mystery hangs.

## Commands

### `backfup init`

Interactive setup wizard that creates `~/.backfup/config.json`:

```bash
backfup init
```

- Add one or more database connection URLs (supports `$VAR` / `${VAR}` env references)
- Choose a local backup directory (default: `~/.backfup/backups/`)
- Optionally configure S3 for remote storage

### `backfup backup`

Back up databases to local disk, with optional S3 upload:

```bash
# Back up all databases defined in config
backfup backup

# Back up a specific named database from config
backfup backup --name app

# Ad-hoc backup (no config needed)
backfup backup --url postgresql://user:pass@host:5432/mydb

# Back up and upload to S3
backfup backup --s3

# Use a custom config file
backfup backup --config ./project-backfup.json
```

Backup files are named `{name}-{YYYYMMDD-HHmmss}.dump.gz` and stored in the configured backup directory.

### `backfup restore`

Restore from a local file or an S3 URI:

```bash
# From a local backup
backfup restore app-20260516-170000.dump.gz --url postgresql://user:pass@host:5432/mydb

# From S3
backfup restore s3://my-bucket/db-backups/app-20260516-170000.dump.gz --url postgresql://...

# Skip confirmation (for automation)
backfup restore backup.dump.gz --url ... --yes
```

The restore process drops and recreates database objects (`--clean --if-exists`). A confirmation prompt appears unless `--yes` is passed.

### `backfup list`

List backups in the local directory and optionally S3:

```bash
# Local backups only
backfup list

# Include S3
backfup list --s3

# Show last 5 only
backfup list --last 5

# JSON output
backfup list --json
```

## Configuration

Config lives at `~/.backfup/config.json` (override with `--config <path>`):

```json
{
  "databases": {
    "app": "$APP_DATABASE_URL",
    "analytics": "$ANALYTICS_DATABASE_URL"
  },
  "backupDir": "~/.backfup/backups",
  "s3": {
    "bucket": "my-backups",
    "region": "us-east-1",
    "prefix": "db-backups/",
    "accessKeyId": "AKIA...",
    "secretAccessKey": "...",
    "endpoint": "https://s3.fr-par.scw.cloud"
  }
}
```

### Environment variable interpolation

Connection URLs in the config support `$VAR` and `${VAR}` syntax. The actual credentials come from your environment at runtime:

```json
{
  "databases": {
    "app": "$APP_DATABASE_URL"
  }
}
```

```bash
export APP_DATABASE_URL=postgresql://user:pass@host:5432/mydb
backfup backup
```

### S3 credentials

You can provide S3 credentials explicitly in the config, or leave `accessKeyId` / `secretAccessKey` blank to rely on the default AWS credential chain (environment variables, IAM roles, `~/.aws/credentials`, etc.).

The `@aws-sdk/client-s3` package is an optional dependency — it's only imported when you use `--s3` flags or `s3://` URIs.

## Global Flags

| Flag | Description |
|------|-------------|
| `--quiet` | Suppress all output except errors |
| `--json` | Output results as JSON |
| `--no-color` | Disable colored output |
| `--yes` | Skip confirmation prompts |
| `--verbose` | Show detailed debug output |
| `--version` | Show version |

## Design Principles

- **No lock-in**: Backups are standard `pg_dump --format=custom` compressed with gzip. Restore them with plain `pg_restore` without this CLI.
- **Local-first**: S3 is optional. Local backups work with zero external dependencies beyond `pg_dump`.
- **Streaming**: Backups are streamed through gzip directly to disk — no buffering the entire dump in memory.
- **Deterministic naming**: `{name}-{YYYYMMDD-HHmmss}.dump.gz` — sortable, human-readable, scriptable.

## License

MIT
