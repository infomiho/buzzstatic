import { Command } from "commander";
import { writeFileSync } from "node:fs";
import { join } from "node:path";
import { getOptions, CliError, type CliOptions } from "../client.js";
import { checkServerCompatibility } from "../compat.js";
import { createProgressBar, createSpinner, formatSize } from "../lib.js";
import {
  resolveSiteName,
  packSite,
  uploadSite,
  type DeployResult,
} from "../deploy.js";

export async function deploy(
  directory: string,
  siteName: string | undefined,
  cliOptions: CliOptions = {},
  makePrivate = false
) {
  const options = getOptions(cliOptions);

  if (!options.token) {
    throw new CliError("Not authenticated", "Run 'buzz login' first");
  }

  await checkServerCompatibility(cliOptions);

  siteName = resolveSiteName(process.cwd(), directory, siteName);

  const progressBar = createProgressBar("Zipping");
  let progressStarted = false;

  let zipBuffer: Buffer;
  try {
    zipBuffer = await packSite(directory, (processed, total) => {
      if (!progressStarted && total > 0) {
        progressBar.start(total, 0);
        progressStarted = true;
      }
      if (progressStarted) {
        progressBar.update(processed);
      }
    });
  } finally {
    if (progressStarted) {
      progressBar.stop();
    }
  }

  console.log(`Compressed to ${formatSize(zipBuffer.length)}`);

  const uploadSpinner = createSpinner("Uploading");
  uploadSpinner.start();

  let result: DeployResult;
  try {
    result = await uploadSite(
      options.server,
      options.token,
      zipBuffer,
      siteName,
      globalThis.fetch,
      makePrivate
    );
    uploadSpinner.stop("✓ Uploaded");
  } catch (error) {
    uploadSpinner.stop("✗ Upload failed");
    throw error;
  }

  console.log(
    `Deployed to ${result.url} (${result.private ? "private" : "public"})`
  );
  if (result.deploymentNumber !== undefined) {
    console.log(`Deployment ${result.deploymentNumber}`);
  }
  writeFileSync(join(process.cwd(), "CNAME"), result.siteName + "\n");

  if (makePrivate && !result.private) {
    throw new CliError(
      `${result.url} was published publicly, but you asked for a private site.`,
      `Run 'buzz access private --site ${result.siteName}' to protect it, or 'buzz delete ${result.siteName}' to remove the public copy.`
    );
  }
}

export function registerDeployCommand(program: Command) {
  program
    .command("deploy <directory>")
    .description("Deploy a directory to the server")
    .option("--site <name>", "Site to deploy to (created if needed)")
    .option("--private", "Publish the site so only you can view it")
    .action(
      (directory: string, cmdOptions: { site?: string; private?: boolean }) =>
        deploy(directory, cmdOptions.site, program.opts(), cmdOptions.private)
    );
}
