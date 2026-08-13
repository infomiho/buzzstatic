import { Command } from "commander";
import { CliError, isRecord, requestJson, type CliOptions } from "../client.js";
import { resolveCurrentSite } from "../site.js";

interface SiteOption {
  site?: string;
}

interface SiteDeployment {
  deployment_number: number;
  deployed_at: string;
  size_bytes: number;
  source: "dashboard" | "api";
  actor: string;
  credential: string | null;
  active: boolean;
}

function isSiteDeployment(value: unknown): value is SiteDeployment {
  return (
    isRecord(value) &&
    Number.isInteger(value.deployment_number) &&
    value.deployment_number > 0 &&
    typeof value.deployed_at === "string" &&
    typeof value.size_bytes === "number" &&
    ["dashboard", "api"].includes(String(value.source)) &&
    typeof value.actor === "string" &&
    (value.credential === null || typeof value.credential === "string") &&
    typeof value.active === "boolean"
  );
}

function isSiteDeploymentArray(value: unknown): value is SiteDeployment[] {
  return Array.isArray(value) && value.every(isSiteDeployment);
}

function deploymentPath(site: string): string {
  return `/sites/${encodeURIComponent(site)}/deployments`;
}

export async function deployments(
  options: SiteOption,
  cliOptions: CliOptions = {}
): Promise<void> {
  const site = resolveCurrentSite(options.site);
  const rows = await requestJson(
    deploymentPath(site),
    {
      guard: isSiteDeploymentArray,
      invalid: "Server returned an invalid site-deployment response",
    },
    {},
    { cliOptions, errors: { notFound: `Site '${site}' not found` } }
  );
  if (rows.length === 0) {
    console.log(`No deployments for site '${site}'`);
    return;
  }
  console.log(
    `${"DEPLOYMENT".padEnd(18)} ${"DEPLOYED".padEnd(20)} DEPLOYED BY`
  );
  for (const row of rows) {
    const deployed = row.deployed_at.slice(0, 19).replace("T", " ");
    const deployment = `${row.deployment_number}${row.active ? " · Live" : ""}`;
    const source = row.source === "api" ? "API" : "Dashboard";
    const context = row.credential ? `${row.credential} · ${source}` : source;
    console.log(
      `${deployment.padEnd(18)} ${deployed.padEnd(20)} ${row.actor} (${context})`
    );
  }
}

export async function useDeployment(
  number: string,
  options: SiteOption,
  cliOptions: CliOptions = {}
): Promise<void> {
  const deploymentNumber = Number(number);
  if (!Number.isInteger(deploymentNumber) || deploymentNumber < 1) {
    throw new CliError("Deployment number must be a positive integer");
  }
  const site = resolveCurrentSite(options.site);
  await requestJson(
    `${deploymentPath(site)}/${deploymentNumber}/activate`,
    {
      guard: isSiteDeployment,
      invalid: "Server returned an invalid site-deployment response",
    },
    { method: "POST" },
    {
      cliOptions,
      errors: {
        notFound: `Deployment ${deploymentNumber} not found for site '${site}'`,
      },
    }
  );
  console.log(`Deployment ${deploymentNumber} is now live for site '${site}'.`);
}

const SITE_OPTION = "Site name (defaults to the current CNAME)";

export function registerDeploymentsCommands(program: Command): void {
  const deploymentCommands = program
    .command("deployments")
    .description("List deployments or make one live");
  deploymentCommands
    .command("list", { isDefault: true })
    .description("List deployments for a site")
    .option("--site <site>", SITE_OPTION)
    .action((options: SiteOption) => deployments(options, program.opts()));
  deploymentCommands
    .command("use <deployment-number>")
    .description("Make a deployment live for a site")
    .option("--site <site>", SITE_OPTION)
    .action((number: string, options: SiteOption) =>
      useDeployment(number, options, program.opts())
    );
}
