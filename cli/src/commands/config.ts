import { Command } from "commander";
import { CliError } from "../lib.js";
import type { CliOptions } from "../target.js";
import { setDefaultServer, showServers } from "./server.js";

export function configCommand(key?: string, value?: string, cliOptions: CliOptions = {}) {
  if (!key) {
    showServers({}, cliOptions);
  } else if (key === "server" && value) {
    setDefaultServer(value);
  } else {
    throw new CliError("Invalid config command", "Usage: buzz config server <name-or-url>");
  }
}

export function registerConfigCommand(program: Command) {
  program
    .command("config [key] [value]")
    .description("Show servers, or set the default with 'config server <url>' (same as 'server default')")
    .action((key?: string, value?: string) => configCommand(key, value, program.opts()));
}
