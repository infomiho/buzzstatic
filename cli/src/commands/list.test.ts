import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { list } from "./list.js";

const fetchMock = vi.fn<typeof fetch>();
const log = vi.spyOn(console, "log").mockImplementation(() => {});

describe("list", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockReset();
    log.mockClear();
  });

  afterEach(() => vi.unstubAllGlobals());

  it.each([
    ["current", { last_deployed_at: "2026-08-13T10:00:00" }],
    ["previous", { created: "2026-08-13T10:00:00" }],
  ])("accepts the %s server timestamp field", async (_contract, timestamp) => {
    fetchMock.mockResolvedValueOnce(Response.json([{
      name: "my-site",
      ...timestamp,
      size_bytes: 100,
      private: false,
    }]));

    await list({ server: "https://buzz.test", token: "token" });

    expect(log).toHaveBeenNthCalledWith(
      2,
      expect.stringContaining("2026-08-13 10:00:00"),
    );
  });
});
