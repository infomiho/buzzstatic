import { Command } from "commander";
import { writeFileSync } from "node:fs";
import { join } from "node:path";
import { CliError, type CliOptions } from "../client.js";
import { missingCredential, resolveTarget, serverLabel, targetSummary } from "../target.js";
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
  const target = resolveTarget(cliOptions);

  if (!target.token) throw missingCredential(target);

  await checkServerCompatibility(target);

  siteName = resolveSiteName(process.cwd(), directory, siteName);
  const site = siteName ? `'${siteName}'` : "a new site (name assigned by the server)";
  console.error(`Deploying ${site} to ${serverLabel(target)}`);
  console.error(targetSummary(target));

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

  console.error(`Compressed to ${formatSize(zipBuffer.length)}`);

  const uploadSpinner = createSpinner("Uploading");
  uploadSpinner.start();

  let result: DeployResult;
  try {
    result = await uploadSite(
      target,
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
