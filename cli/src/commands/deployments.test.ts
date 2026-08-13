import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { deployments, useDeployment } from "./deployments.js";
import { createProgram } from "../program.js";

const fetchMock = vi.fn<typeof fetch>();
const log = vi.spyOn(console, "log").mockImplementation(() => {});

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("site deployments", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockReset();
    log.mockClear();
  });

  afterEach(() => vi.unstubAllGlobals());

  it("lists deployments for an explicit site", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse([{
      deployment_number: 2,
      deployed_at: "2026-08-13T10:00:00",
      size_bytes: 1024,
      source: "api",
      actor: "alice",
      credential: null,
      active: true,
    }]));

    await deployments({ site: "my-site" }, { server: "https://buzz.test", token: "token" });

    expect(fetchMock).toHaveBeenCalledWith(
      "https://buzz.test/sites/my-site/deployments",
      expect.anything(),
    );
    expect(log).toHaveBeenNthCalledWith(
      1,
      "DEPLOYMENT         DEPLOYED             DEPLOYED BY",
    );
    expect(log).toHaveBeenNthCalledWith(
      2,
      "2 · Live           2026-08-13 10:00:00  alice (API)",
    );
  });

  it("makes a deployment live", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({
      deployment_number: 1,
      deployed_at: "2026-08-12T10:00:00",
      size_bytes: 900,
      source: "api",
      actor: "alice",
      credential: "Production CI",
      active: true,
    }));

    await useDeployment("1", { site: "my-site" }, { server: "https://buzz.test", token: "token" });

    expect(fetchMock).toHaveBeenCalledWith(
      "https://buzz.test/sites/my-site/deployments/1/activate",
      expect.objectContaining({ method: "POST" }),
    );
    expect(log).toHaveBeenCalledWith("Deployment 1 is now live for site 'my-site'.");
  });

  it("routes argv and root connection options to make a deployment live", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({
      deployment_number: 3,
      deployed_at: "2026-08-13T10:00:00",
      size_bytes: 1024,
      source: "api",
      actor: "alice",
      credential: null,
      active: true,
    }));

    await createProgram().parseAsync([
      "node",
      "buzz",
      "--server",
      "https://buzz.test",
      "--token",
      "token",
      "deployments",
      "use",
      "3",
      "--site",
      "my-site",
    ]);

    expect(fetchMock).toHaveBeenCalledWith(
      "https://buzz.test/sites/my-site/deployments/3/activate",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({ Authorization: "Bearer token" }),
      }),
    );
  });

  it("rejects an invalid deployment number", async () => {
    await expect(useDeployment("1.5", { site: "my-site" })).rejects.toThrow(
      "Deployment number must be a positive integer",
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
