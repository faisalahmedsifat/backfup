import { spawn } from "child_process";
import { createGunzip } from "zlib";
import { createReadStream } from "fs";
import { pipeline } from "stream/promises";

/**
 * Restore a compressed pg_dump file to a target database.
 * @param dumpPath - Path to the .dump.gz file (local)
 * @param targetUrl - Target PostgreSQL connection URL
 */
export async function runRestore(
  dumpPath: string,
  targetUrl: string,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const input = createReadStream(dumpPath);
    const gunzip = createGunzip();

    const pgRestore = spawn(
      "pg_restore",
      [
        "--dbname=" + targetUrl,
        "--no-owner",
        "--no-privileges",
        "--clean",
        "--if-exists",
        "--no-password",
      ],
      {
        stdio: ["pipe", "pipe", "pipe"],
        env: { ...process.env, PGPASSWORD: process.env.PGPASSWORD || "" },
      },
    );

    let stderr = "";
    pgRestore.stderr.on("data", (chunk: Buffer) => {
      stderr += chunk.toString();
    });

    let exitCode: number | null = null;
    let settled = false;

    // Track exit code whenever close fires (may fire before or after pipeline ends)
    pgRestore.on("close", (code) => {
      exitCode = code;
    });

    const finish = (err?: Error) => {
      if (settled) return;
      settled = true;
      if (err) {
        reject(err);
      } else if (exitCode !== 0) {
        reject(new Error(`pg_restore exited with code ${exitCode}: ${stderr}`));
      } else {
        resolve();
      }
    };

    pgRestore.on("error", (err) => finish(err));

    // Stream: file → gunzip → pg_restore stdin
    pipeline(input, gunzip, pgRestore.stdin)
      .then(() => finish())
      .catch((err) => finish(err));
  });
}
