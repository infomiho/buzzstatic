import { Command } from "commander";
import { CliError } from "../errors.js";
import {
  CONFIG_PATH,
  getCredential,
  isServerName,
  loadConfig,
  normalizeServerUrl,
  saveConfig,
  serverNameFor,
  tryNormalize,
  type Config,
} from "../credentials.js";
import {
  CREDENTIAL_LABELS,
  SELECTION_LABELS,
  resolveSelector,
  selectServer,
  serverLabel,
  serverSelector,
  withCredential,
  type CliOptions,
  type CredentialSource,
  type Selection,
  type Server,
} from "../target.js";

interface ServerEntry {
  name: string | null;
  url: string;
  session_saved: boolean;
}

interface OverviewError {
  code: string;
  message: string;
  tip?: string;
}

export interface Overview {
  schema_version: 1;
  default_server: { name: string | null; url: string } | null;
  selected_server: { name: string | null; url: string; source: Selection["source"] } | null;
  authentication: { source: CredentialSource };
  servers: ServerEntry[] | null;
  error: OverviewError | null;
}

function toError(error: unknown): OverviewError {
  if (error instanceof CliError) {
    return { code: error.code ?? "ERROR", message: error.message, tip: error.tip };
  }
  return { code: "ERROR", message: error instanceof Error ? error.message : String(error) };
}

function knownServers(config: Config): ServerEntry[] {
  const urls = new Set<string>(Object.values(config.servers ?? {}));
  if (config.server) urls.add(tryNormalize(config.server) ?? config.server);
  for (const url of Object.keys(config.credentials ?? {})) urls.add(url);
  return [...urls].map((url) => ({
    name: serverNameFor(config, url) ?? null,
    url,
    session_saved: config.credentials?.[url] !== undefined,
  }));
}

export function inspectServers(cliOptions: CliOptions = {}): Overview {
  const overview: Overview = {
    schema_version: 1,
    default_server: null,
    selected_server: null,
    authentication: { source: "none" },
    servers: null,
    error: null,
  };

  let config: Config;
  try {
    config = loadConfig();
  } catch (error) {
    return { ...overview, error: toError(error) };
  }

  overview.servers = knownServers(config);
  if (config.server) {
    const url = tryNormalize(config.server) ?? config.server;
    overview.default_server = { name: serverNameFor(config, url) ?? null, url };
  }
  try {
    const selection = selectServer(cliOptions, config);
    if (selection) {
      overview.selected_server = { name: selection.name ?? null, url: selection.url, source: selection.source };
      overview.authentication.source = withCredential(selection, cliOptions, config).credential;
    }
  } catch (error) {
    overview.error = toError(error);
  }
  return overview;
}

function serverTable(overview: Overview): string[] {
  const servers = overview.servers ?? [];
  const nameWidth = Math.max(4, ...servers.map((server) => (server.name ?? "-").length)) + 2;
  const urlWidth = Math.max(3, ...servers.map((server) => server.url.length)) + 2;
  const row = (name: string, url: string, isDefault: string, session: string) =>
    `${name.padEnd(nameWidth)}${url.padEnd(urlWidth)}${isDefault.padEnd(9)}${session}`.trimEnd();
  return [
    row("NAME", "URL", "DEFAULT", "SESSION"),
    ...servers.map((server) =>
      row(
        server.name ?? "-",
        server.url,
        server.url === overview.default_server?.url ? "yes" : "",
        server.session_saved ? "saved" : "missing"
      )
    ),
  ];
}

function authenticationLine(overview: Overview): string {
  const { source } = overview.authentication;
  if (source === "saved_session" && overview.selected_server) {
    return `saved session for ${serverLabel(overview.selected_server)}`;
  }
  return CREDENTIAL_LABELS[source];
}

function targetLines(overview: Overview): string[] {
  const defaultServer = overview.default_server
    ? serverLabel(overview.default_server)
    : "none";
  const lines = [`Default server: ${defaultServer}`];
  const selected = overview.selected_server;
  if (!selected) {
    lines.push(`Selected server: ${overview.error ? "unresolved" : "none"}`);
    return lines;
  }
  lines.push(
    `Selected server: ${serverLabel(selected)}`,
    `Selected by: ${SELECTION_LABELS[selected.source]}`,
    `Authentication: ${authenticationLine(overview)}`,
    `Verify account: buzz whoami --server ${serverSelector(selected)}`
  );
  return lines;
}

const ADD_SERVER = "Add a server: buzz server add <name> <url> --default";

function exampleLines(overview: Overview): string[] {
  const servers = overview.servers ?? [];
  const other =
    servers.find((server) => server.url !== overview.selected_server?.url) ??
    (servers.length === 1 ? servers[0] : undefined);
  if (!other) return [ADD_SERVER];
  const selector = serverSelector(other);
  const isOther = other.url !== overview.selected_server?.url;
  return [
    isOther ? "Use another server for one command:" : "Target this server explicitly for one command:",
    `  buzz deploy ./dist --site my-site --server ${selector}`,
    "",
    "Sign in to that server:",
    `  buzz login --server ${selector}`,
    "",
    "Change the saved default:",
    `  buzz server default ${selector}`,
    "",
    "--server applies to one command. Signing in preserves the default.",
    "Session saved means a local credential exists. Use whoami to verify an account.",
  ];
}

export function formatOverview(overview: Overview): string {
  const sections: string[][] = [];
  sections.push(overview.servers?.length ? serverTable(overview) : ["No servers saved yet."]);
  sections.push(targetLines(overview));
  sections.push(exampleLines(overview));
  sections.push([`Config file: ${CONFIG_PATH}`]);
  return sections.map((lines) => lines.join("\n")).join("\n\n");
}

export function showServers(options: { json?: boolean }, cliOptions: CliOptions = {}): void {
  const overview = inspectServers(cliOptions);
  if (options.json) {
    console.log(JSON.stringify(overview, null, 2));
    if (overview.error) process.exitCode = 1;
    return;
  }
  const { error } = overview;
  if (overview.servers !== null) console.log(formatOverview(overview));
  if (error) throw new CliError(error.message, error.tip, error.code);
}

function shellOverrideNote(config: Config, server: Server): string[] {
  if (!process.env.BUZZ_SERVER?.trim()) return [];
  let selection: Selection | undefined;
  try {
    selection = selectServer({}, config);
  } catch (error) {
    const reason = error instanceof Error ? error.message : String(error);
    return [`BUZZ_SERVER is set but does not resolve: ${reason}`];
  }
  if (!selection || selection.url === server.url) return [];
  return [
    `Commands in this shell still target ${serverLabel(selection)} because BUZZ_SERVER is set.`,
    `Unset BUZZ_SERVER or pass --server ${serverSelector(server)}.`,
  ];
}

function invalidName(name: string): CliError {
  return new CliError(
    `Invalid server name '${name}'`,
    "Use lowercase letters, digits, and hyphens, starting with a letter",
    "INVALID_SERVER_NAME"
  );
}

function registerName(config: Config, name: string, url: string): boolean {
  const existing = config.servers?.[name];
  if (existing === url) return false;
  if (existing) {
    throw new CliError(
      `Server '${name}' already points to ${existing}`,
      `Remove it first: buzz server remove ${name}`
    );
  }
  const otherName = serverNameFor(config, url);
  if (otherName) {
    throw new CliError(
      `${url} is already saved as '${otherName}'`,
      `Use --server ${otherName}, or remove that name first`
    );
  }
  config.servers = { ...config.servers, [name]: url };
  return true;
}

export function addServer(name: string, rawUrl: string, options: { default?: boolean }): void {
  if (!isServerName(name)) throw invalidName(name);
  const url = normalizeServerUrl(rawUrl);
  const config = loadConfig();
  const server = { name, url };
  const added = registerName(config, name, url);
  if (options.default) config.server = url;
  saveConfig(config);

  const lines = [`${added ? "Saved" : "Already saved:"} server ${serverLabel(server)}.`];
  if (options.default) lines.push(`Default server: ${serverLabel(server)}`);
  if (!getCredential(config, url)) lines.push(`Sign in: buzz login --server ${name}`);
  console.log([...lines, ...shellOverrideNote(config, server)].join("\n"));
}

export function setDefaultServer(selector: string): void {
  const config = loadConfig();
  const server = resolveSelector(selector, config);
  config.server = server.url;
  saveConfig(config);
  console.log([`Default server: ${serverLabel(server)}`, ...shellOverrideNote(config, server)].join("\n"));
}

export function removeServer(name: string): void {
  const config = loadConfig();
  const url = config.servers?.[name];
  if (!url) {
    throw new CliError(`Unknown server '${name}'`, "See saved servers: buzz server", "UNKNOWN_SERVER");
  }
  const { [name]: _removed, ...rest } = config.servers!;
  config.servers = Object.keys(rest).length ? rest : undefined;
  saveConfig(config);

  const lines = [`Removed server name '${name}'.`];
  if (config.server === url) lines.push(`Default remains ${url}.`);
  if (getCredential(config, url)) {
    lines.push(`Saved session retained. Sign out with: buzz logout --server ${url}`);
  }
  console.log(lines.join("\n"));
}

export function registerServerCommand(program: Command): void {
  const server = program
    .command("server")
    .description("Show saved servers, the default, and which one commands target")
    .option("--json", "Print the overview as JSON")
    .action((options: { json?: boolean }) => showServers(options, program.opts()))
    // Subcommands only edit local config, so --json and --server do not apply to them.
    .configureHelp({ showGlobalOptions: false });
  server
    .command("add <name> <url>")
    .description("Save a server under a name, without signing in")
    .option("--default", "Also make it the default server")
    .action((name: string, url: string, options: { default?: boolean }) =>
      addServer(name, url, options)
    );
  server
    .command("default <name-or-url>")
    .description("Change the saved default server")
    .action(setDefaultServer);
  server
    .command("remove <name>")
    .description("Remove a server name, keeping its session and default")
    .action(removeServer);
}
