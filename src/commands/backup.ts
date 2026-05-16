import { join } from "path";
import { readFileSync } from "fs";
import { createLogger, LoggerOptions } from "../lib/logger.js";
import {
  loadConfig,
  getBackupDir,
  ensureDirectories,
  parseDbUrl,
  redactUrl,
  PgbackConfig,
} from "../lib/config.js";
import {
  runDump,
  getPgDumpVersion,
  getServerVersion,
  parseServerVersion,
} from "../lib/dump.js";
import { getS3, isS3Available } from "../lib/s3.js";

function formatTimestamp(): string {
  const now = new Date();
  const Y = now.getFullYear();
  const M = String(now.getMonth() + 1).padStart(2, "0");
  const D = String(now.getDate()).padStart(2, "0");
  const h = String(now.getHours()).padStart(2, "0");
  const m = String(now.getMinutes()).padStart(2, "0");
  const s = String(now.getSeconds()).padStart(2, "0");
  return `${Y}${M}${D}-${h}${m}${s}`;
}

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return (bytes / Math.pow(1024, i)).toFixed(1) + " " + units[i];
}

export function registerBackupCommand(program: any) {
  program
    .command("backup")
    .description(
      "Back up PostgreSQL databases to local disk (and optionally S3)",
    )
    .option("--url <connection>", "Ad-hoc: back up a single database by URL")
    .option("--name <name>", "Back up a specific named database from config")
    .option("--s3", "Upload backups to S3 after local dump")
    .option(
      "--config <path>",
      "Path to config file (default: ~/.backfup/config.json)",
    )
    .action(async (opts: any, cmd: any) => {
      const globalOpts = cmd.parent.opts() as LoggerOptions;
      const log = createLogger(globalOpts);

      // Check pg_dump availability and version
      const pgVer = getPgDumpVersion();
      if (!pgVer) {
        log.error(
          "pg_dump not found. Install the PostgreSQL client tools to proceed.",
        );
        log.newline();
        log.info("  # Debian/Ubuntu (replace 18 with your server version):");
        log.info(
          "  sudo sh -c 'echo \"deb http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main\" > /etc/apt/sources.list.d/pgdg.list'",
        );
        log.info(
          "  curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | sudo gpg --dearmor -o /etc/apt/trusted.gpg.d/pgdg.gpg",
        );
        log.info(
          "  sudo apt update && sudo apt install postgresql-client-<VERSION>",
        );
        log.newline();
        log.info("  # macOS:");
        log.info("  brew install libpq && brew link --force libpq");
        log.newline();
        log.info("  # Then verify: pg_dump --version");
        process.exit(1);
      }

      // Determine which databases to back up
      const databases: Record<string, string> = {};

      if (opts.url) {
        // Ad-hoc: --url was provided
        const parsed = parseDbUrl(opts.url);
        if (!parsed) {
          log.error(`Invalid connection URL: ${opts.url}`);
          log.info("Expected format: postgresql://user:pass@host:5432/dbname");
          process.exit(1);
        }
        databases["adhoc"] = opts.url;
      } else {
        // Load from config
        const config: PgbackConfig | null = loadConfig(opts.config);
        if (!config) {
          log.error(
            'No config found. Run "backfup init" first or use --url for ad-hoc backups.',
          );
          process.exit(1);
        }

        ensureDirectories(config);

        if (opts.name) {
          // Specific named database
          if (!config.databases[opts.name]) {
            log.error(`Database "${opts.name}" not found in config.`);
            log.info(`Available: ${Object.keys(config.databases).join(", ")}`);
            process.exit(1);
          }
          databases[opts.name] = config.databases[opts.name];
        } else {
          // All databases
          if (Object.keys(config.databases).length === 0) {
            log.error("No databases defined in config.");
            process.exit(1);
          }
          Object.assign(databases, config.databases);
        }
      }

      // Load config for backup dir and S3 settings
      const config: PgbackConfig | null = opts.url
        ? null
        : loadConfig(opts.config);
      const backupDir = config
        ? getBackupDir(config)
        : join(process.cwd(), "backfup-backups");
      const timestamp = formatTimestamp();

      // Check S3
      let useS3 = opts.s3;
      if (useS3 && !config?.s3) {
        log.error(
          '--s3 flag used but no S3 config found. Run "backfup init" to configure S3.',
        );
        process.exit(1);
      }
      if (useS3) {
        const available = await isS3Available();
        if (!available) {
          log.error("S3 support requires @aws-sdk/client-s3. Install it with:");
          log.info("  npm install @aws-sdk/client-s3");
          process.exit(1);
        }
      }

      // Pre-flight: check server versions against local pg_dump
      if (pgVer) {
        for (const [name, url] of Object.entries(databases)) {
          const serverRaw = getServerVersion(url);
          if (serverRaw) {
            const serverVer = parseServerVersion(serverRaw);
            if (serverVer && serverVer.major > pgVer.major) {
              log.error(
                `Version mismatch for "${name}": server is PostgreSQL ${serverVer.major}.${serverVer.minor}, but local pg_dump is ${pgVer.major}.${pgVer.minor}.`,
              );
              log.info(
                `pg_dump must be >= the server version. Install PostgreSQL ${serverVer.major} client:`,
              );
              log.newline();
              log.info("  # Debian/Ubuntu:");
              log.info(
                `  sudo apt install postgresql-client-${serverVer.major}`,
              );
              log.info("  # macOS:");
              log.info("  brew upgrade libpq");
              process.exit(1);
            }
          }
        }
      }

      log.newline();
      log.info("🗃️  Starting backup...");
      log.info(`   pg_dump ${pgVer?.raw ?? "unknown"}`);
      log.info(`   ${Object.keys(databases).length} database(s) to back up`);
      if (useS3) log.info("   S3 upload: enabled");
      log.newline();

      const results: Array<{
        name: string;
        file: string;
        size: number;
        s3Key?: string;
      }> = [];
      let errors = 0;

      for (const [name, url] of Object.entries(databases)) {
        const filename = `${name}-${timestamp}.dump.gz`;
        const filepath = join(backupDir, filename);

        const spinner = log.spinner(
          `Backing up "${name}" (${redactUrl(url)})...`,
        );
        spinner.start();

        try {
          const size = await runDump(url, filepath);
          spinner.succeed();
          log.info(`   ${filename}  ${formatBytes(size)}`);

          let s3Key: string | undefined;

          if (useS3 && config?.s3) {
            const s3Spinner = log.spinner(`   ↳ Uploading to S3...`);
            s3Spinner.start();
            try {
              const s3 = await getS3(config.s3);
              const prefix = config.s3.prefix || "";
              s3Key = `${prefix}${filename}`;
              const fileBuffer = readFileSync(filepath);
              await s3.upload(
                config.s3.bucket,
                s3Key,
                fileBuffer,
                "application/gzip",
              );
              s3Spinner.succeed();
              log.info(`   ↳ s3://${config.s3.bucket}/${s3Key}`);
            } catch (err: any) {
              s3Spinner.fail();
              log.error(`   S3 upload failed: ${err.message}`);
              errors++;
            }
          }

          results.push({ name, file: filepath, size, s3Key });
        } catch (err: any) {
          spinner.fail();
          log.error(`   Failed: ${err.message}`);
          errors++;
        }
      }

      log.newline();
      if (results.length > 0) {
        log.success(`${results.length} backup(s) created successfully`);
        if (errors > 0) {
          log.warn(`${errors} error(s) occurred`);
        }
      } else {
        log.error("All backups failed.");
      }
      log.newline();

      if (globalOpts.json) {
        log.json({ results, errors });
      }

      if (errors > 0 && results.length === 0) {
        process.exit(1);
      }
    });
}
