import { existsSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { CliError, isRecord, requestJson } from "./client.js";
import { createZipBuffer } from "./lib.js";

export interface DeployResult {
  url: string;
  siteName: string;
  private: boolean;
  deploymentNumber?: number;
}

interface DeploySiteResponse {
  name?: string;
  site_name?: string;
  url: string;
  private: boolean;
  deployment_number?: number;
}

function isDeploySiteResponse(value: unknown): value is DeploySiteResponse {
  return (
    isRecord(value) &&
    (typeof value.site_name === "string" || typeof value.name === "string") &&
    typeof value.url === "string" &&
    typeof value.private === "boolean" &&
    (value.deployment_number === undefined ||
      (Number.isInteger(value.deployment_number) && value.deployment_number > 0))
  );
}

export function resolveSiteName(
  cwd: string,
  directory: string,
  explicit?: string
): string | undefined {
  if (explicit) return explicit;

  const cwdCname = join(cwd, "CNAME");
  if (existsSync(cwdCname)) {
    return readFileSync(cwdCname, "utf-8").trim();
  }

  const dirCname = join(directory, "CNAME");
  if (existsSync(dirCname)) {
    return readFileSync(dirCname, "utf-8").trim();
  }

  return undefined;
}

export async function packSite(
  directory: string,
  onProgress?: (processed: number, total: number) => void
): Promise<Buffer> {
  if (!existsSync(directory)) {
    throw new CliError(`'${directory}' does not exist`);
  }

  const stat = statSync(directory);
  if (!stat.isDirectory()) {
    throw new CliError(`'${directory}' is not a directory`);
  }

  return createZipBuffer(directory, { onProgress });
}

export async function uploadSite(
  server: string,
  token: string,
  zip: Buffer,
  siteName?: string,
  fetchFn: typeof fetch = globalThis.fetch,
  makePrivate = false
): Promise<DeployResult> {
  const body = new FormData();
  body.append("file", new Blob([zip], { type: "application/zip" }), "site.zip");

  const headers: Record<string, string> = {};
  if (siteName) {
    headers["x-buzz-site"] = siteName;
  }
  if (makePrivate) {
    headers["x-buzz-access"] = "private";
  }

  const data = await requestJson(
    "/deploy",
    {
      guard: isDeploySiteResponse,
      invalid: "Server returned an invalid deployment response",
    },
    { method: "POST", headers, body },
    {
      fetchFn,
      cliOptions: { server, token },
      errors: {
        unauthorized: new CliError("Not authenticated", "Run 'buzz login' first"),
        forbidden: (message) =>
          new CliError(
            message,
            message.includes("owned by another user")
              ? "Choose a different name with --site <name>"
              : undefined
          ),
        fallback: "Unknown error",
      },
    }
  );

  return {
    url: data.url,
    siteName: data.site_name ?? data.name!,
    private: data.private,
    deploymentNumber: data.deployment_number,
  };
}
