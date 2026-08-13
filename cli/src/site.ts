import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { CliError } from "./client.js";

export function resolveCurrentSite(site?: string): string {
  if (site?.trim()) return site.trim();

  const cnamePath = join(process.cwd(), "CNAME");
  if (!existsSync(cnamePath)) {
    throw new CliError(
      "No CNAME file found",
      "Deploy first with 'buzz deploy .' or pass --site <site>"
    );
  }
  const cname = readFileSync(cnamePath, "utf8").trim();
  if (!cname) throw new CliError("CNAME file is empty", "Pass --site <site>");
  return cname;
}
