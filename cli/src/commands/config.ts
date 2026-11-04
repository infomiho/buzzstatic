import { Command } from "commander";
import { CliError } from "../lib.js";
import { loadConfig, normalizeServerUrl, saveConfig } from "../credentials.js";
import type { CliOptions } from "../target.js";
import { showServers } from "./server.js";

export function configCommand(key?: string, value?: string, cliOptions: CliOptions = {}) {
  if (!key) {
    showServers({}, cliOptions);
    return;
  }

  if (key === "server" && value) {
    const config = loadConfig();
    const server = normalizeServerUrl(value);
    config.server = server;
    saveConfig(config);
    console.log(`Server set to ${server}`);
  } else {
    throw new CliError(
      "Invalid config command",
      "Usage: buzz config server <url>"
    );
  }
}

export function registerConfigCommand(program: Command) {
  program
    .command("config [key] [value]")
    .description("View or set configuration (server)")
    .action((key?: string, value?: string) => configCommand(key, value, program.opts()));
}
