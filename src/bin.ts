#!/usr/bin/env node

import { Command } from "commander";
import { readFileSync } from "fs";
import { resolve, dirname } from "path";
import { fileURLToPath } from "url";
import { registerInitCommand } from "./commands/init.js";
import { registerBackupCommand } from "./commands/backup.js";
import { registerRestoreCommand } from "./commands/restore.js";
import { registerListCommand } from "./commands/list.js";

// Read package.json for version
const __dirname = dirname(fileURLToPath(import.meta.url));
const pkgPath = resolve(__dirname, "../package.json");
const pkg = JSON.parse(readFileSync(pkgPath, "utf-8"));

const program = new Command();

program
  .name("backfup")
  .description(
    "Postgres backup CLI — wrap pg_dump/pg_restore with multi-database and S3 support",
  )
  .version(pkg.version, "-v, --version")
  .option("--quiet", "Suppress all output except errors")
  .option("--json", "Output results as JSON")
  .option("--no-color", "Disable colored output")
  .option("--yes", "Skip confirmation prompts")
  .option("--verbose", "Show detailed debug output");

registerInitCommand(program);
registerBackupCommand(program);
registerRestoreCommand(program);
registerListCommand(program);

program.parse();
