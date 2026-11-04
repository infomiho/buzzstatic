import { Command } from "commander";
import { registerDeployCommand } from "./deploy.js";
import { registerListCommand } from "./list.js";
import { registerDeleteCommand } from "./delete.js";
import { registerConfigCommand } from "./config.js";
import { registerServerCommand } from "./server.js";
import { registerUrlCommand } from "./url.js";
import { registerAuthCommands } from "./auth.js";
import { registerTokensCommand } from "./tokens.js";
import { registerDomainsCommand } from "./domains.js";
import { registerAccessCommand } from "./access.js";
import { registerDeploymentsCommands } from "./deployments.js";

export function registerCommands(program: Command) {
  program.commandsGroup("Sites:");
  registerDeployCommand(program);
  registerDeploymentsCommands(program);
  registerListCommand(program);
  registerUrlCommand(program);
  registerDeleteCommand(program);

  program.commandsGroup("Site settings:");
  registerAccessCommand(program);
  registerDomainsCommand(program);

  program.commandsGroup("Automation:");
  registerTokensCommand(program);

  program.commandsGroup("Servers and account:");
  registerServerCommand(program);
  registerAuthCommands(program);
  registerConfigCommand(program);
}
