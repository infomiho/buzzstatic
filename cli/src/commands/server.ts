import { Command } from "commander";
import { CliError } from "../errors.js";
import { CONFIG_PATH, loadConfig, normalizeServerUrl, type Config } from "../credentials.js";
import {
  CREDENTIAL_LABELS,
  SELECTION_LABELS,
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

function tryNormalize(raw: string): string {
  try {
    return normalizeServerUrl(raw);
  } catch {
    return raw;
  }
}

function knownServers(config: Config): ServerEntry[] {
  const urls = new Set<string>();
  if (config.server) urls.add(tryNormalize(config.server));
  for (const url of Object.keys(config.credentials ?? {})) urls.add(url);
  return [...urls].map((url) => ({
    name: null,
    url,
    session_saved: config.credentials?.[url] !== undefined,
  }));
}

function nullableServer(server: Server): { name: string | null; url: string } {
  return { name: server.name ?? null, url: server.url };
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
    overview.default_server = { name: null, url: tryNormalize(config.server) };
  }
  try {
    const selection = selectServer(cliOptions, config);
    if (selection) {
      overview.selected_server = { ...nullableServer(selection), source: selection.source };
      overview.authentication.source = withCredential(selection, cliOptions, config).credential;
    }
  } catch (error) {
    overview.error = toError(error);
  }
  return overview;
}

function asServer(server: { name: string | null; url: string }): Server {
  return { url: server.url, name: server.name ?? undefined };
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
    return `saved session for ${serverLabel(asServer(overview.selected_server))}`;
  }
  return CREDENTIAL_LABELS[source];
}

function targetLines(overview: Overview): string[] {
  const defaultServer = overview.default_server
    ? serverLabel(asServer(overview.default_server))
    : "none";
  const lines = [`Default server: ${defaultServer}`];
  const selected = overview.selected_server;
  if (!selected) {
    lines.push(`Selected server: ${overview.error ? "unresolved" : "none"}`);
    return lines;
  }
  const server = asServer(selected);
  lines.push(
    `Selected server: ${serverLabel(server)}`,
    `Selected by: ${SELECTION_LABELS[selected.source]}`,
    `Authentication: ${authenticationLine(overview)}`,
    `Verify account: buzz whoami --server ${serverSelector(server)}`
  );
  return lines;
}

export function formatOverview(overview: Overview): string {
  const sections: string[][] = [];
  if (overview.servers?.length) {
    sections.push(serverTable(overview));
  } else {
    sections.push([
      "No servers saved yet.",
      "Set a default: buzz config server <url>",
    ]);
  }
  sections.push(targetLines(overview));
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

export function registerServerCommand(program: Command): void {
  const server = program
    .command("server")
    .description("Show saved servers and which one commands target");
  server
    .command("list", { isDefault: true })
    .description("Show saved servers, the default, and the current target (default)")
    .option("--json", "Print the overview as JSON")
    .action((options: { json?: boolean }) => showServers(options, program.opts()));
}
