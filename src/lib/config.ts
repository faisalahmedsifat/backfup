import { readFileSync, existsSync, mkdirSync, writeFileSync } from "fs";
import { resolve, dirname } from "path";
import { homedir } from "os";

const DEFAULT_CONFIG_DIR = resolve(homedir(), ".backfup");
const DEFAULT_CONFIG_PATH = resolve(DEFAULT_CONFIG_DIR, "config.json");
const DEFAULT_BACKUP_DIR = resolve(DEFAULT_CONFIG_DIR, "backups");

export interface S3Config {
  bucket: string;
  region: string;
  prefix?: string;
  accessKeyId?: string;
  secretAccessKey?: string;
  endpoint?: string;
}

export interface backfupConfig{
  databases: Record<string, string>;
  backupDir: string;
  s3?: S3Config;
}

/**
 * Expand ~ in paths and resolve to absolute.
 */
function expandPath(p: string): string {
  if (p.startsWith("~")) {
    return resolve(homedir(), p.slice(1));
  }
  return resolve(p);
}

/**
 * Interpolate $VAR and ${VAR} references in a string.
 */
export function interpolateEnv(value: string): string {
  return value
    .replace(/\$\{(\w+)\}/g, (_, name) => process.env[name] ?? "")
    .replace(/\$(\w+)/g, (_, name) => process.env[name] ?? "");
}

/**
 * Resolve the config file path. Uses --config flag if provided,
 * otherwise ~/.backfup/config.json
 */
export function getConfigPath(override?: string): string {
  if (override) return resolve(override);
  return DEFAULT_CONFIG_PATH;
}

/**
 * Get the backup directory path.
 */
export function getBackupDir(config: backfupConfig): string {
  return expandPath(config.backupDir);
}

/**
 * Ensure the config directory and backup directory exist.
 */
export function ensureDirectories(config: backfupConfig): void {
  const configDir = dirname(DEFAULT_CONFIG_PATH);
  if (!existsSync(configDir)) {
    mkdirSync(configDir, { recursive: true });
  }
  const backupDir = getBackupDir(config);
  if (!existsSync(backupDir)) {
    mkdirSync(backupDir, { recursive: true });
  }
}

/**
 * Load config from ~/.backfup/config.json (or --config override).
 * Interpolates env vars in database URLs.
 */
export function loadConfig(configPath?: string):backfupConfig | null {
  const path = getConfigPath(configPath);

  if (!existsSync(path)) {
    return null;
  }

  try {
    const raw = JSON.parse(readFileSync(path, "utf-8")) as backfupConfig;

    // Interpolate env vars in database URLs
    const databases: Record<string, string> = {};
    for (const [name, url] of Object.entries(raw.databases ?? {})) {
      databases[name] = interpolateEnv(url);
    }
    raw.databases = databases;

    return raw;
  } catch {
    return null;
  }
}

/**
 * Write config to ~/.backfup/config.json.
 */
export function saveConfig(config: backfupConfig): void {
  const configDir = dirname(DEFAULT_CONFIG_PATH);
  if (!existsSync(configDir)) {
    mkdirSync(configDir, { recursive: true });
  }
  writeFileSync(DEFAULT_CONFIG_PATH, JSON.stringify(config, null, 2), "utf-8");
}

/**
 * Parse a database connection URL into its components.
 */
export interface DbConnection {
  url: string;
  host: string;
  port: number;
  database: string;
  user: string;
  password: string;
}

export function parseDbUrl(raw: string): DbConnection | null {
  try {
    const url = new URL(raw);
    const protocol = url.protocol.replace(":", "");
    if (protocol !== "postgresql" && protocol !== "postgres") {
      return null;
    }
    return {
      url: raw,
      host: url.hostname,
      port: parseInt(url.port || "5432", 10),
      database: url.pathname.replace(/^\//, ""),
      user: decodeURIComponent(url.username || ""),
      password: decodeURIComponent(url.password || ""),
    };
  } catch {
    return null;
  }
}

/**
 * Redact password from a URL for display.
 */
export function redactUrl(url: string): string {
  try {
    const u = new URL(url);
    if (u.password) {
      u.password = "****";
    }
    return u.toString();
  } catch {
    return url;
  }
}

export { DEFAULT_CONFIG_PATH, DEFAULT_BACKUP_DIR };
