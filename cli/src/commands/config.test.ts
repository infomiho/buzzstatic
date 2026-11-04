import { afterEach, describe, expect, it, vi } from "vitest";
import { rmSync } from "node:fs";
import { CONFIG_PATH, loadConfig, saveConfig } from "../credentials.js";
import { configCommand } from "./config.js";

afterEach(() => {
  rmSync(CONFIG_PATH, { force: true });
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
});

describe("config compatibility", () => {
  it("sets the default with 'config server' and explains a BUZZ_SERVER override", () => {
    saveConfig({ server: "https://a.example.com", servers: { work: "https://w.example.com" } });
    vi.stubEnv("BUZZ_SERVER", "https://a.example.com");
    const log = vi.spyOn(console, "log").mockImplementation(() => {});

    configCommand("server", "https://w.example.com/");

    expect(loadConfig().server).toBe("https://w.example.com");
    expect(log.mock.calls.join("\n")).toContain("because BUZZ_SERVER is set");
  });

  it("accepts a saved name", () => {
    saveConfig({ servers: { work: "https://w.example.com" } });
    vi.spyOn(console, "log").mockImplementation(() => {});

    configCommand("server", "work");

    expect(loadConfig().server).toBe("https://w.example.com");
  });

  it("shows the server overview without token fragments", () => {
    saveConfig({ server: "https://a.example.com", credentials: { "https://a.example.com": "buzz_sess_x" } });
    const log = vi.spyOn(console, "log").mockImplementation(() => {});

    configCommand();

    const output = log.mock.calls.join("\n");
    expect(output).toContain("Default server: https://a.example.com");
    expect(output).not.toContain("buzz_sess");
  });
});
