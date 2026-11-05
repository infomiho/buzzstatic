import { Command } from "commander";
import { requestEmpty, type CliOptions } from "../client.js";
import { announcedTarget, serverLabel } from "../target.js";
import { confirm } from "../prompts.js";

export async function deleteSite(
  subdomain: string,
  options: { yes?: boolean },
  cliOptions: CliOptions = {}
) {
  const target = announcedTarget(cliOptions);
  if (!options.yes) {
    const confirmed = await confirm(`Delete site '${subdomain}' on ${serverLabel(target)}?`);
    if (!confirmed) {
      console.log("Aborted.");
      return;
    }
  }

  await requestEmpty(
    `/sites/${subdomain}`,
    [204],
    { method: "DELETE" },
    { target,
      errors: { notFound: `Site '${subdomain}' not found`, fallback: "Unknown error" },
    }
  );
  console.log(`Deleted ${subdomain}`);
}

export function registerDeleteCommand(program: Command) {
  program
    .command("delete <site>")
    .description("Delete a deployed site")
    .option("-y, --yes", "Skip confirmation prompt")
    .action((subdomain: string, options: { yes?: boolean }) =>
      deleteSite(subdomain, options, program.opts())
    );
}
