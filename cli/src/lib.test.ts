import { describe, it, expect } from "vitest";
import { mkdtempSync, writeFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import JSZip from "jszip";
import { formatSize, createZipBuffer } from "./lib.js";

describe("formatSize", () => {
  it.each([
    [500, "500 B"],
    [2048, "2.0 KB"],
    [3 * 1024 * 1024, "3.0 MB"],
  ])("formats %i bytes as %s", (bytes, formatted) => {
    expect(formatSize(bytes)).toBe(formatted);
  });
});

function makeTmpDir(): string {
  return mkdtempSync(join(tmpdir(), "buzz-zip-test-"));
}

async function zipEntries(buf: Buffer): Promise<string[]> {
  const zip = await JSZip.loadAsync(buf);
  return Object.keys(zip.files).filter((f) => !f.endsWith("/")).sort();
}

describe("createZipBuffer", () => {
  it("includes site files and excludes local or sensitive files", async () => {
    const dir = makeTmpDir();
    writeFileSync(join(dir, "index.html"), "<h1>hi</h1>");
    writeFileSync(join(dir, "style.css"), "body{}");
    mkdirSync(join(dir, ".git"));
    writeFileSync(join(dir, ".git", "config"), "gitconfig");
    writeFileSync(join(dir, ".DS_Store"), "");
    writeFileSync(join(dir, ".env"), "SECRET=123");
    writeFileSync(join(dir, ".env.local"), "SECRET=456");
    writeFileSync(join(dir, ".env.production"), "SECRET=789");
    mkdirSync(join(dir, ".vscode"));
    writeFileSync(join(dir, ".vscode", "settings.json"), "{}");
    mkdirSync(join(dir, ".idea"));
    writeFileSync(join(dir, ".idea", "workspace.xml"), "<xml/>");
    mkdirSync(join(dir, "node_modules"));
    mkdirSync(join(dir, "node_modules", "some-pkg"));
    writeFileSync(join(dir, "node_modules", "some-pkg", "index.js"), "module.exports = {}");
    mkdirSync(join(dir, ".well-known"));
    writeFileSync(join(dir, ".well-known", "acme-challenge"), "token123");
    mkdirSync(join(dir, "assets"));
    writeFileSync(join(dir, "assets", "logo.png"), "img");
    writeFileSync(join(dir, "assets", ".DS_Store"), "");

    const buf = await createZipBuffer(dir);
    const entries = await zipEntries(buf);

    expect(entries).toEqual([
      ".well-known/acme-challenge",
      "assets/logo.png",
      "index.html",
      "style.css",
    ]);
  });
});
