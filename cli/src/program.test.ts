import { describe, expect, it } from "vitest";
import type { Command } from "commander";
import { createProgram } from "./program.js";

function commandPaths() {
  const program = createProgram();
  const help = program.createHelp();
  // visibleCommands drops hidden subcommands but adds Commander's implicit
  // `help`, which is not part of the CLI surface we document.
  const documented = (command: Command) =>
    help.visibleCommands(command).filter((child) => child.name() !== "help");
  return documented(program).flatMap((command) => [
    command.name(),
    ...documented(command).map((child) => `${command.name()} ${child.name()}`),
  ]);
}

describe("createProgram", () => {
  it("registers every public command", () => {
    expect(commandPaths()).toEqual([
      "deploy",
      "deployments",
      "deployments list",
      "deployments use",
      "list",
      "url",
      "delete",
      "access",
      "access status",
      "access private",
      "access public",
      "domains",
      "domains list",
      "domains add",
      "domains check",
      "domains retry",
      "domains cancel-transition",
      "domains remove",
      "tokens",
      "tokens list",
      "tokens create",
      "tokens delete",
      "server",
      "server add",
      "server default",
      "server remove",
      "login",
      "logout",
      "whoami",
      "config",
    ]);
  });

  it("keeps root help suitable for generated reference", () => {
    const help = createProgram().helpInformation();

    expect(help).toContain("Usage: buzz [options] [command]");
    expect(help).toContain("Saved server name or URL");
    expect(help).toContain("Session or deployment token");
    expect(help).toContain("deploy [options] <directory>");
  });

  it("points to server discovery from root help", () => {
    let help = "";
    const program = createProgram().configureOutput({ writeOut: (text) => (help += text) });

    program.outputHelp();

    expect(help).toContain("Inspect servers and the current target:   buzz server");
    expect(help).toContain("buzz login --server <name-or-url>");
  });
});
