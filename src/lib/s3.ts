import type { S3Config } from "./config.js";

/**
 * Lazy-load the S3 client only when S3 operations are requested.
 * This keeps `@aws-sdk/client-s3` truly optional — if you never
 * use --s3 flags, you never pay the import cost.
 */

interface S3ClientWrapper {
  upload: (
    bucket: string,
    key: string,
    body: Buffer | string,
    contentType?: string,
  ) => Promise<void>;
  download: (bucket: string, key: string) => Promise<Buffer>;
  listObjects: (
    bucket: string,
    prefix: string,
  ) => Promise<Array<{ Key: string; Size: number; LastModified: Date }>>;
  deleteObject: (bucket: string, key: string) => Promise<void>;
}

async function createS3Wrapper(config: S3Config): Promise<S3ClientWrapper> {
  const {
    S3Client,
    PutObjectCommand,
    GetObjectCommand,
    ListObjectsV2Command,
    DeleteObjectCommand,
  } = await import("@aws-sdk/client-s3");

  const clientConfig: Record<string, unknown> = {
    region: config.region || "us-east-1",
  };

  if (config.accessKeyId && config.secretAccessKey) {
    clientConfig.credentials = {
      accessKeyId: config.accessKeyId,
      secretAccessKey: config.secretAccessKey,
    };
  }

  if (config.endpoint) {
    clientConfig.endpoint = config.endpoint;
    clientConfig.forcePathStyle = true;
  }

  const client = new S3Client(clientConfig as any);

  return {
    async upload(bucket, key, body, contentType) {
      await client.send(
        new PutObjectCommand({
          Bucket: bucket,
          Key: key,
          Body: body,
          ContentType: contentType,
        }),
      );
    },

    async download(bucket, key) {
      const response = await client.send(
        new GetObjectCommand({
          Bucket: bucket,
          Key: key,
        }),
      );
      // Read stream into buffer
      const chunks: Buffer[] = [];
      if (response.Body) {
        for await (const chunk of response.Body as AsyncIterable<Buffer>) {
          chunks.push(chunk);
        }
      }
      return Buffer.concat(chunks);
    },

    async listObjects(bucket, prefix) {
      const results: Array<{ Key: string; Size: number; LastModified: Date }> =
        [];
      let continuationToken: string | undefined;

      do {
        const response = await client.send(
          new ListObjectsV2Command({
            Bucket: bucket,
            Prefix: prefix,
            ContinuationToken: continuationToken,
          }),
        );

        if (response.Contents) {
          for (const obj of response.Contents) {
            if (obj.Key) {
              results.push({
                Key: obj.Key,
                Size: obj.Size ?? 0,
                LastModified: obj.LastModified ?? new Date(0),
              });
            }
          }
        }

        continuationToken = response.NextContinuationToken;
      } while (continuationToken);

      return results;
    },

    async deleteObject(bucket, key) {
      await client.send(
        new DeleteObjectCommand({
          Bucket: bucket,
          Key: key,
        }),
      );
    },
  };
}

let cachedS3: S3ClientWrapper | null = null;
let cachedConfig: S3Config | null = null;

/**
 * Get (or create) a cached S3 wrapper. Cached so we don't
 * re-import the SDK on every call.
 */
export async function getS3(config: S3Config): Promise<S3ClientWrapper> {
  // Bust cache if config changed
  if (cachedS3 && cachedConfig === config) {
    return cachedS3;
  }
  cachedS3 = await createS3Wrapper(config);
  cachedConfig = config;
  return cachedS3;
}

/**
 * Check if S3 is configured and the SDK is available.
 * Returns true if the optional dependency was installed.
 */
export async function isS3Available(): Promise<boolean> {
  try {
    await import("@aws-sdk/client-s3");
    return true;
  } catch {
    return false;
  }
}
