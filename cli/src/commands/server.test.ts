import { afterEach, describe, expect, it, vi } from "vitest";
import { rmSync, writeFileSync } from "node:fs";
import { CONFIG_PATH, loadConfig, saveConfig } from "../credentials.js";
import { resolveTarget } from "../target.js";
import { addServer, inspectServers, removeServer, setDefaultServer, showServers } from "./server.js";

function captureStdout(): () => string {
  const log = vi.spyOn(console, "log").mockImplementation(() => {});
  return () => log.mock.calls.map((call) => call.join(" ")).join("\n");
}

afterEach(() => {
  rmSync(CONFIG_PATH, { force: true });
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
  process.exitCode = undefined;
});

describe("server overview", () => {
  it("lists known URLs once with local session state and no token fragments", () => {
    saveConfig({
      server: "https://a.example.com",
      credentials: { "https://a.example.com": "buzz_sess_secret_a", "https://b.example.com": "buzz_sess_secret_b" },
    });
    vi.stubEnv("BUZZ_SERVER", "https://b.example.com");
    const stdout = captureStdout();

    showServers({});

    const output = stdout();
    expect(output).toMatch(/-\s+https:\/\/a\.example\.com\s+yes\s+saved/);
    expect(output).toMatch(/-\s+https:\/\/b\.example\.com\s+saved/);
    expect(output).toContain("Default server: https://a.example.com");
    expect(output).toContain("Selected server: https://b.example.com");
    expect(output).toContain("Selected by: BUZZ_SERVER");
    expect(output).toContain("Authentication: saved session for https://b.example.com");
    expect(output).not.toContain("buzz_sess");
  });

  it("returns the versioned JSON contract as a single object", () => {
    saveConfig({ server: "https://a.example.com", credentials: { "https://a.example.com": "secret" } });
    const stdout = captureStdout();

    showServers({ json: true }, { server: "https://c.example.com" });

    expect(JSON.parse(stdout())).toEqual({
      schema_version: 1,
      default_server: { name: null, url: "https://a.example.com" },
      selected_server: { name: null, url: "https://c.example.com", source: "flag" },
      authentication: { source: "none" },
      servers: [{ name: null, url: "https://a.example.com", session_saved: true }],
      error: null,
    });
    expect(process.exitCode).toBeUndefined();
  });

  it("succeeds on a fresh installation without selecting localhost", () => {
    const overview = inspectServers();

    expect(overview).toMatchObject({ selected_server: null, servers: [], error: null });
  });

  it("reports an invalid selector with the readable local state", () => {
    saveConfig({ server: "https://a.example.com" });
    vi.stubEnv("BUZZ_SERVER", "not a url");
    const stdout = captureStdout();

    showServers({ json: true });

    const overview = JSON.parse(stdout());
    expect(overview.selected_server).toBeNull();
    expect(overview.servers).toHaveLength(1);
    expect(overview.error.code).toBe("INVALID_SERVER_SELECTOR");
    expect(process.exitCode).toBe(1);
  });

  it("reports unreadable config as no servers instead of an empty list", () => {
    writeFileSync(CONFIG_PATH, "{broken");
    const stdout = captureStdout();

    showServers({ json: true });

    const overview = JSON.parse(stdout());
    expect(overview.servers).toBeNull();
    expect(overview.error.code).toBe("CONFIG_UNREADABLE");
  });
});

const PERSONAL = "https://personal.example.com";
const WORK = "https://work.example.com";

describe("server names and default", () => {
  it("adds a server without changing the default or creating a session", () => {
    saveConfig({ server: PERSONAL, credentials: { [PERSONAL]: "p" } });
    const stdout = captureStdout();

    addServer("work", "HTTPS://Work.Example.com/", {});

    expect(loadConfig()).toEqual({
      server: PERSONAL,
      servers: { work: WORK },
      credentials: { [PERSONAL]: "p" },
    });
    expect(stdout()).toContain("Sign in: buzz login --server work");
  });

  it("sets the default only when asked", () => {
    addServer("personal", PERSONAL, { default: true });

    expect(loadConfig().server).toBe(PERSONAL);
  });

  it("names a migrated URL while keeping its session and default", () => {
    saveConfig({ server: WORK, credentials: { [WORK]: "w" } });
    captureStdout();

    addServer("work", WORK, {});

    expect(loadConfig()).toMatchObject({ server: WORK, credentials: { [WORK]: "w" } });
  });

  it("is idempotent for the same name and URL", () => {
    captureStdout();
    addServer("work", WORK, {});

    expect(() => addServer("work", `${WORK}/`, {})).not.toThrow();
  });

  it("rejects a name that points elsewhere or a second name for one URL", () => {
    captureStdout();
    addServer("work", WORK, {});

    expect(() => addServer("work", PERSONAL, {})).toThrow("already points to");
    expect(() => addServer("job", WORK, {})).toThrow("already saved as 'work'");
    expect(() => addServer("Work", PERSONAL, {})).toThrow("Invalid server name");
  });

  it("targets a named server for one command and keeps the default", () => {
    saveConfig({ server: PERSONAL, servers: { work: WORK }, credentials: { [WORK]: "w" } });

    expect(resolveTarget({ server: "work" })).toMatchObject({ url: WORK, name: "work", token: "w" });
    expect(loadConfig().server).toBe(PERSONAL);
  });

  it("explains that BUZZ_SERVER still wins after changing the default", () => {
    saveConfig({ server: PERSONAL, servers: { personal: PERSONAL, work: WORK } });
    vi.stubEnv("BUZZ_SERVER", "personal");
    const stdout = captureStdout();

    setDefaultServer("work");

    expect(loadConfig().server).toBe(WORK);
    expect(stdout()).toContain(
      "Commands in this shell still target personal (https://personal.example.com) because BUZZ_SERVER is set."
    );
  });

  it("warns when removing the name BUZZ_SERVER uses", () => {
    saveConfig({ server: PERSONAL, servers: { work: WORK } });
    vi.stubEnv("BUZZ_SERVER", "work");
    const stdout = captureStdout();

    removeServer("work");

    expect(stdout()).toContain("BUZZ_SERVER is set but does not resolve");
  });

  it("does not tell the user to unset BUZZ_SERVER after a plain add", () => {
    saveConfig({ server: PERSONAL, servers: { personal: PERSONAL } });
    vi.stubEnv("BUZZ_SERVER", "personal");
    const stdout = captureStdout();

    addServer("work", WORK, {});

    expect(stdout()).not.toContain("Unset BUZZ_SERVER");
  });

  it("keeps the default URL and session when removing a name", () => {
    saveConfig({ server: WORK, servers: { work: WORK }, credentials: { [WORK]: "w" } });
    const stdout = captureStdout();

    removeServer("work");

    expect(loadConfig()).toEqual({ server: WORK, credentials: { [WORK]: "w" } });
    expect(stdout()).toContain(`Default remains ${WORK}.`);
    expect(stdout()).toContain(`buzz logout --server ${WORK}`);
  });

  it("does not move a session or default when a removed name is reused", () => {
    saveConfig({ server: WORK, servers: { work: WORK }, credentials: { [WORK]: "w" } });
    captureStdout();

    removeServer("work");
    addServer("work", PERSONAL, {});

    expect(loadConfig()).toMatchObject({ server: WORK, credentials: { [WORK]: "w" } });
    expect(resolveTarget({ server: "work" })).toMatchObject({ url: PERSONAL, credential: "none" });
  });

  it("repairs an unresolved BUZZ_SERVER name by registering it", () => {
    saveConfig({ server: PERSONAL });
    vi.stubEnv("BUZZ_SERVER", "work");
    expect(() => resolveTarget()).toThrow(expect.objectContaining({ code: "UNKNOWN_SERVER" }));
    captureStdout();

    addServer("work", WORK, {});

    expect(resolveTarget()).toMatchObject({ url: WORK, source: "env" });
    expect(loadConfig().server).toBe(PERSONAL);
  });

  it("teaches overrides with names, or URLs for unnamed servers", () => {
    saveConfig({ server: PERSONAL, servers: { work: WORK }, credentials: { [PERSONAL]: "p" } });
    let stdout = captureStdout();
    showServers({});
    expect(stdout()).toContain("buzz deploy ./dist --site my-site --server work");
    expect(stdout()).toContain("buzz server default work");

    vi.restoreAllMocks();
    saveConfig({ server: PERSONAL, credentials: { [PERSONAL]: "p", [WORK]: "w" } });
    stdout = captureStdout();
    showServers({});
    expect(stdout()).toContain(`buzz login --server ${WORK}`);
  });
});
