# backfup

**backfup** is a developer-focused CLI for database backups and exploration. It automates the full backup lifecycle — dump, compress, upload — while also giving you a local browser-based interface to explore your data.

```
backfup init
backfup add --name app "$DATABASE_URL"
backfup backup app
backfup studio app
```

---

## Why backfup

Database backups are usually held together with shell scripts:

```bash
pg_dump | gzip | aws s3 cp ...
```

This leads to fragile scripts, inconsistent naming, hard restore workflows, and a setup that no one on the team fully understands. `backfup` replaces all of that with a single unified tool.

And while you're at it — actually looking at your data shouldn't require installing a native GUI app or paying for a SaaS. `backfup studio` opens a zero-config browser interface directly from your registered databases.

---

## Install

```bash
pip install backfup
```

### Dependencies

| Feature | Requires |
|---|---|
| PostgreSQL backup/restore | `pg_dump`, `psql` on PATH |
| Studio | `fastapi`, `uvicorn`, `psycopg2-binary` |

```bash
uv add fastapi uvicorn psycopg2-binary
```

---

## Quickstart

### 1. Configure storage

```bash
backfup init
```

Interactive prompts will ask for your S3 endpoint, bucket, region, and credentials. Credentials can be stored directly or as environment variable references.

Non-interactive:

```bash
backfup init \
  --endpoint https://s3.amazonaws.com \
  --bucket prod-backups \
  --region us-east-1 \
  --from-env \
  --access-key-env BACKFUP_ACCESS_KEY \
  --secret-key-env BACKFUP_SECRET_KEY
```

This produces `~/.backfup/config.yaml`:

```yaml
storage:
  endpoint: https://s3.amazonaws.com
  bucket: prod-backups
  region: us-east-1
  access_key: ENV("BACKFUP_ACCESS_KEY")
  secret_key: ENV("BACKFUP_SECRET_KEY")
```

### 2. Register a database

```bash
backfup add postgres://user:pass@localhost:5432/mydb --name app
```

Or via environment variable:

```bash
backfup add --name app --from-env DATABASE_URL
```

### 3. Verify everything works

```bash
backfup test --storage
backfup test --database app
```

### 4. Run a backup

```bash
backfup backup app
```

```
Starting backup

Dumping database... done
Compressing... done
Uploading to storage... done

Backup complete
s3://prod-backups/backfup/app/2026-03-12-14-20-00.sql.gz
```

### 5. Explore your data

```bash
backfup studio app
```

```
  backfup studio → http://127.0.0.1:4242
  database        → app
  press Ctrl+C to stop
```

---

## Commands

### `backfup init`

Configures S3-compatible storage. Supports interactive and non-interactive modes.

| Flag | Description |
|---|---|
| `--endpoint` | S3 endpoint URL |
| `--bucket` | Bucket name |
| `--region` | Region |
| `--access-key` | Direct access key |
| `--secret-key` | Direct secret key |
| `--from-env` | Read credentials from environment variables |
| `--access-key-env` | Env var name for access key (use with `--from-env`) |
| `--secret-key-env` | Env var name for secret key (use with `--from-env`) |
| `--force` | Overwrite existing configuration |

---

### `backfup add <connection-url>`

Registers a database for backup and studio access.

```bash
backfup add postgres://user:pass@localhost:5432/app --name app
backfup add --name app --from-env DATABASE_URL
```

| Flag | Description |
|---|---|
| `--name` | Alias for this database (auto-generated if omitted) |
| `--from-env` | Read connection URL from this environment variable |
| `--force` | Overwrite existing entry with the same name |

If `--name` is not provided, a random 6-character case-sensitive ID is assigned (e.g. `aB3kXz`).

---

### `backfup test`

Verifies your configuration is working.

```bash
backfup test --storage        # tests S3 connectivity and write permissions
backfup test --database app   # tests database connection and pg_dump availability
backfup test --storage --database app   # runs both
```

---

### `backfup backup <name>`

Runs a full backup: dump → gzip → upload.

```bash
backfup backup app
```

Backups are stored at:

```
<bucket>/backfup/<database>/<timestamp>.sql.gz
```

Example:

```
prod-backups/backfup/app/2026-03-12-14-20-00.sql.gz
```

Timestamps follow `YYYY-MM-DD-HH-MM-SS` for natural sort order.

---

### `backfup list <name>`

Lists all backups stored for a database.

```bash
backfup list app
```

```
Available backups

1. 2026-03-12 14:20
2. 2026-03-11 03:00
3. 2026-03-10 03:00
```

---

### `backfup restore <name>`

Downloads and restores a selected backup.

```bash
backfup restore app
```

```
Select backup

1) 2026-03-12 14:20
2) 2026-03-11 03:00
3) 2026-03-10 03:00
```

---

### `backfup studio <name>`

Launches a local browser-based database explorer.

```bash
backfup studio app
backfup studio app --port 8080
```

| Flag | Description |
|---|---|
| `--port` | Port to bind to (default: `4242`) |
| `--host` | Host to bind to (default: `127.0.0.1`) |

**Features:**
- Table browser with row counts
- Per-column filtering with 300ms debounce
- Sort by any column
- Row detail panel with full field values
- Foreign key navigation — click to follow relationships
- JSON column rendering
- Raw SQL editor with query history (`⌘↵` to run)
- Schema diagram (ERD) inferred from foreign keys
- Global search across all tables
- CSV export with active filters applied

---

## Configuration

`backfup` stores all configuration at `~/.backfup/config.yaml`.

```yaml
storage:
  endpoint: http://localhost:9000
  bucket: backfup-dev
  region: us-east-1
  access_key: ENV("BACKFUP_ACCESS_KEY")
  secret_key: ENV("BACKFUP_SECRET_KEY")

databases:
  - name: app
    type: postgres
    connection_url: ENV("DATABASE_URL")
```

Values wrapped in `ENV("...")` are resolved from the environment at runtime — the actual secrets never touch disk.

---

## Storage providers

Any S3-compatible endpoint works. Supply the endpoint URL directly — no provider-specific configuration needed.

| Provider | Endpoint format |
|---|---|
| AWS S3 | `https://s3.amazonaws.com` |
| Cloudflare R2 | `https://<account>.r2.cloudflarestorage.com` |
| MinIO | `http://localhost:9000` |
| DigitalOcean Spaces | `https://<region>.digitaloceanspaces.com` |
| Wasabi | `https://s3.<region>.wasabisys.com` |

---

## Local development setup

A ready-to-use `docker-compose.yml` is included for local development with MinIO and PostgreSQL:

```bash
docker compose up
```

Then:

```bash
backfup init \
  --endpoint http://localhost:9000 \
  --bucket backfup-dev \
  --region us-east-1 \
  --access-key minioadmin \
  --secret-key minioadmin

backfup add postgres://backfup:backfup@localhost:5432/appdb --name appdb
backfup test --storage --database appdb
backfup studio appdb
```

---

## Supported databases

| Database | Dump tool | Status |
|---|---|---|
| PostgreSQL | `pg_dump` / `psql` | ✅ MVP |
| MySQL | `mysqldump` / `mysql` | Planned |
| MongoDB | `mongodump` / `mongorestore` | Planned |

---

## Roadmap

- Scheduled backups (`backfup schedule app daily`)
- Retention policies (keep last N backups)
- Client-side encryption
- Multi-database backup (`backfup backup --all`)
- Backup metadata (size, duration, row count)