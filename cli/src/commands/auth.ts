import { spawn } from "node:child_process";
import { Command } from "commander";
import {
  apiFetch,
  authHeaders,
  isRecord,
  requestJson,
  CliError,
  type CliOptions,
} from "../client.js";
import {
  CREDENTIAL_LABELS,
  SELECTION_LABELS,
  resolveTarget,
  serverLabel,
  serverSelector,
  type Target,
} from "../target.js";
import { checkServerCompatibility } from "../compat.js";
import {
  clearCredential,
  getCredential,
  loadConfig,
  saveConfig,
  serverNameFor,
  setCredential,
  type Config,
} from "../credentials.js";

interface DeviceStart {
  verification_uri: string;
  user_code: string;
  device_code: string;
  interval?: number;
  expires_in?: number;
}

function isDeviceStart(value: unknown): value is DeviceStart {
  return (
    isRecord(value) &&
    typeof value.verification_uri === "string" &&
    typeof value.user_code === "string" &&
    typeof value.device_code === "string" &&
    (value.interval === undefined || typeof value.interval === "number") &&
    (value.expires_in === undefined || typeof value.expires_in === "number")
  );
}

function openInBrowser(url: string): void {
  const command =
    process.platform === "darwin"
      ? "open"
      : process.platform === "win32"
        ? "cmd"
        : "xdg-open";
  const args = process.platform === "win32" ? ["/c", "start", "", url] : [url];
  try {
    const child = spawn(command, args, { stdio: "ignore", detached: true });
    child.on("error", () => {});
    child.unref();
  } catch {
    // Best effort; the URL is printed either way.
  }
}

interface PollResult {
  status?: string;
  error?: string;
  token?: string;
  user?: { login: string; name?: string | null };
}

function isPollResult(value: unknown): value is PollResult {
  if (!isRecord(value)) return false;
  if (value.status === "complete") {
    return (
      typeof value.token === "string" &&
      isRecord(value.user) &&
      typeof value.user.login === "string"
    );
  }
  return true;
}

interface AuthUser {
  login: string;
  name?: string | null;
}

function isUser(value: unknown): value is AuthUser {
  return isRecord(value) && typeof value.login === "string";
}

function defaultServerLines(config: Config, target: Target): string[] {
  if (!config.server) {
    return ["Default server: none", `Set a default: buzz server default ${serverSelector(target)}`];
  }
  const name = serverNameFor(config, config.server);
  return [`Default server: ${serverLabel({ url: config.server, name })}`];
}

function remainingTokenLines(target: Target): string[] {
  if (target.credential === "env") {
    return ["BUZZ_TOKEN is still set, so commands in this environment keep using it."];
  }
  if (target.credential === "flag") {
    return ["--token applies only to commands that include it."];
  }
  return [];
}

function loginInCi(): CliError {
  return new CliError(
    "Browser sign-in is not available in CI",
    "Create a deployment token with 'buzz tokens create <site>', then set BUZZ_TOKEN and BUZZ_SERVER",
    "LOGIN_IN_CI"
  );
}

interface LoginOptions {
  browser?: boolean;
}

export async function login(options: LoginOptions = {}, cliOptions: CliOptions = {}) {
  if (process.env.CI) throw loginInCi();
  const target = resolveTarget(cliOptions);

  await checkServerCompatibility(target);

  const start = await requestJson(
    "/auth/device",
    {
      guard: isDeviceStart,
      invalid: "Server returned an invalid device authorization response",
    },
    { method: "POST" },
    { target, auth: "none", errors: { fallback: "Failed to start login" } }
  );

  console.error(`Signing in to ${serverLabel(target)}`);
  console.error(`Selected by: ${SELECTION_LABELS[target.source]}\n`);
  console.error(`Open: ${start.verification_uri}`);
  console.error(`Enter code: ${start.user_code}`);
  console.error("Check the account in your browser, then approve sign-in.");
  console.error("Waiting for approval...");
  if (options.browser !== false && process.stdout.isTTY) {
    openInBrowser(start.verification_uri);
  }

  const interval = (start.interval || 5) * 1000;
  const maxAttempts = Math.ceil((start.expires_in || 900) / (start.interval || 5));

  for (let i = 0; i < maxAttempts; i++) {
    await new Promise((resolve) => setTimeout(resolve, interval));

    const poll = await requestJson(
      "/auth/device/poll",
      { guard: isPollResult, invalid: "Server returned an invalid login response" },
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ device_code: start.device_code }),
      },
      { target, auth: "none", errors: { fallback: "Login failed" } }
    );

    if (poll.status === "pending") {
      continue;
    }

    if (poll.error) {
      throw new CliError(poll.error, `Try again: buzz login --server ${serverSelector(target)}`);
    }

    if (poll.status === "complete" && poll.token && poll.user) {
      const config = loadConfig();
      setCredential(config, target.url, poll.token);
      saveConfig(config);
      console.log(
        [
          `Signed in to ${serverLabel(target)} as @${poll.user.login}.`,
          ...defaultServerLines(config, target),
          ...remainingTokenLines(target),
        ].join("\n")
      );
      return;
    }
  }

  throw new CliError("Login timed out", `Try again: buzz login --server ${serverSelector(target)}`);
}

async function revokeSession(target: Target, token: string): Promise<boolean> {
  try {
    const response = await apiFetch(
      "/auth/logout",
      { method: "POST", headers: authHeaders(token) },
      { target, auth: "none" }
    );
    // 400 means the session is already invalid on the server.
    return response.ok || response.status === 400;
  } catch {
    return false;
  }
}

export async function logout(cliOptions: CliOptions = {}) {
  const config = loadConfig();
  const target = resolveTarget(cliOptions, config);
  const server = serverLabel(target);
  const token = getCredential(config, target.url);

  if (!token) {
    console.log([`No saved session for ${server}.`, ...remainingTokenLines(target)].join("\n"));
    return;
  }

  const revoked = await revokeSession(target, token);
  clearCredential(config, target.url);
  saveConfig(config);

  const result = revoked
    ? `Signed out of ${server}. Removed the saved session.`
    : `Removed the saved session for ${server}, but could not confirm it was revoked on the server.`;
  console.log([result, ...remainingTokenLines(target)].join("\n"));
}

interface WhoamiOptions {
  json?: boolean;
}

function jsonError(error: unknown) {
  if (error instanceof CliError) {
    return { code: error.code ?? "REQUEST_FAILED", message: error.message, tip: error.tip ?? null };
  }
  return { code: "REQUEST_FAILED", message: String(error), tip: null };
}

async function verifiedAccount(target: Target): Promise<AuthUser> {
  return requestJson(
    "/auth/me",
    { guard: isUser, invalid: "Server returned an invalid user response" },
    {},
    { target }
  );
}

async function whoamiJson(cliOptions: CliOptions): Promise<void> {
  let target: Target | undefined;
  let account: { login: string; name: string | null } | null = null;
  let error: ReturnType<typeof jsonError> | null = null;
  try {
    target = resolveTarget(cliOptions);
    const user = await verifiedAccount(target);
    account = { login: user.login, name: user.name ?? null };
  } catch (caught) {
    error = jsonError(caught);
    process.exitCode = 1;
  }
  const selected = target
    ? { name: target.name ?? null, url: target.url, source: target.source }
    : null;
  const authentication = { source: target?.credential ?? "none" };
  console.log(
    JSON.stringify(
      { schema_version: 1, selected_server: selected, authentication, account, error },
      null,
      2
    )
  );
}

export async function whoami(options: WhoamiOptions = {}, cliOptions: CliOptions = {}) {
  if (options.json) return whoamiJson(cliOptions);
  const target = resolveTarget(cliOptions);
  const user = await verifiedAccount(target);
  console.log(
    [
      `Signed in to ${serverLabel(target)} as @${user.login}${user.name ? ` (${user.name})` : ""}`,
      `Selected by: ${SELECTION_LABELS[target.source]}`,
      `Authentication: ${CREDENTIAL_LABELS[target.credential]}`,
    ].join("\n")
  );
}

export function registerAuthCommands(program: Command) {
  program
    .command("login")
    .description("Sign in to the selected server via your browser (keeps the default)")
    .option("--no-browser", "Print the approval URL and code without opening a browser")
    .addHelpText(
      "after",
      "\nExamples:\n  buzz login                 Sign in to the default server\n  buzz login --server work   Sign in to the server saved as 'work'"
    )
    .action((options: LoginOptions) => login(options, program.opts()));

  program
    .command("logout")
    .description("Sign out of the selected server and remove its saved session")
    .action(() => logout(program.opts()));

  program
    .command("whoami")
    .description("Verify which account the selected server signs you in as")
    .option("--json", "Print the result as JSON")
    .action((options: WhoamiOptions) => whoami(options, program.opts()));
}
