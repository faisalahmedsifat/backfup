# backfup

**backfup** is a developer-focused CLI that automates database backups and uploads them to **any S3-compatible storage**.

It removes the need for developers to maintain fragile scripts combining:

* `pg_dump`
* `gzip`
* `cron`
* `aws s3 cp`

Instead, backups become a simple CLI workflow.

Example usage:

```
backfup init
backfup add --name app "$DATABASE_URL"
backfup backup app
```

Backups are stored in object storage and can later be restored with a single command.

---

# Problem

Database backups are usually implemented using shell scripts such as:

```
pg_dump
gzip
aws s3 cp
cron
```

This leads to several issues:

* fragile scripts
* inconsistent backup naming
* difficult setup for new developers
* hard restore workflows
* storage configuration complexity

There is no unified interface for backup and restore operations.

---

# Goal

Provide a **minimal CLI tool** that handles:

1. dumping databases
2. compressing backups
3. uploading to S3-compatible storage
4. listing backups
5. restoring backups

A developer should be able to configure backups in **less than two minutes**.

---

# Core Principles

### Simple developer workflow

Commands should feel familiar and predictable.

Example:

```
backfup backup app
```

### Storage provider neutrality

Any S3-compatible object storage should work.

### Secure configuration

Secrets must support **environment variable references**.

### Automation friendly

Commands must support **non-interactive execution**.

---

# Supported Databases (MVP)

Initial version should support:

| Database   | Dump Tool   |
| ---------- | ----------- |
| PostgreSQL | `pg_dump`   |
| MySQL      | `mysqldump` |
| MongoDB    | `mongodump` |

MVP should begin with **PostgreSQL support only**.

---

# Storage Provider

Primary storage provider:

```
S3-compatible object storage
```

Examples:

* AWS S3
* Cloudflare R2
* MinIO
* DigitalOcean Spaces
* Wasabi

The user supplies an **endpoint URL**, not a provider name.

Example endpoints:

AWS

```
https://s3.amazonaws.com
```

Cloudflare R2

```
https://<account>.r2.cloudflarestorage.com
```

MinIO

```
http://localhost:9000
```

---

# Project Directory

When initialized, the project contains:

```
.backfup/
   config.json
```

This directory stores all backup configuration.

---

# Configuration Format

Example configuration file:

```json
{
  "storage": {
    "type": "s3",
    "endpoint": "https://s3.amazonaws.com",
    "bucket": "prod-backups",
    "region": "us-east-1",
    "accessKey": {
      "fromEnv": "BACKFUP_ACCESS_KEY"
    },
    "secretKey": {
      "fromEnv": "BACKFUP_SECRET_KEY"
    }
  },
  "databases": [
    {
      "name": "app",
      "type": "postgres",
      "connectionString": {
        "fromEnv": "DATABASE_URL"
      }
    }
  ]
}
```

Sensitive values should preferably reference environment variables.

---

# Backup Storage Structure

Backups are stored in the following path format:

```
<bucket>/backfup/<database>/<timestamp>.sql.gz
```

Example:

```
prod-backups/backfup/app/2026-03-11-14-20-00.sql.gz
```

Timestamp format:

```
YYYY-MM-DD-HH-MM-SS
```

This ensures backups remain naturally sortable.

---

# CLI Commands

## Initialize configuration

```
backfup init
```

Creates `.backfup/config.json`.

Supports **interactive and non-interactive modes**.

---

### Interactive example

```
backfup init
```

Prompts:

```
S3 endpoint URL
Bucket name
Region
Credential method
```

---

### Non-interactive example

```
backfup init \
  --endpoint https://s3.amazonaws.com \
  --bucket prod-backups \
  --region us-east-1 \
  --access-key-env BACKFUP_ACCESS_KEY \
  --secret-key-env BACKFUP_SECRET_KEY
```

Flags supported:

| Flag               | Description                     |
| ------------------ | ------------------------------- |
| `--endpoint`       | S3 endpoint                     |
| `--bucket`         | storage bucket                  |
| `--region`         | storage region                  |
| `--access-key`     | direct credential               |
| `--secret-key`     | direct credential               |
| `--access-key-env` | env reference                   |
| `--secret-key-env` | env reference                   |
| `--use-aws-env`    | use AWS environment credentials |
| `--force`          | overwrite existing config       |

---

## Add database

Registers a database to backup.

```
backfup add --name <name> <connection-string>
```

Example:

```
backfup add --name app postgres://user:pass@localhost:5432/app
```

Environment variable alternative:

```
backfup add --name app --from-env DATABASE_URL
```

---

## Test configuration

```
backfup test <database>
```

Checks:

* database connection
* required dump tool availability
* storage access

Example output:

```
Connecting to database
Connection successful

Checking pg_dump
Found

Checking storage access
Upload test successful
```

---

## Run backup

Creates a new backup.

```
backfup backup <database>
```

Process:

1. run database dump
2. compress backup
3. upload to storage
4. display results

Example:

```
Starting backup

Dumping database
Compressing backup
Uploading to storage

Backup completed

Location:
s3://prod-backups/backfup/app/2026-03-11-14-20.sql.gz
```

---

## List backups

```
backfup list <database>
```

Displays all backups stored for that database.

Example output:

```
Available backups

1. 2026-03-11 14:20
2. 2026-03-10 03:00
3. 2026-03-09 03:00
```

---

## Restore backup

Restores a selected backup.

```
backfup restore <database>
```

Example:

```
Select backup

1) 2026-03-11
2) 2026-03-10
3) 2026-03-09
```

The selected backup is downloaded and restored.

---

# Internal Backup Pipeline

The backup pipeline follows:

```
database dump
→ gzip compression
→ upload to storage
```

Conceptually:

```
pg_dump → gzip → S3 upload
```

Streaming should be used when possible to avoid writing large temporary files.

---

# System Requirements

Required database tools must exist on the system.

For PostgreSQL:

```
pg_dump
psql
```

For MySQL:

```
mysqldump
mysql
```

For MongoDB:

```
mongodump
mongorestore
```

The CLI should detect missing tools and fail early.

---

# User Flows

## Flow 1 — Initial setup

Developer installs `backfup`.

Run:

```
backfup init
```

Configure storage.

Configuration file is created.

---

## Flow 2 — Register database

Developer has a connection string.

Example:

```
DATABASE_URL=postgres://user:pass@localhost:5432/app
```

Command:

```
backfup add --name app "$DATABASE_URL"
```

Database is saved in config.

---

## Flow 3 — Verify configuration

Developer runs:

```
backfup test app
```

System verifies connectivity and storage access.

---

## Flow 4 — Create backup

Before running migrations:

```
backfup backup app
```

Backup is uploaded to storage.

---

## Flow 5 — View backups

Developer checks available backups:

```
backfup list app
```

Backup history is displayed.

---

## Flow 6 — Restore backup

If a migration fails:

```
backfup restore app
```

Developer selects a backup and restores the database.

---

# Error Handling

Examples:

### Missing dump tool

```
Error: pg_dump not found
Install PostgreSQL client tools
```

### Missing environment variable

```
Error: DATABASE_URL environment variable not set
```

### Storage connection failure

```
Error: unable to connect to storage endpoint
```

---

# MVP Scope

The first version should support:

Commands:

```
init
add
test
backup
list
restore
```

Database:

```
PostgreSQL
```

Storage:

```
S3-compatible endpoint
```

---

# Future Features

Possible extensions:

### Scheduled backups

```
backfup schedule app daily
```

### Retention policies

```
keep last 7 backups
```

### Encryption

Client-side encrypted backups.

### Multi-database backups

```
backfup backup --all
```

### Backup metadata

Include metadata such as:

* backup size
* timestamp
* database type

---

# Intended Outcome

`backfup` replaces workflows like:

```
cron
pg_dump
gzip
aws s3 cp
```

with a single unified interface:

```
backfup add "$DATABASE_URL"
backfup backup
backfup restore
```

The tool focuses on **speed, reliability, and minimal configuration**, making database backups accessible for developers without maintaining custom scripts.
