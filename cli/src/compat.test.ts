import { describe, expect, it } from "vitest";
import { checkServerCompatibility } from "./compat.js";
import { CliError } from "./errors.js";

const cliOptions = { server: "https://buzz.example.com" };

function fetchOnce(response: Response): typeof fetch {
  return async () => response;
}

describe("checkServerCompatibility", () => {
  it("passes when the CLI meets the server minimum", async () => {
    await expect(
      checkServerCompatibility(
        cliOptions,
        fetchOnce(Response.json({ version: "9.9.9", min_cli_version: "0.1.0" }))
      )
    ).resolves.toBeUndefined();
  });

  it("rejects a CLI older than the server minimum", async () => {
    const check = checkServerCompatibility(
      cliOptions,
      fetchOnce(Response.json({ version: "9.9.9", min_cli_version: "99.0.0" }))
    );

    await expect(check).rejects.toThrow(CliError);
    await expect(check).rejects.toThrow(/minimum version this server supports/);
  });

  it.each([
    ["missing endpoint", fetchOnce(new Response("Not Found", { status: 404 }))],
    ["malformed response", fetchOnce(Response.json({ status: "ok" }))],
    ["unreachable server", async () => { throw new Error("connect ECONNREFUSED"); }],
  ] as const)("skips a %s", async (_case, fetchFn) => {
    await expect(
      checkServerCompatibility(cliOptions, fetchFn)
    ).resolves.toBeUndefined();
  });
});
