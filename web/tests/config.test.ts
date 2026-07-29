import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { ConfigError, REQUIRED_KEYS, loadConfig, tryLoadConfig } from "@/lib/config";

const CONFIG_PATH = path.resolve(__dirname, "..", "..", "config.frozen.json");

describe("frozen config (web loader)", () => {
  it("loads every required key", () => {
    const config = loadConfig(CONFIG_PATH);
    for (const key of REQUIRED_KEYS) {
      expect(config, `missing ${key}`).toHaveProperty(key);
    }
  });

  it("raises a clear error when a key is missing", () => {
    const config = JSON.parse(fs.readFileSync(CONFIG_PATH, "utf-8"));
    delete config.position_notional;
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "stockbot-config-"));
    const broken = path.join(dir, "broken.json");
    fs.writeFileSync(broken, JSON.stringify(config));

    expect(() => loadConfig(broken)).toThrow(ConfigError);
    expect(() => loadConfig(broken)).toThrow(/missing required key\(s\): position_notional/);
    fs.rmSync(dir, { recursive: true, force: true });
  });

  it("reports a missing file rather than crashing", () => {
    const result = tryLoadConfig(path.join(os.tmpdir(), "definitely-not-here.json"));
    expect(result.ok).toBe(false);
  });

  it("reports composite_threshold as the outstanding placeholder", () => {
    const result = tryLoadConfig(CONFIG_PATH);
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.pendingPlaceholders).toEqual(["composite_threshold"]);
    }
  });
});
