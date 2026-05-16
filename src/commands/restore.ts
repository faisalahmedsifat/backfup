import { existsSync, unlinkSync, mkdirSync, writeFileSync } from "fs";
import { join } from "path";
import { tmpdir } from "os";
import { createLogger, LoggerOptions } from "../lib/logger.js";
import { loadConfig, parseDbUrl } from "../lib/config.js";
import { runRestore } from "../lib/restore.js";
import { checkPgRestore } from "../lib/dump.js";
import { getS3, isS3Available } from "../lib/s3.js";

/**
 * Parse an s3://bucket/key URI.
 */
function parseS3Uri(uri: string): { bucket: string; key: string } | null {
  const match = uri.match(/^s3:\/\/([^\/]+)\/(.+)$/);
  if (!match) return null;
  return { bucket: match[1], key: match[2] };
}

export function registerRestoreCommand(program: any) {
  program
    .command("restore [source]")
    .description("Restore a database from a local backup file or S3 URI")
    .option("--url <connection>", "Target database connection URL")
    .option("--yes", "Skip confirmation prompt")
    .option(
      "--config <path>",
      "Path to config file (default: ~/.backfup/config.json)",
    )
    .action(async (source: string | undefined, opts: any, cmd: any) => {
      const globalOpts = cmd.parent.opts() as LoggerOptions;
      const log = createLogger(globalOpts);

      // Check pg_restore availability
      if (!checkPgRestore()) {
        log.error(
          "pg_restore not found. Install postgresql-client to proceed.",
        );
        process.exit(1);
      }

      // Validate target
      if (!opts.url) {
        log.error("Target database URL is required. Use --url.");
        log.info(
          "Example: backfup restore backup.dump.gz --url postgresql://user:pass@host:5432/mydb",
        );
        process.exit(1);
      }

      const parsed = parseDbUrl(opts.url);
      if (!parsed) {
        log.error(`Invalid target URL: ${opts.url}`);
        process.exit(1);
      }

      // Validate source
      if (!source) {
        log.error("Backup source is required.");
        log.info(
          "Usage: backfup restore <file|s3://bucket/key> --url <target>",
        );
        process.exit(1);
      }

      let localPath: string;
      let tempFile: string | null = null;

      // Handle S3 source
      const s3Match = parseS3Uri(source);
      if (s3Match) {
        const config = loadConfig(opts.config);
        if (!config?.s3) {
          log.error(
            'S3 source requires S3 config. Run "backfup init" to configure S3.',
          );
          process.exit(1);
        }

        const available = await isS3Available();
        if (!available) {
          log.error(
            "S3 support requires @aws-sdk/client-s3. Install it: npm install @aws-sdk/client-s3",
          );
          process.exit(1);
        }

        const spinner = log.spinner("Downloading from S3...");
        spinner.start();

        try {
          const s3 = await getS3(config.s3);
          const data = await s3.download(s3Match.bucket, s3Match.key);

          // Write to temp file
          const tmpDir = join(tmpdir(), "backfup");
          if (!existsSync(tmpDir)) mkdirSync(tmpDir, { recursive: true });
          tempFile = join(tmpDir, `restore-${Date.now()}.dump.gz`);
          writeFileSync(tempFile, data);
          localPath = tempFile;

          spinner.succeed();
          log.info(`   Downloaded from s3://${s3Match.bucket}/${s3Match.key}`);
        } catch (err: any) {
          spinner.fail();
          log.error(`Download failed: ${err.message}`);
          process.exit(1);
        }
      } else {
        localPath = source;
        if (!existsSync(localPath)) {
          log.error(`File not found: ${localPath}`);
          process.exit(1);
        }
      }

      // Confirm unless --yes
      log.newline();
      log.info("Restore details:");
      log.info(`   Source:  ${source}`);
      log.info(`   Target:  ${opts.url.replace(/\/\/.*@/, "//****:****@")}`);
      log.newline();

      if (!globalOpts.yes) {
        const { default: inquirer } = await import("inquirer");
        const { confirm } = await inquirer.prompt([
          {
            type: "confirm",
            name: "confirm",
            message:
              "⚠️  This will DROP and recreate objects in the target database. Continue?",
            default: false,
          },
        ]);
        if (!confirm) {
          log.info("Restore cancelled.");
          cleanup(tempFile);
          return;
        }
      }

      const spinner = log.spinner("Restoring database...");
      spinner.start();

      try {
        await runRestore(localPath, opts.url);
        spinner.succeed();
        log.success("Restore complete!");
        log.newline();
      } catch (err: any) {
        spinner.fail();
        log.error(`Restore failed: ${err.message}`);
        cleanup(tempFile);
        process.exit(1);
      }

      cleanup(tempFile);
    });
}

function cleanup(tempFile: string | null) {
  if (tempFile && existsSync(tempFile)) {
    try {
      unlinkSync(tempFile);
    } catch {
      /* ignore */
    }
  }
}
