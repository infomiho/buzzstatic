import { describe, expect, it, vi } from "vitest";
import { resolveTarget } from "./target.js";
import type { Config } from "./credentials.js";

const config: Config = {
  server: "https://default.example.com",
  credentials: {
    "https://default.example.com": "default-session",
    "https://work.example.com": "work-session",
  },
};

describe("resolveTarget", () => {
  it("uses the saved default and its session", () => {
    expect(resolveTarget({}, config)).toMatchObject({
      url: "https://default.example.com",
      source: "default",
      credential: "saved_session",
      token: "default-session",
    });
  });

  it("prefers --server over BUZZ_SERVER over the default", () => {
    vi.stubEnv("BUZZ_SERVER", "https://env.example.com");

    expect(resolveTarget({}, config)).toMatchObject({ url: "https://env.example.com", source: "env" });
    expect(resolveTarget({ server: "https://work.example.com/" }, config)).toMatchObject({
      url: "https://work.example.com",
      source: "flag",
      token: "work-session",
    });
  });

  it("prefers --token over BUZZ_TOKEN over the saved session", () => {
    vi.stubEnv("BUZZ_TOKEN", "env-token");

    expect(resolveTarget({}, config)).toMatchObject({
      credential: "env",
      token: "env-token",
      hasSavedSession: true,
    });
    expect(resolveTarget({ token: "flag-token" }, config)).toMatchObject({
      credential: "flag",
      token: "flag-token",
      hasEnvToken: true,
    });
  });

  it.each([
    [{ server: "" }, "INVALID_SERVER_SELECTOR"],
    [{ server: "ftp://x.example.com" }, "INVALID_SERVER_SELECTOR"],
  ])("rejects %j instead of falling back", (cliOptions, code) => {
    expect(() => resolveTarget(cliOptions, config)).toThrow(expect.objectContaining({ code }));
  });

  it("names the source of an invalid selector", () => {
    vi.stubEnv("BUZZ_SERVER", "not a url");

    expect(() => resolveTarget({}, config)).toThrow("(from BUZZ_SERVER)");
  });

  it("selects nothing on a fresh installation", () => {
    expect(() => resolveTarget({}, {})).toThrow(
      expect.objectContaining({ code: "NO_SERVER_SELECTED" })
    );
  });
});
