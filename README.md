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
| MongoDB backup/restore | `mongodump`, `mongorestore` on PATH |
| MySQL backup/restore | `mysqldump`, `mysql` on PATH |
| Studio | `fastapi`, `uvicorn`, `psycopg2-binary` |

```bash
uv add fastapi uvicorn psycopg2-binary pymongo
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

Database type is auto-detected from the connection URL scheme — `postgres://`, `mysql://`, `mongodb://`.

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

  database  → app (postgres)
  storage   → https://s3.amazonaws.com / prod-backups
  id        → aB3kXz
  key       → backfup/app/2026-03-12-14-20-00.sql.gz

Dumping database... started
Compressing and uploading... done

Backup complete

  location  → s3://prod-backups/backfup/app/2026-03-12-14-20-00.sql.gz
  size      → 142.3 KB
  timestamp → 2026-03-12-14-20-00
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

Registers a database for backup and studio access. Database type is auto-detected from the URL scheme.

```bash
backfup add postgres://user:pass@localhost:5432/app --name app
backfup add mongodb://user:pass@localhost:27017/app?authSource=admin --name mongo
backfup add --name app --from-env DATABASE_URL
```

| Flag | Description |
|---|---|
| `--name` | Alias for this database (auto-generated if omitted) |
| `--from-env` | Read connection URL from this environment variable |
| `--force` | Overwrite existing entry with the same name |

If `--name` is not provided, a random 6-character case-sensitive ID is assigned (e.g. `aB3kXz`).

---

### `backfup list`

Lists all registered databases.

```bash
backfup list
```

```
Registered databases

  app
    type  → postgres
    url   → postgres://user:***@localhost:5432/app

  mongo
    type  → mongodb
    url   → mongodb://user:***@localhost:27017/app?authSource=admin

  2 database(s) total
```

---

### `backfup test`

Verifies your configuration is working.

```bash
backfup test --storage              # tests S3 connectivity and write permissions
backfup test --database app         # tests database connection and dump tool availability
backfup test --storage --database app   # runs both
```

---

### `backfup backup <name>`

Runs a full backup: dump → gzip → upload. Each backup gets a unique short ID alongside its timestamp.

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

Timestamps follow `YYYY-MM-DD-HH-MM-SS` for natural sort order. The backup ID (e.g. `aB3kXz`) is stored as S3 object metadata and used for direct restore.

---

### `backfup backup list <name>`

Lists all backups stored for a database.

```bash
backfup backup list app
```

```
Available backups for 'app'

  1) [aB3kXz]  2026-03-12 14:20:00 UTC  (142.3 KB)
  2) [mQ7pXr]  2026-03-11 03:00:00 UTC  (138.1 KB)
  3) [xR2nWs]  2026-03-10 03:00:00 UTC  (135.7 KB)

  3 backup(s) total
```

---

### `backfup restore <name>`

Downloads and restores a backup. Pass `--id` to restore directly, or omit it to select interactively.

```bash
backfup restore app --id aB3kXz     # direct restore by ID
backfup restore app                  # interactive selection
```

```
Select a backup to restore for 'app'

  1) [aB3kXz]  2026-03-12 14:20:00 UTC
  2) [mQ7pXr]  2026-03-11 03:00:00 UTC

Enter number: 1

Downloading... done
Restoring... done

Restore complete.
```

| Flag | Description |
|---|---|
| `--id` | Backup ID to restore directly (from `backfup backup list`) |

---

### `backfup storage list`

Shows storage configuration and a summary of all backups in the bucket grouped by database.

```bash
backfup storage list
```

```
Storage configuration

  endpoint  → http://localhost:9000
  bucket    → backfup-dev
  region    → us-east-1

Bucket contents

  app/      (3 backup(s), 416.1 KB)
  mongo/    (1 backup(s), 28.4 KB)

  total → 444.5 KB across 2 database(s)
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
  - name: mongo
    type: mongodb
    connection_url: mongodb://backfup:backfup@localhost:27017/appdb?authSource=admin
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

A ready-to-use `docker-compose.yml` is included with MinIO, PostgreSQL, and MongoDB:

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
backfup add mongodb://backfup:backfup@localhost:27017/appdb?authSource=admin --name mongo

backfup test --storage --database appdb
backfup studio appdb
```

---

## Supported databases

| Database | Dump tool | Test | Status |
|---|---|---|---|
| PostgreSQL | `pg_dump` / `psql` | `psql` | ✅ MVP |
| MongoDB | `mongodump` / `mongorestore` | `pymongo` | ✅ MVP |
| MySQL | `mysqldump` / `mysql` | `mysql` | Planned |

---

## Roadmap

- Scheduled backups (`backfup schedule app --every 6h`)
- Retention policies (keep last N backups)
- Client-side encryption
- Multi-database backup (`backfup backup --all`)
- Backup metadata (size, duration, row count)