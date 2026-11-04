import type { Target } from "../target.js";

export function testTarget(overrides: Partial<Target> = {}): Target {
  return {
    url: "https://buzz.example.com",
    source: "flag",
    credential: "flag",
    token: "session-token",
    hasSavedSession: false,
    hasEnvToken: false,
    ...overrides,
  };
}
