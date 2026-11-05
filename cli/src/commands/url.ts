import { Command } from "commander";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { CliError, type CliOptions } from "../client.js";
import { resolveTarget } from "../target.js";

export function url(cliOptions: CliOptions = {}) {
  const cnamePath = join(process.cwd(), "CNAME");
  if (!existsSync(cnamePath)) {
    throw new CliError("No CNAME file found", "Deploy first with: buzz deploy .");
  }
  const subdomain = readFileSync(cnamePath, "utf-8").trim();
  const server = new URL(resolveTarget(cliOptions).url);
  console.log(`${server.protocol}//${subdomain}.${server.host}`);
}

export function registerUrlCommand(program: Command) {
  program
    .command("url")
    .description("Show the URL for the current directory")
    .action(() => url(program.opts()));
}
