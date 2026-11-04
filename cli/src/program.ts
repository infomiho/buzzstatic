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
  program.addHelpText(
    "after",
    [
      "",
      "Servers:",
      "  Inspect servers and the current target:   buzz server",
      "  Use another server for one command:       buzz <command> --server <name-or-url>",
      "  Change the saved default:                 buzz server default <name-or-url>",
      "  Sign in to a specific server:             buzz login --server <name-or-url>",
    ].join("\n")
  );
  return program;
}
