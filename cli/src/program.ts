import { Command } from "commander";
import { registerCommands } from "./commands/index.js";
import { cliVersion } from "./version.js";

export function createProgram(): Command {
  const program = new Command();

  program
    .name("buzz")
    .description("Deploy static sites to a Buzz server")
    .version(cliVersion)
    .configureHelp({ showGlobalOptions: true })
    .option(
      "-s, --server <name-or-url>",
      "Saved server name or URL for this command (overrides BUZZ_SERVER and the saved default)"
    )
    .option(
      "-t, --token <token>",
      "Session or deployment token for this command (overrides BUZZ_TOKEN and the saved session)"
    );

  registerCommands(program);
  return program;
}
