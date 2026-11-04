import { CliError } from "./errors.js";
import { getCredential, loadConfig, normalizeServerUrl, type Config } from "./credentials.js";

export interface CliOptions {
  server?: string;
  token?: string;
}

export type SelectionSource = "flag" | "env" | "default";
export type CredentialSource = "flag" | "env" | "saved_session" | "none";

export interface Server {
  url: string;
  name?: string | null;
}

export interface Selection extends Server {
  source: SelectionSource;
}

export interface Target extends Selection {
  credential: CredentialSource;
  token?: string;
  hasSavedSession: boolean;
  hasEnvToken: boolean;
}

export const SELECTION_LABELS: Record<SelectionSource, string> = {
  flag: "--server",
  env: "BUZZ_SERVER",
  default: "saved default",
};

export const CREDENTIAL_LABELS: Record<CredentialSource, string> = {
  flag: "--token",
  env: "BUZZ_TOKEN",
  saved_session: "saved session",
  none: "none",
};

export function serverLabel(server: Server): string {
  return server.name ? `${server.name} (${server.url})` : server.url;
}

export function serverSelector(server: Server): string {
  return server.name ?? server.url;
}

export function resolveSelector(value: string, _config: Config): Server {
  if (!value.trim()) {
    throw new CliError(
      "The server selector is empty",
      "Pass a full URL like https://buzz.example.com",
      "INVALID_SERVER_SELECTOR"
    );
  }
  return { url: normalizeServerUrl(value) };
}

function selector(
  cliOptions: CliOptions,
  config: Config
): { value: string; source: SelectionSource } | undefined {
  if (cliOptions.server !== undefined) return { value: cliOptions.server, source: "flag" };
  const env = process.env.BUZZ_SERVER?.trim();
  if (env) return { value: env, source: "env" };
  if (config.server) return { value: config.server, source: "default" };
  return undefined;
}

function withSource(error: unknown, source: SelectionSource): unknown {
  if (!(error instanceof CliError)) return error;
  return new CliError(`${error.message} (from ${SELECTION_LABELS[source]})`, error.tip, error.code);
}

export function selectServer(cliOptions: CliOptions, config: Config): Selection | undefined {
  const chosen = selector(cliOptions, config);
  if (!chosen) return undefined;
  try {
    return { ...resolveSelector(chosen.value, config), source: chosen.source };
  } catch (error) {
    throw withSource(error, chosen.source);
  }
}

export function noServerSelected(): CliError {
  return new CliError(
    "No server selected",
    "Set a default with 'buzz config server <url>', or pass --server <url>",
    "NO_SERVER_SELECTED"
  );
}

export function withCredential(
  selection: Selection,
  cliOptions: CliOptions,
  config: Config
): Target {
  const saved = getCredential(config, selection.url);
  const envToken = process.env.BUZZ_TOKEN || undefined;
  const base = { ...selection, hasSavedSession: saved !== undefined, hasEnvToken: !!envToken };
  if (cliOptions.token) return { ...base, credential: "flag", token: cliOptions.token };
  if (envToken) return { ...base, credential: "env", token: envToken };
  if (saved) return { ...base, credential: "saved_session", token: saved };
  return { ...base, credential: "none" };
}

export function resolveTarget(cliOptions: CliOptions = {}, config: Config = loadConfig()): Target {
  const selection = selectServer(cliOptions, config);
  if (!selection) throw noServerSelected();
  return withCredential(selection, cliOptions, config);
}
