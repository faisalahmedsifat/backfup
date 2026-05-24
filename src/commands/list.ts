import { readdirSync, statSync } from "fs";
import { join, basename } from "path";
import { createLogger, LoggerOptions } from "../lib/logger.js";
import {
  loadConfig,
  getBackupDir,
  ensureDirectories,
  backfupConfig,
} from "../lib/config.js";
import { getS3, isS3Available } from "../lib/s3.js";

interface BackupEntry {
  name: string;
  timestamp: string;
  size: number;
  location: "local" | "s3" | "both";
  file?: string;
  s3Key?: string;
}

function formatBytes(bytes: number): string {
  if (!bytes || bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return (bytes / Math.pow(1024, i)).toFixed(1) + " " + units[i];
}

function formatTimestamp(ts: string): string {
  // Input: YYYYMMDD-HHmmss → Output: YYYY-MM-DD HH:mm:ss
  if (ts.length >= 15) {
    const d = ts.slice(0, 4) + "-" + ts.slice(4, 6) + "-" + ts.slice(6, 8);
    const t = ts.slice(9, 11) + ":" + ts.slice(11, 13) + ":" + ts.slice(13, 15);
    return d + " " + t;
  }
  return ts;
}

/**
 * Parse backup filename like "app-20260516-170000.dump.gz"
 */
function parseFilename(
  filename: string,
): { name: string; timestamp: string } | null {
  const match = filename.match(/^(.+)-(\d{8}-\d{6})\.dump\.gz$/);
  if (!match) return null;
  return { name: match[1], timestamp: match[2] };
}

/**
 * Scan local backup directory for .dump.gz files.
 */
function scanLocal(backupDir: string): BackupEntry[] {
  const entries: BackupEntry[] = [];

  try {
    const files = readdirSync(backupDir);
    for (const file of files) {
      const parsed = parseFilename(file);
      if (!parsed) continue;

      const filepath = join(backupDir, file);
      try {
        const stat = statSync(filepath);
        entries.push({
          name: parsed.name,
          timestamp: parsed.timestamp,
          size: stat.size,
          location: "local",
          file: file,
        });
      } catch {
        // Skip files we can't stat
      }
    }
  } catch {
    // Directory may not exist
  }

  return entries;
}

export function registerListCommand(program: any) {
  program
    .command("list")
    .description("List backups (local directory + optional S3)")
    .option("--s3", "Also list S3 backups")
    .option(
      "--config <path>",
      "Path to config file (default: ~/.backfup/config.json)",
    )
    .option("--last <n>", "Show only last N backups", parseInt)
    .action(async (opts: any, cmd: any) => {
      const globalOpts = cmd.parent.opts() as LoggerOptions;
      const log = createLogger(globalOpts);

      const config: backfupConfig| null = loadConfig(opts.config);

      // Determine backup dir
      let backupDir: string;
      if (config) {
        ensureDirectories(config);
        backupDir = getBackupDir(config);
      } else {
        log.warn("No config found. Showing current directory only.");
        backupDir = process.cwd();
      }

      const spinner = log.spinner("Scanning backups...");
      spinner.start();

      // Scan local
      const localEntries = scanLocal(backupDir);

      // Scan S3 if requested
      let s3Entries: BackupEntry[] = [];
      if (opts.s3 && config?.s3) {
        const available = await isS3Available();
        if (!available) {
          spinner.stop();
          log.error(
            "S3 support requires @aws-sdk/client-s3. Install it: npm install @aws-sdk/client-s3",
          );
          process.exit(1);
        }

        try {
          const s3 = await getS3(config.s3);
          const prefix = config.s3.prefix || "";
          const objects = await s3.listObjects(config.s3.bucket, prefix);

          for (const obj of objects) {
            const key = obj.Key;
            const filename = basename(key);
            const parsed = parseFilename(filename);
            if (!parsed) continue;

            // Check if this key also exists locally
            const localMatch = localEntries.find((e) => e.file === filename);

            s3Entries.push({
              name: parsed.name,
              timestamp: parsed.timestamp,
              size: obj.Size,
              location: localMatch ? "both" : "s3",
              file: localMatch ? filename : undefined,
              s3Key: key,
            });
          }
        } catch (err: any) {
          spinner.stop();
          log.error(`S3 listing failed: ${err.message}`);
          process.exit(1);
        }
      }

      // Merge: mark local entries that also exist in S3 as 'both'
      if (s3Entries.length > 0) {
        for (const local of localEntries) {
          const s3Match = s3Entries.find((e) => e.file === local.file);
          if (s3Match) {
            local.location = "both";
            local.s3Key = s3Match.s3Key;
          }
        }
      }

      // Merge all unique entries
      const allEntries = [...localEntries];
      for (const s3e of s3Entries) {
        if (
          !allEntries.find((e) => e.file === s3e.file && e.s3Key === s3e.s3Key)
        ) {
          allEntries.push(s3e);
        }
      }

      // Sort by timestamp descending
      allEntries.sort((a, b) => b.timestamp.localeCompare(a.timestamp));

      spinner.stop();

      if (allEntries.length === 0) {
        log.newline();
        log.info("No backups found.");
        log.info('Run "backfup backup" to create your first backup.');
        log.newline();
        return;
      }

      // Apply --last filter
      const limit = opts.last || allEntries.length;
      const displayed = allEntries.slice(0, limit);

      if (globalOpts.json) {
        log.json(displayed);
        return;
      }

      log.newline();
      log.table(
        ["Name", "Timestamp", "Size", "Location"],
        displayed.map((e) => [
          e.name,
          formatTimestamp(e.timestamp),
          formatBytes(e.size),
          e.location,
        ]),
      );
      log.newline();
      log.info(
        `${displayed.length} backup(s) shown${allEntries.length > displayed.length ? ` (${allEntries.length} total)` : ""}`,
      );
      log.info(`Backup directory: ${backupDir}`);
      log.newline();
    });
}
