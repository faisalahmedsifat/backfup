import { spawn, execSync, execFileSync } from "child_process";
import { createGzip } from "zlib";
import { createWriteStream, statSync, unlinkSync, existsSync } from "fs";
import { pipeline } from "stream/promises";

/**
 * Run pg_dump for a given database URL and write compressed output to disk.
 * Returns the file size in bytes.
 */
export async function runDump(
  url: string,
  outputPath: string,
): Promise<number> {
  return new Promise((resolve, reject) => {
    const pgDump = spawn(
      "pg_dump",
      [
        url,
        "--format=custom",
        "--compress=0",
        "--no-owner",
        "--no-privileges",
        "--no-password",
      ],
      {
        stdio: ["ignore", "pipe", "pipe"],
        env: { ...process.env, PGPASSWORD: process.env.PGPASSWORD || "" },
      },
    );

    const gzip = createGzip({ level: 6 });
    const out = createWriteStream(outputPath);

    let stderr = "";
    pgDump.stderr.on("data", (chunk: Buffer) => {
      stderr += chunk.toString();
    });

    let exitCode: number | null = null;
    let settled = false;

    // Track exit code whenever close fires (may fire before or after pipeline ends)
    pgDump.on("close", (code) => {
      exitCode = code;
    });

    const finish = (err?: Error) => {
      if (settled) return;
      settled = true;
      // Clean up zero-byte file on failure
      if (err || exitCode !== 0) {
        try {
          if (existsSync(outputPath)) unlinkSync(outputPath);
        } catch {}
      }
      if (err) {
        reject(err);
      } else if (exitCode !== 0) {
        const msg = buildPgDumpError(stderr);
        reject(new Error(msg));
      } else {
        try {
          resolve(statSync(outputPath).size);
        } catch {
          resolve(0);
        }
      }
    };

    pgDump.on("error", (err) => finish(err));

    pipeline(pgDump.stdout, gzip, out)
      .then(() => finish())
      .catch((err) => finish(err));
  });
}

/**
 * Parse pg_dump version string into major.minor numbers.
 * Example input: "pg_dump (PostgreSQL) 14.22 (Ubuntu 14.22-0ubuntu0.22.04.1)"
 */
export function getPgDumpVersion(): {
  major: number;
  minor: number;
  raw: string;
} | null {
  try {
    const out = execFileSync("pg_dump", ["--version"], {
      encoding: "utf-8",
      stdio: ["ignore", "pipe", "ignore"],
    });
    const match = out.match(/(\d+)\.(\d+)/);
    if (!match) return null;
    return {
      major: parseInt(match[1], 10),
      minor: parseInt(match[2], 10),
      raw: out.trim(),
    };
  } catch {
    return null;
  }
}

/**
 * Query the server version using a lightweight psql connection.
 */
export function getServerVersion(url: string): string | null {
  try {
    const out = execFileSync("psql", [url, "-Atc", "SELECT version();"], {
      encoding: "utf-8",
      stdio: ["ignore", "pipe", "ignore"],
      env: { ...process.env, PGPASSWORD: process.env.PGPASSWORD || "" },
      timeout: 10000,
    });
    return out.trim();
  } catch {
    return null;
  }
}

/**
 * Parse server version string into major.minor.
 * Example: "PostgreSQL 18.3 (Debian 18.3-1.pgdg13+1) on x86_64-pc-linux-gnu..."
 */
export function parseServerVersion(
  versionString: string,
): { major: number; minor: number } | null {
  const match = versionString.match(/PostgreSQL\s+(\d+)\.(\d+)/i);
  if (!match) return null;
  return { major: parseInt(match[1], 10), minor: parseInt(match[2], 10) };
}

/**
 * Turn pg_dump stderr into a helpful error message.
 * Detects version mismatch and gives actionable install instructions.
 */
function buildPgDumpError(stderr: string): string {
  // Version mismatch: server is newer than local pg_dump
  const serverMatch = stderr.match(/server version:\s*([\d.]+)/);
  const localMatch = stderr.match(/pg_dump version:\s*([\d.]+)/);

  if (serverMatch && localMatch) {
    const serverVer = serverMatch[1];
    const localVer = localMatch[1];
    const serverMajor = serverMatch[1].split(".")[0];
    let msg = `Version mismatch: your pg_dump is v${localVer} but the server is v${serverVer}.\n`;
    msg += `pg_dump must be >= the server version. Install PostgreSQL ${serverMajor} client:\n`;
    msg += "\n";
    msg += "  # Debian/Ubuntu:\n";
    msg +=
      "  sudo sh -c 'echo \"deb http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main\" > /etc/apt/sources.list.d/pgdg.list'\n";
    msg +=
      "  curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | sudo gpg --dearmor -o /etc/apt/trusted.gpg.d/pgdg.gpg\n";
    msg += `  sudo apt update && sudo apt install postgresql-client-${serverMajor}\n`;
    msg += "\n";
    msg += "  # macOS:\n";
    msg += "  brew upgrade libpq\n";
    msg += "\n";
    msg += "  # Then verify: pg_dump --version";
    return msg;
  }

  // Generic pg_dump error — include stderr
  const cleaned = stderr.trim().replace(/\n/g, "; ");
  return `pg_dump failed: ${cleaned || "unknown error"}`;
}

/**
 * Check if pg_dump is available on the system.
 */
export function checkPgDump(): boolean {
  try {
    execSync("pg_dump --version", { stdio: "ignore" });
    return true;
  } catch {
    return false;
  }
}

/**
 * Check if pg_restore is available on the system.
 */
export function checkPgRestore(): boolean {
  try {
    execSync("pg_restore --version", { stdio: "ignore" });
    return true;
  } catch {
    return false;
  }
}
