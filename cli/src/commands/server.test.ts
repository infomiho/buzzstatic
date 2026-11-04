import { afterEach, describe, expect, it, vi } from "vitest";
import { rmSync, writeFileSync } from "node:fs";
import { CONFIG_PATH, saveConfig } from "../credentials.js";
import { inspectServers, showServers } from "./server.js";

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
