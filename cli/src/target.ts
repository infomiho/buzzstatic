import { CliError } from "./errors.js";
import {
  getCredential,
  isServerName,
  loadConfig,
  normalizeServerUrl,
  serverNameFor,
  type Config,
} from "./credentials.js";

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

function savedNamesTip(config: Config): string {
  const names = Object.keys(config.servers ?? {});
  return names.length ? `Saved servers: ${names.join(", ")}` : "No servers are saved yet";
}

function lookupName(value: string, config: Config): Server {
  const url = config.servers?.[value];
  if (!url) {
    throw new CliError(
      `Unknown server '${value}'`,
      `${savedNamesTip(config)}. Add it with 'buzz server add ${value} <url>'`,
      "UNKNOWN_SERVER"
    );
  }
  return { url, name: value };
}

export function resolveSelector(value: string, config: Config): Server {
  const trimmed = value.trim();
  if (isServerName(trimmed)) return lookupName(trimmed, config);
  if (!trimmed.includes("://")) {
    throw new CliError(
      `Invalid server '${value}'`,
      "Use a saved server name or a full URL like https://buzz.example.com",
      "INVALID_SERVER_SELECTOR"
    );
  }
  const url = normalizeServerUrl(trimmed);
  return { url, name: serverNameFor(config, url) };
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

function resolveDefault(url: string, config: Config): Server {
  const normalized = normalizeServerUrl(url);
  return { url: normalized, name: serverNameFor(config, normalized) };
}

function withSource(error: unknown, source: SelectionSource): unknown {
  if (!(error instanceof CliError)) return error;
  return new CliError(`${error.message} (from ${SELECTION_LABELS[source]})`, error.tip, error.code);
}

export function selectServer(cliOptions: CliOptions, config: Config): Selection | undefined {
  const chosen = selector(cliOptions, config);
  if (!chosen) return undefined;
  try {
    const resolve = chosen.source === "default" ? resolveDefault : resolveSelector;
    return { ...resolve(chosen.value, config), source: chosen.source };
  } catch (error) {
    throw withSource(error, chosen.source);
  }
}

function noServerSelected(config: Config): CliError {
  const names = Object.keys(config.servers ?? {});
  const tip = names.length
    ? `${savedNamesTip(config)}. Pass --server <name> or run 'buzz server default <name>'`
    : "Add a server: buzz server add <name> <url> --default";
  return new CliError("No server selected", tip, "NO_SERVER_SELECTED");
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
  if (!selection) throw noServerSelected(config);
  return withCredential(selection, cliOptions, config);
}

export function loginCommand(server: Server): string {
  return `buzz login --server ${serverSelector(server)}`;
}

export function runningInCi(): boolean {
  const ci = process.env.CI?.trim().toLowerCase();
  return !!ci && ci !== "false" && ci !== "0";
}

export function credentialRecovery(target: Target): string {
  const login = loginCommand(target);
  if (runningInCi() && target.credential !== "saved_session") {
    return `Update the ${target.credential === "flag" ? "--token value" : "BUZZ_TOKEN secret"} with a valid deployment token for ${serverLabel(target)}`;
  }
  if (target.credential === "flag") {
    if (target.hasEnvToken) return "Replace --token, or omit it to use BUZZ_TOKEN";
    if (target.hasSavedSession) return "Replace --token, or omit it to use the saved session";
    return `Replace --token, or omit it and run: ${login}`;
  }
  if (target.credential === "env") {
    if (target.hasSavedSession) return "Update BUZZ_TOKEN, or unset it to use the saved session";
    return `Update BUZZ_TOKEN, or unset it and run: ${login}`;
  }
  return `Run: ${login}`;
}

const REJECTED: Record<CredentialSource, (server: string) => string> = {
  flag: (server) => `The token from --token was rejected by ${server}`,
  env: (server) => `The token from BUZZ_TOKEN was rejected by ${server}`,
  saved_session: (server) => `Your saved session for ${server} was rejected`,
  none: (server) => `${server} requires sign-in`,
};

export function rejectedCredential(target: Target): CliError {
  return new CliError(
    REJECTED[target.credential](serverLabel(target)),
    credentialRecovery(target),
    "AUTH_REJECTED"
  );
}

export function missingCredential(target: Target): CliError {
  return new CliError(
    `No saved session for ${serverLabel(target)}`,
    credentialRecovery(target),
    "NOT_AUTHENTICATED"
  );
}

function sessionRecovery(target: Target): string {
  if (runningInCi()) return "This needs a signed-in session, so run it outside CI";
  const steps = [
    target.credential === "flag" ? "omit --token" : undefined,
    target.hasEnvToken ? "unset BUZZ_TOKEN" : undefined,
  ].filter(Boolean);
  if (!steps.length) return `Run: ${loginCommand(target)}`;
  const action = steps.join(" and ");
  const sentence = action[0].toUpperCase() + action.slice(1);
  return target.hasSavedSession
    ? `${sentence} to use the saved session`
    : `${sentence}, then run: ${loginCommand(target)}`;
}

export function sessionRequired(target: Target, message: string): CliError {
  return new CliError(message, sessionRecovery(target), "SESSION_REQUIRED");
}

export function targetSummary(target: Target): string {
  return `Selected by: ${SELECTION_LABELS[target.source]} · Authentication: ${CREDENTIAL_LABELS[target.credential]}`;
}

export function announcedTarget(cliOptions: CliOptions = {}): Target {
  const target = resolveTarget(cliOptions);
  console.error(`Server: ${serverLabel(target)} · ${targetSummary(target)}`);
  return target;
}
