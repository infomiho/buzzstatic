import { describe, expect, it, vi } from "vitest";
import { rejectedCredential, resolveTarget, runningInCi, sessionRequired } from "./target.js";
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

describe("credential recovery", () => {
  const work = { server: "https://work.example.com" };

  it.each([
    [{}, {}, "Your saved session for https://work.example.com was rejected", "Run: buzz login --server https://work.example.com"],
    [{ BUZZ_TOKEN: "env" }, {}, "The token from BUZZ_TOKEN was rejected", "Update BUZZ_TOKEN, or unset it to use the saved session"],
    [{}, { token: "flag" }, "The token from --token was rejected", "Replace --token, or omit it to use the saved session"],
    [{ BUZZ_TOKEN: "env" }, { token: "flag" }, "The token from --token was rejected", "Replace --token, or omit it to use BUZZ_TOKEN"],
  ])("explains a rejected credential with env %j and flags %j", (env, flags, message, tip) => {
    for (const [key, value] of Object.entries(env)) vi.stubEnv(key, value);

    const error = rejectedCredential(resolveTarget({ ...work, ...flags }, config));

    expect(error.message).toContain(message);
    expect(error.tip).toBe(tip);
  });

  it("offers login when unsetting BUZZ_TOKEN would leave no credential", () => {
    vi.stubEnv("BUZZ_TOKEN", "env");

    const target = resolveTarget({ server: "https://new.example.com" }, config);

    expect(rejectedCredential(target).tip).toBe(
      "Update BUZZ_TOKEN, or unset it and run: buzz login --server https://new.example.com"
    );
  });

  it("uses the saved name in recovery commands", () => {
    const named = { ...config, servers: { work: "https://work.example.com" } };

    const target = resolveTarget({ server: "work" }, named);

    expect(rejectedCredential(target)).toMatchObject({
      message: "Your saved session for work (https://work.example.com) was rejected",
      tip: "Run: buzz login --server work",
    });
  });
});

describe("session-only operations", () => {
  it.each([
    [{ BUZZ_TOKEN: "deploy" }, {}, "https://new.example.com", "Unset BUZZ_TOKEN, then run: buzz login --server https://new.example.com"],
    [{ BUZZ_TOKEN: "deploy" }, {}, "https://work.example.com", "Unset BUZZ_TOKEN to use the saved session"],
    [{ BUZZ_TOKEN: "deploy" }, { token: "t" }, "https://new.example.com", "Omit --token and unset BUZZ_TOKEN, then run: buzz login --server https://new.example.com"],
  ])("recovers from env %j and flags %j on %s", (env, flags, server, tip) => {
    for (const [key, value] of Object.entries(env)) vi.stubEnv(key, value);

    expect(sessionRequired(resolveTarget({ server, ...flags }, config), "x").tip).toBe(tip);
  });

  it("does not suggest browser sign-in in CI", () => {
    vi.stubEnv("CI", "true");
    vi.stubEnv("BUZZ_TOKEN", "deploy");
    const target = resolveTarget({ server: "https://work.example.com" }, config);

    expect(rejectedCredential(target).tip).toBe(
      "Update the BUZZ_TOKEN secret with a valid deployment token for https://work.example.com"
    );
    expect(sessionRequired(target, "x").tip).not.toContain("buzz login");
  });

  it("treats CI=false as a local terminal", () => {
    vi.stubEnv("CI", "false");

    expect(runningInCi()).toBe(false);
  });
});
