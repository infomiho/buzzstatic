import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { rmSync } from "node:fs";
import { CONFIG_PATH, loadConfig, saveConfig } from "../credentials.js";
import { login, logout, whoami } from "./auth.js";

const PERSONAL = "https://personal.example.com";
const WORK = "https://work.example.com";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function deviceServer(pollResult: unknown) {
  return vi.fn<typeof fetch>(async (input) => {
    const url = String(input);
    if (url.endsWith("/version")) return json({ version: "9.9.9", min_cli_version: "0.0.1" });
    if (url.endsWith("/auth/device")) {
      return json({ verification_uri: `${WORK}/device`, user_code: "ABCD-EFGH", device_code: "dc", interval: 0.001 });
    }
    if (url.endsWith("/auth/device/poll")) return json(pollResult);
    throw new Error(`unexpected request ${url}`);
  });
}

function output(spy: ReturnType<typeof vi.spyOn>): string {
  return spy.mock.calls.map((call: unknown[]) => call.join(" ")).join("\n");
}

let stdout: ReturnType<typeof vi.spyOn>;
let stderr: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  stdout = vi.spyOn(console, "log").mockImplementation(() => {});
  stderr = vi.spyOn(console, "error").mockImplementation(() => {});
  saveConfig({
    server: PERSONAL,
    servers: { personal: PERSONAL, work: WORK },
    credentials: { [PERSONAL]: "personal-session", [WORK]: "old-work-session" },
  });
});

afterEach(() => {
  rmSync(CONFIG_PATH, { force: true });
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  process.exitCode = undefined;
});

describe("login", () => {
  it("signs in to work and leaves the personal default and session alone", async () => {
    const fetchFn = deviceServer({ status: "complete", token: "new-work-session", user: { login: "me-work" } });
    vi.stubGlobal("fetch", fetchFn);

    await login({}, { server: "work" });

    expect(fetchFn.mock.calls.every(([url]) => String(url).startsWith(WORK))).toBe(true);
    expect(loadConfig()).toMatchObject({
      server: PERSONAL,
      credentials: { [PERSONAL]: "personal-session", [WORK]: "new-work-session" },
    });
    expect(output(stderr)).toContain("Signing in to work (https://work.example.com)\nSelected by: --server");
    expect(output(stdout)).toBe(
      "Signed in to work (https://work.example.com) as @me-work.\nDefault server: personal (https://personal.example.com)"
    );
  });

  it("keeps the existing session when approval fails", async () => {
    vi.stubGlobal("fetch", deviceServer({ error: "Device code expired" }));

    await expect(login({}, { server: "work" })).rejects.toMatchObject({
      message: "Device code expired",
      tip: "Try again: buzz login --server work",
    });
    expect(loadConfig().credentials?.[WORK]).toBe("old-work-session");
  });

  it("signs in to a URL on a fresh installation without setting a default", async () => {
    rmSync(CONFIG_PATH);
    vi.stubGlobal("fetch", deviceServer({ status: "complete", token: "t", user: { login: "me" } }));

    await login({}, { server: WORK });

    expect(loadConfig()).toEqual({ credentials: { [WORK]: "t" } });
    expect(output(stdout)).toContain(`Default server: none\nSet a default: buzz server default ${WORK}`);
  });

  it("explains that BUZZ_TOKEN still wins after signing in", async () => {
    vi.stubEnv("BUZZ_TOKEN", "env-token");
    vi.stubGlobal("fetch", deviceServer({ status: "complete", token: "t", user: { login: "me" } }));

    await login({}, { server: "work" });

    expect(output(stdout)).toContain("BUZZ_TOKEN is still set");
  });

  it("refuses browser sign-in in CI before any request", async () => {
    vi.stubEnv("CI", "true");
    const fetchFn = vi.fn<typeof fetch>();
    vi.stubGlobal("fetch", fetchFn);

    await expect(login({}, { server: "work" })).rejects.toMatchObject({ code: "LOGIN_IN_CI" });
    expect(fetchFn).not.toHaveBeenCalled();
  });
});

describe("logout", () => {
  it("removes only the selected server's session and keeps its name and the default", async () => {
    const fetchFn = vi.fn<typeof fetch>(async () => new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchFn);

    await logout({ server: "work" });

    expect(String(fetchFn.mock.calls[0][0])).toBe(`${WORK}/auth/logout`);
    expect(loadConfig()).toEqual({
      server: PERSONAL,
      servers: { personal: PERSONAL, work: WORK },
      credentials: { [PERSONAL]: "personal-session" },
    });
    expect(output(stdout)).toBe("Signed out of work (https://work.example.com). Removed the saved session.");
  });

  it("reports a failed revocation and a remaining BUZZ_TOKEN", async () => {
    vi.stubEnv("BUZZ_TOKEN", "env-token");
    vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => { throw new Error("offline"); }));

    await logout({ server: "work" });

    expect(output(stdout)).toContain("could not confirm it was revoked");
    expect(output(stdout)).toContain("BUZZ_TOKEN is still set");
  });
});

describe("whoami", () => {
  it("names the server and the freshly verified account", async () => {
    const fetchFn = vi.fn<typeof fetch>(async () => json({ login: "me-work", name: "Me" }));
    vi.stubGlobal("fetch", fetchFn);

    await whoami({}, { server: "work" });

    expect(String(fetchFn.mock.calls[0][0])).toBe(`${WORK}/auth/me`);
    expect(output(stdout)).toBe(
      "Signed in to work (https://work.example.com) as @me-work (Me)\nSelected by: --server\nAuthentication: saved session"
    );
  });

  it("returns verified account JSON", async () => {
    vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => json({ login: "me-work" })));

    await whoami({ json: true }, { server: "work" });

    expect(JSON.parse(output(stdout))).toEqual({
      schema_version: 1,
      selected_server: { name: "work", url: WORK, source: "flag" },
      authentication: { source: "saved_session" },
      account: { login: "me-work", name: null },
      error: null,
    });
  });

  it("reports a rejected session in JSON without inferring an account", async () => {
    vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => new Response(null, { status: 401 })));

    await whoami({ json: true }, { server: "work" });

    const result = JSON.parse(output(stdout));
    expect(result.account).toBeNull();
    expect(result.error).toMatchObject({ code: "AUTH_REJECTED", tip: "Run: buzz login --server work" });
    expect(process.exitCode).toBe(1);
  });
});
