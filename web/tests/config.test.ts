import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { describe, expect, it } from "vitest";

import {
  ConfigError,
  REQUIRED_KEYS,
  canonicalValue,
  governedDigest,
  loadConfig,
  tryLoadConfig,
} from "@/lib/config";

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

  it("reports no pending placeholders now composite_threshold is frozen", () => {
    const result = tryLoadConfig(CONFIG_PATH);
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.pendingPlaceholders).toEqual([]);
    }
  });


  it("computes the same governed digest the Python loader recorded", () => {
    const config = JSON.parse(fs.readFileSync(CONFIG_PATH, "utf-8"));
    const recorded =
      config._version_digests.strategy_version[String(config.strategy_version)];
    // If this fails, the two loaders disagree about how to canonicalise a
    // value and the guard would fire on every page load.
    expect(governedDigest(config, "strategy_version")).toBe(recorded);
  });

  it("refuses a governed value that changed without a version bump", () => {
    const config = JSON.parse(fs.readFileSync(CONFIG_PATH, "utf-8"));
    config.high_leverage_debt_ebitda = 5.0;
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "stockbot-config-"));
    const drifted = path.join(dir, "drifted.json");
    fs.writeFileSync(drifted, JSON.stringify(config));

    // Matched against whatever the current version is, not a literal, so this
    // does not need editing every time the version legitimately moves.
    expect(() => loadConfig(drifted)).toThrow(
      new RegExp(
        `governed by strategy_version have changed but strategy_version is still ${config.strategy_version}`,
      ),
    );
    fs.rmSync(dir, { recursive: true, force: true });
  });

  it("refuses a version bump with no digest recorded for it", () => {
    const config = JSON.parse(fs.readFileSync(CONFIG_PATH, "utf-8"));
    const unrecorded =
      Math.max(
        ...Object.keys(config._version_digests.strategy_version).map(Number),
      ) + 1;
    config.strategy_version = unrecorded;
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "stockbot-config-"));
    const bumped = path.join(dir, "bumped.json");
    fs.writeFileSync(bumped, JSON.stringify(config));

    expect(() => loadConfig(bumped)).toThrow(
      new RegExp(`no digest recorded for strategy_version=${unrecorded}`),
    );
    fs.rmSync(dir, { recursive: true, force: true });
  });

  it("accepts a bump once its digest is recorded", () => {
    const config = JSON.parse(fs.readFileSync(CONFIG_PATH, "utf-8"));
    const next = String(
      Math.max(
        ...Object.keys(config._version_digests.strategy_version).map(Number),
      ) + 1,
    );
    config.high_leverage_debt_ebitda = 5.0;
    config.strategy_version = Number(next);
    config._version_digests.strategy_version[next] = governedDigest(
      config,
      "strategy_version",
    );
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "stockbot-config-"));
    const bumped = path.join(dir, "ok.json");
    fs.writeFileSync(bumped, JSON.stringify(config));

    expect(() => loadConfig(bumped)).not.toThrow();
    fs.rmSync(dir, { recursive: true, force: true });
  });


  it("canonicalises nested objects the same way the Python loader does", () => {
    // freshness_sla is a governed OBJECT. String(obj) is "[object Object]" in
    // JavaScript and "{'a': 1}" in Python, so without walking it the two
    // loaders would hash differently and the guard would fire on every load.
    expect(canonicalValue({ b: 2, a: 1 })).toBe("{a:1,b:2}");
    expect(canonicalValue([1, 2.0, "x"])).toBe("[1,2,x]");
    expect(canonicalValue({ outer: { inner: 4.0 } })).toBe("{outer:{inner:4}}");
    expect(canonicalValue({ a: 1, b: 2 })).toBe(canonicalValue({ b: 2, a: 1 }));
  });
});
