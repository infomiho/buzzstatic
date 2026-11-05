import { Command } from "commander";
import {
  CliError,
  isRecord,
  requestEmpty,
  requestJson,
  sessionOnly,
  type ApiErrors,
  type CliOptions,
  type Target,
} from "../client.js";
import { announcedTarget, serverLabel } from "../target.js";
import { confirm } from "../prompts.js";
import { resolveCurrentSite } from "../site.js";

interface AccessState {
  private: boolean;
}

interface SiteOption {
  site?: string;
}

interface PublicOptions extends SiteOption {
  yes?: boolean;
}

interface PublicDependencies {
  confirm: (message: string) => Promise<boolean>;
}

const accessErrors: ApiErrors = {
  forbidden: sessionOnly("manage site access"),
};

function isAccessState(value: unknown): value is AccessState {
  return isRecord(value) && typeof value.private === "boolean";
}

function accessPath(site: string): string {
  return `/sites/${encodeURIComponent(site)}/access`;
}

function printAccess(site: string, state: AccessState): void {
  console.log(`${site}: ${state.private ? "private" : "public"}`);
}

async function reportVisibility(
  site: string,
  target: Target,
  init: RequestInit,
  fallback: string
): Promise<void> {
  const state = await requestJson(
    accessPath(site),
    { guard: isAccessState, invalid: "Server returned an invalid site-access response" },
    init,
    { target,
      errors: { ...accessErrors, notFound: `Site '${site}' not found`, fallback },
    }
  );
  printAccess(site, state);
}

export async function accessStatus(
  options: SiteOption,
  cliOptions: CliOptions = {}
): Promise<void> {
  const site = resolveCurrentSite(options.site);
  await reportVisibility(site, announcedTarget(cliOptions), {}, "Could not get site access");
}

export async function makePrivate(
  options: SiteOption,
  cliOptions: CliOptions = {}
): Promise<void> {
  const site = resolveCurrentSite(options.site);
  await reportVisibility(
    site,
    announcedTarget(cliOptions),
    { method: "PUT" },
    "Could not make the site private"
  );
}

export async function makePublic(
  options: PublicOptions,
  cliOptions: CliOptions = {},
  dependencies: PublicDependencies = { confirm }
): Promise<void> {
  const target = announcedTarget(cliOptions);
  const site = resolveCurrentSite(options.site);
  if (!options.yes && !(await dependencies.confirm(`Make '${site}' public on ${serverLabel(target)}?`))) {
    console.log("Aborted.");
    return;
  }
  await requestEmpty(accessPath(site), [204], { method: "DELETE" }, { target,
    errors: {
      ...accessErrors,
      notFound: `Site '${site}' not found`,
      fallback: "Could not make the site public",
    },
  });
  printAccess(site, { private: false });
}

const SITE_OPTION = "Site name (defaults to the current CNAME)";

export function registerAccessCommand(program: Command): void {
  const access = program
    .command("access")
    .description("Show or change who can view a site");
  // Status is a default subcommand rather than an action on `access` itself:
  // an option declared on the parent shadows the same option on every
  // subcommand, so `access public --site x` would lose its site.
  access
    .command("status", { isDefault: true })
    .description("Show who can view this site (default)")
    .option("--site <site>", SITE_OPTION)
    .action((options: SiteOption) => accessStatus(options, program.opts()));
  access
    .command("private")
    .description("Let only the site owner view this site")
    .option("--site <site>", SITE_OPTION)
    .action((options: SiteOption) => makePrivate(options, program.opts()));
  access
    .command("public")
    .description("Let anyone view this site")
    .option("--site <site>", SITE_OPTION)
    .option("-y, --yes", "Skip confirmation prompt")
    .action((options: PublicOptions) => makePublic(options, program.opts()));
}
