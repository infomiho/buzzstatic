import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { beforeEach } from "vitest";

process.env.HOME = mkdtempSync(join(tmpdir(), "buzz-home-"));

beforeEach(() => {
  delete process.env.BUZZ_SERVER;
  delete process.env.BUZZ_TOKEN;
  delete process.env.CI;
});
