import inquirer from "inquirer";
import { createLogger, LoggerOptions } from "../lib/logger.js";
import {
  backfupConfig,
  S3Config,
  loadConfig,
  saveConfig,
  ensureDirectories,
  DEFAULT_BACKUP_DIR,
} from "../lib/config.js";

export function registerInitCommand(program: any) {
  program
    .command("init")
    .description("Set up ~/.backfup/config.json interactively")
    .option(
      "--config <path>",
      "Path to config file (default: ~/.backfup/config.json)",
    )
    .action(async (opts: any, cmd: any) => {
      const globalOpts = cmd.parent.opts() as LoggerOptions;
      const log = createLogger(globalOpts);

      log.newline();
      log.info("🗃️  backfup — Postgres Backup Setup");
      log.info("─".repeat(40));
      log.newline();

      // Check if config already exists
      const existing = loadConfig(opts.config);
      if (existing) {
        const { overwrite } = await inquirer.prompt([
          {
            type: "confirm",
            name: "overwrite",
            message:
              "Config already exists at ~/.backfup/config.json. Overwrite?",
            default: false,
          },
        ]);
        if (!overwrite) {
          log.info("Setup cancelled. Config unchanged.");
          log.newline();
          return;
        }
      }

      // --- Database Configuration ---
      log.info("Database Configuration");
      log.info("Each database needs a name and a connection URL.");
      log.info(
        "Tip: Use $VAR or ${VAR} in URLs to reference environment variables.",
      );
      log.newline();

      const databases: Record<string, string> = {};

      let addMore = true;
      while (addMore) {
        const dbAnswers = await inquirer.prompt([
          {
            type: "input",
            name: "name",
            message: 'Database name (short alias, e.g. "app", "analytics"):',
            validate: (input: string) => {
              if (!input.trim()) return "Name is required";
              if (databases[input.trim()]) return "Name already used";
              return true;
            },
          },
          {
            type: "input",
            name: "url",
            message:
              "Connection URL (e.g. postgresql://user:pass@host:5432/db or $DATABASE_URL):",
            validate: (input: string) =>
              input.trim() ? true : "URL is required",
          },
        ]);

        databases[dbAnswers.name.trim()] = dbAnswers.url.trim();

        const { more } = await inquirer.prompt([
          {
            type: "confirm",
            name: "more",
            message: "Add another database?",
            default: false,
          },
        ]);
        addMore = more;
      }

      const { backupDir } = await inquirer.prompt([
        {
          type: "input",
          name: "backupDir",
          message: "Backup storage directory:",
          default: DEFAULT_BACKUP_DIR,
        },
      ]);

      log.newline();
      log.info("S3 Storage Configuration (optional)");
      log.info("─".repeat(40));
      log.newline();

      const { useS3 } = await inquirer.prompt([
        {
          type: "confirm",
          name: "useS3",
          message: "Configure S3 for remote backup storage?",
          default: false,
        },
      ]);

      let s3: S3Config | undefined;

      if (useS3) {
        const s3Answers = await inquirer.prompt([
          {
            type: "input",
            name: "bucket",
            message: "S3 bucket name:",
            validate: (input: string) =>
              input.trim() ? true : "Bucket name is required",
          },
          {
            type: "input",
            name: "region",
            message: "S3 region:",
            default: "us-east-1",
          },
          {
            type: "input",
            name: "prefix",
            message: 'Key prefix (e.g. "db-backups/", press Enter to skip):',
            default: "",
          },
          {
            type: "input",
            name: "accessKeyId",
            message:
              "Access Key ID (leave blank to use default AWS credential chain):",
            default: "",
          },
          {
            type: "password",
            name: "secretAccessKey",
            message:
              "Secret Access Key (leave blank to use default AWS credential chain):",
            mask: "*",
          },
          {
            type: "input",
            name: "endpoint",
            message:
              "Custom endpoint (for S3-compatible providers, e.g. https://s3.fr-par.scw.cloud):",
            default: "",
          },
        ]);

        s3 = {
          bucket: s3Answers.bucket.trim(),
          region: s3Answers.region.trim() || "us-east-1",
        };

        if (s3Answers.prefix.trim()) {
          s3.prefix = s3Answers.prefix.trim();
        }
        if (s3Answers.accessKeyId.trim()) {
          s3.accessKeyId = s3Answers.accessKeyId.trim();
        }
        if (s3Answers.secretAccessKey.trim()) {
          s3.secretAccessKey = s3Answers.secretAccessKey.trim();
        }
        if (s3Answers.endpoint.trim()) {
          s3.endpoint = s3Answers.endpoint.trim();
        }
      }

      const config: backfupConfig = {
        databases,
        backupDir: backupDir.trim(),
      };

      if (s3) {
        config.s3 = s3;
      }

      saveConfig(config);
      ensureDirectories(config);

      log.newline();
      log.success("Configuration saved to ~/.backfup/config.json");
      log.newline();

      log.info("  Databases:");
      for (const [name, url] of Object.entries(databases)) {
        log.info(`    ${name}  →  ${url}`);
      }
      log.info(`  Backup dir: ${backupDir.trim()}`);
      if (s3) {
        log.info(`  S3:         s3://${s3.bucket}/${s3.prefix || ""}`);
      }
      log.newline();
      log.info("Next steps:");
      log.info('  Run "backfup backup" to create your first backup');
      log.info('  Run "backfup list" to browse backups');
      log.newline();
    });
}
