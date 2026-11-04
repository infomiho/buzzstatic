import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { CONFIG_PATH, loadConfig, saveConfig } from "../credentials.js";
import { deleteSite } from "./delete.js";
import { deploy } from "./deploy.js";
import { url } from "./url.js";

const PERSONAL = "https://personal.example.com";
const WORK = "https://work.example.com";

function fakeServers() {
  return vi.fn<typeof fetch>(async (input) => {
    const requested = String(input);
    if (requested.endsWith("/version")) {
      return Response.json({ version: "9.9.9", min_cli_version: "0.0.1" });
    }
    if (requested.endsWith("/deploy")) {
      return Response.json({ site_name: "demo", url: "https://demo.work.example.com", private: false });
    }
    return new Response(null, { status: 204 });
  });
}

let cwd: string;
let previousCwd: string;
let stdout: ReturnType<typeof vi.spyOn>;
let stderr: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  previousCwd = process.cwd();
  cwd = mkdtempSync(join(tmpdir(), "buzz-project-"));
  process.chdir(cwd);
  stdout = vi.spyOn(console, "log").mockImplementation(() => {});
  stderr = vi.spyOn(console, "error").mockImplementation(() => {});
  saveConfig({
    server: PERSONAL,
    servers: { personal: PERSONAL, work: WORK },
    credentials: { [PERSONAL]: "p", [WORK]: "w" },
  });
});

afterEach(() => {
  process.chdir(previousCwd);
  rmSync(CONFIG_PATH, { force: true });
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function requestedOrigins(fetchFn: ReturnType<typeof fakeServers>): string[] {
  return fetchFn.mock.calls.map(([input]) => new URL(String(input)).origin);
}

describe("one-command server overrides", () => {
  it("deploys the same site name only to the chosen server and announces it first", async () => {
    const fetchFn = fakeServers();
    vi.stubGlobal("fetch", fetchFn);
    mkdirSync(join(cwd, "dist"));
    writeFileSync(join(cwd, "dist", "index.html"), "hi");
    writeFileSync(join(cwd, "CNAME"), "demo\n");

    await deploy(join(cwd, "dist"), undefined, { server: "work" });

    expect(new Set(requestedOrigins(fetchFn))).toEqual(new Set([WORK]));
    const banner = stderr.mock.calls.map((call: unknown[]) => call.join(" ")).join("\n");
    expect(banner).toContain("Deploying 'demo' to work (https://work.example.com)");
    expect(banner).toContain("Selected by: --server · Authentication: saved session");
    expect(loadConfig().server).toBe(PERSONAL);
    expect(readFileSync(join(cwd, "CNAME"), "utf-8")).toBe("demo\n");
  });

  it("describes an unnamed deployment as assigned by the server", async () => {
    vi.stubGlobal("fetch", fakeServers());
    mkdirSync(join(cwd, "dist"));
    writeFileSync(join(cwd, "dist", "index.html"), "hi");

    await deploy(join(cwd, "dist"), undefined, {});

    expect(stderr.mock.calls.join("\n")).toContain(
      "Deploying a new site (name assigned by the server) to personal (https://personal.example.com)"
    );
  });

  it("deletes on the chosen server and names it in the confirmation", async () => {
    const fetchFn = fakeServers();
    vi.stubGlobal("fetch", fetchFn);

    await deleteSite("demo", { yes: true }, { server: "work" });

    expect(requestedOrigins(fetchFn)).toEqual([WORK]);
    expect(stderr.mock.calls.join("\n")).toContain("Server: work (https://work.example.com)");
    expect(stdout.mock.calls.join("\n")).toBe("Deleted demo");
  });

  it("keeps url output clean", () => {
    writeFileSync(join(cwd, "CNAME"), "demo\n");

    url({ server: "work" });

    expect(stdout.mock.calls).toEqual([["https://demo.work.example.com"]]);
    expect(stderr).not.toHaveBeenCalled();
  });
});
