import { chmodSync, existsSync, readFileSync, renameSync, rmSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { CliError } from "./errors.js";

export const CONFIG_PATH = join(homedir(), ".buzz.config.json");
export const CONFIG_VERSION = 2;
export const LEGACY_DEFAULT_SERVER = "http://localhost:8080";

export interface Config {
  server?: string;
  servers?: Record<string, string>;
  credentials?: Record<string, string>;
}

const SERVER_NAME = /^[a-z][a-z0-9-]*$/;

export function isServerName(value: string): boolean {
  return SERVER_NAME.test(value);
}

export function serverNameFor(config: Config, url: string): string | undefined {
  return Object.entries(config.servers ?? {}).find(([, saved]) => saved === url)?.[0];
}

export class ConfigUnreadableError extends CliError {
  constructor(public path: string) {
    super(
      `Cannot read the Buzz config file at ${path}`,
      "Fix or remove the file. Buzz will not overwrite it.",
      "CONFIG_UNREADABLE"
    );
  }
}

export function normalizeServerUrl(raw: string): string {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new CliError(
      `Invalid server URL '${raw}'`,
      "Use a full URL like https://buzz.example.com",
      "INVALID_SERVER_SELECTOR"
    );
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new CliError(
      `Server URL '${raw}' must use http or https`,
      undefined,
      "INVALID_SERVER_SELECTOR"
    );
  }
  const path = url.pathname.replace(/\/+$/, "");
  return `${url.protocol}//${url.host}${path}`;
}

export function tryNormalize(raw: string): string | undefined {
  try {
    return normalizeServerUrl(raw);
  } catch {
    return undefined;
  }
}

export function getCredential(config: Config, server: string): string | undefined {
  return config.credentials?.[normalizeServerUrl(server)];
}

export function setCredential(config: Config, server: string, token: string): void {
  config.credentials = { ...config.credentials, [normalizeServerUrl(server)]: token };
}

export function clearCredential(config: Config, server: string): void {
  if (!config.credentials) return;
  delete config.credentials[normalizeServerUrl(server)];
  if (Object.keys(config.credentials).length === 0) {
    delete config.credentials;
  }
}

function readRawConfig(path: string): Record<string, unknown> | undefined {
  if (!existsSync(path)) return undefined;

  let parsed: unknown;
  try {
    parsed = JSON.parse(readFileSync(path, "utf-8"));
  } catch {
    throw new ConfigUnreadableError(path);
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    throw new ConfigUnreadableError(path);
  }
  return parsed as Record<string, unknown>;
}

function parseConfig(raw: Record<string, unknown>): Config {
  const config: Config = {};
  if (typeof raw.server === "string" && raw.server.trim()) {
    config.server = tryNormalize(raw.server) ?? raw.server;
  }
  if (typeof raw.servers === "object" && raw.servers !== null) {
    for (const [name, server] of Object.entries(raw.servers)) {
      const url = typeof server === "string" ? tryNormalize(server) : undefined;
      const urlTaken = url !== undefined && serverNameFor(config, url) !== undefined;
      if (isServerName(name) && url && !urlTaken) {
        config.servers = { ...config.servers, [name]: url };
      }
    }
  }
  if (typeof raw.credentials === "object" && raw.credentials !== null) {
    for (const [server, token] of Object.entries(raw.credentials)) {
      const url = tryNormalize(server);
      if (typeof token === "string" && url) {
        setCredential(config, url, token);
      }
    }
  }
  return config;
}

// Before version 2 the CLI targeted localhost when no server was saved, and a
// single unscoped token belonged to whichever server was targeted at the time.
function migrateLegacyConfig(raw: Record<string, unknown>, config: Config): void {
  if (typeof raw.token === "string") {
    const server = tryNormalize(config.server ?? LEGACY_DEFAULT_SERVER);
    if (server && getCredential(config, server) === undefined) {
      setCredential(config, server, raw.token);
    }
  }
  config.server ??= LEGACY_DEFAULT_SERVER;
}

export function loadConfig(path: string = CONFIG_PATH): Config {
  const raw = readRawConfig(path);
  if (!raw) return {};

  const config = parseConfig(raw);
  if (raw.version === undefined && Object.keys(raw).length > 0) {
    migrateLegacyConfig(raw, config);
    try {
      saveConfig(config, path);
    } catch {
      // A read-only config still works; migration is retried on the next load.
    }
  }
  return config;
}

export function saveConfig(config: Config, path: string = CONFIG_PATH): void {
  const tmpPath = `${path}.${process.pid}.tmp`;
  const versioned = { version: CONFIG_VERSION, ...config };
  try {
    writeFileSync(tmpPath, JSON.stringify(versioned, null, 2) + "\n", { mode: 0o600 });
    chmodSync(tmpPath, 0o600);
    renameSync(tmpPath, path);
  } catch (error) {
    rmSync(tmpPath, { force: true });
    throw error;
  }
}
