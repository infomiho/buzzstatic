import { describe, it, expect, vi } from "vitest";
import { mkdtempSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { resolveSiteName, packSite, uploadSite } from "./deploy.js";

function makeTmpDir(): string {
  return mkdtempSync(join(tmpdir(), "buzz-test-"));
}

describe("resolveSiteName", () => {
  it("returns explicit arg when provided", () => {
    const cwd = makeTmpDir();
    const dir = makeTmpDir();
    writeFileSync(join(cwd, "CNAME"), "from-cwd\n");

    expect(resolveSiteName(cwd, dir, "explicit")).toBe("explicit");
  });

  it("falls back to cwd CNAME", () => {
    const cwd = makeTmpDir();
    const dir = makeTmpDir();
    writeFileSync(join(cwd, "CNAME"), "from-cwd\n");

    expect(resolveSiteName(cwd, dir)).toBe("from-cwd");
  });

  it("falls back to directory CNAME when no cwd CNAME", () => {
    const cwd = makeTmpDir();
    const dir = makeTmpDir();
    writeFileSync(join(dir, "CNAME"), "from-dir\n");

    expect(resolveSiteName(cwd, dir)).toBe("from-dir");
  });

  it("prefers cwd CNAME over directory CNAME", () => {
    const cwd = makeTmpDir();
    const dir = makeTmpDir();
    writeFileSync(join(cwd, "CNAME"), "from-cwd\n");
    writeFileSync(join(dir, "CNAME"), "from-dir\n");

    expect(resolveSiteName(cwd, dir)).toBe("from-cwd");
  });

  it("returns undefined when no CNAME exists", () => {
    const cwd = makeTmpDir();
    const dir = makeTmpDir();

    expect(resolveSiteName(cwd, dir)).toBeUndefined();
  });
});

describe("packSite", () => {
  it("throws for nonexistent directory", async () => {
    await expect(packSite("/tmp/does-not-exist-xyz")).rejects.toThrow(
      "does not exist"
    );
  });

  it("throws for a file instead of directory", async () => {
    const dir = makeTmpDir();
    const file = join(dir, "not-a-dir.txt");
    writeFileSync(file, "hello");

    await expect(packSite(file)).rejects.toThrow("is not a directory");
  });

  it("calls onProgress callback", async () => {
    const dir = makeTmpDir();
    writeFileSync(join(dir, "a.txt"), "aaa");
    writeFileSync(join(dir, "b.txt"), "bbb");

    const calls: Array<[number, number]> = [];
    await packSite(dir, (processed, total) => {
      calls.push([processed, total]);
    });

    expect(calls.length).toBeGreaterThan(0);
  });
});

function fakeFetch(status: number, body: object): typeof fetch {
  return async () =>
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    });
}

describe("uploadSite", () => {
  const zip = Buffer.from("fake-zip-content");

  it("returns the explicit site name on success", async () => {
    const result = await uploadSite(
      "http://localhost:8080",
      "test-token",
      zip,
      "my-site",
      fakeFetch(200, {
        site_name: "my-site",
        url: "https://custom.example.com",
        private: false,
        deployment_number: 3,
      })
    );

    expect(result.url).toBe("https://custom.example.com");
    expect(result.siteName).toBe("my-site");
    expect(result.private).toBe(false);
    expect(result.deploymentNumber).toBe(3);
  });

  it("accepts the previous server response while packages roll out", async () => {
    const result = await uploadSite(
      "http://localhost:8080",
      "test-token",
      zip,
      "my-site",
      fakeFetch(200, {
        name: "my-site",
        url: "https://my-site.example.com",
        private: false,
      })
    );

    expect(result.siteName).toBe("my-site");
    expect(result.deploymentNumber).toBeUndefined();
  });

  it("asks for a private site with the deployment", async () => {
    const fetchFn = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({ site_name: "my-site", url: "https://my-site.example.com", private: false, deployment_number: 1 }),
        { status: 200, headers: { "Content-Type": "application/json" } }
      )
    );

    await uploadSite(
      "https://buzz.example.com",
      "test-token",
      zip,
      "my-site",
      fetchFn,
      true
    );

    expect(fetchFn).toHaveBeenCalledWith(
      "https://buzz.example.com/deploy",
      expect.objectContaining({
        headers: expect.objectContaining({
          "x-buzz-access": "private",
        }),
      })
    );
  });

  it("omits the access header for a public deployment", async () => {
    const fetchFn = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({ site_name: "my-site", url: "https://my-site.example.com", private: false, deployment_number: 1 }),
        { status: 200, headers: { "Content-Type": "application/json" } }
      )
    );

    await uploadSite("https://buzz.example.com", "test-token", zip, "my-site", fetchFn);

    const headers = (fetchFn.mock.calls[0][1] as RequestInit)
      .headers as Record<string, string>;
    expect(headers).not.toHaveProperty("x-buzz-access");
  });

  it("rejects a deployment response without a site name", async () => {
    await expect(
      uploadSite(
        "http://localhost:8080",
        "test-token",
        zip,
        undefined,
        fakeFetch(200, { url: "https://custom.example.com" })
      )
    ).rejects.toThrow("Server returned an invalid deployment response");
  });

  it("throws CliError on 401", async () => {
    await expect(
      uploadSite(
        "http://localhost:8080",
        "bad-token",
        zip,
        undefined,
        fakeFetch(401, { detail: "Unauthorized" })
      )
    ).rejects.toThrow("Not authenticated");
  });

  it("explains ownership conflicts", async () => {
    try {
      await uploadSite(
        "http://localhost:8080",
        "test-token",
        zip,
        "taken",
        fakeFetch(403, { detail: "Site 'taken' is owned by another user" })
      );
      expect.unreachable();
    } catch (error: any) {
      expect(error.message).toContain("owned by another user");
      expect(error.tip).toBe(
        "Choose a different name with --site <name>"
      );
    }
  });
});
