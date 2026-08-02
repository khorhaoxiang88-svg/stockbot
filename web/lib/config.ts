import { createHash } from "node:crypto";
import fs from "node:fs";

import { CONFIG_PATH } from "./paths";

/**
 * Loader for config.frozen.json.
 *
 * Mirrors pipeline/config_loader.py. The JSON file is the single source of
 * truth for values; this list is the single source of truth for "what must
 * exist" on the web side. If you add a key, add it in both places.
 */

export const REQUIRED_KEYS = [
  "strategy_version",
  "selection_rule_version",
  "protocol_version",
  "resolution_policy_version",
  "accrual_policy_version",
  "mapping_version",
  "composite_threshold",
  "max_candidates_per_selection",
  "max_per_cohort",
  "book_starting_nav",
  "position_notional",
  "max_open_positions_per_horizon",
  "horizons",
  "atr_window",
  "stop_atr_multiple",
  "target_atr_multiple",
  "gap_cancel_atr",
  "slippage_bps_high_liquidity",
  "slippage_bps_mid_liquidity",
  "cohort_blend_target",
  "cohort_blend_floor",
  "dilution_disqualify",
  "current_ratio_cap",
  "interest_coverage_cap",
  "high_leverage_debt_ebitda",
  "exit_cooldown_days",
  "gap_cancel_cooldown_days",
  "freshness_sla",
] as const;

/** Keys allowed to be null until the phase named in config._placeholders. */
export const PLACEHOLDER_KEYS = ["composite_threshold"] as const;

/** Mirrors config_loader.VERSION_KEYS. */
export const VERSION_KEYS = [
  "strategy_version",
  "selection_rule_version",
  "protocol_version",
  "resolution_policy_version",
  "accrual_policy_version",
  "mapping_version",
] as const;

export type FrozenConfig = Record<string, unknown>;

export class ConfigError extends Error {}

/**
 * One textual form for a value, identical to config_loader.canonical_value.
 *
 * Hashing JSON.stringify output would not survive the language boundary:
 * Python renders 4.0 as "4.0" and JavaScript renders it as "4", so the same
 * config file would hash to two different digests and the guard would fire on
 * every page load. Numbers are normalised to the shortest round-trip form both
 * languages already produce, and tests on both sides assert the same digest.
 */
export function canonicalValue(value: unknown): string {
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") return String(value);
  if (value === null || value === undefined) return "null";
  // Objects and arrays are walked, never stringified. Python renders a dict as
  // "{'a': 1}" and JavaScript renders it as "[object Object]", so a governed
  // value that is an object -- freshness_sla is one -- would hash differently on
  // each side and the guard would fire on every page load. Keys are sorted so
  // the digest does not depend on insertion order.
  if (Array.isArray(value)) {
    return `[${value.map(canonicalValue).join(",")}]`;
  }
  if (typeof value === "object") {
    const record = value as Record<string, unknown>;
    const inner = Object.keys(record)
      .sort()
      .map((key) => `${key}:${canonicalValue(record[key])}`)
      .join(",");
    return `{${inner}}`;
  }
  return String(value);
}

export function governedDigest(config: FrozenConfig, versionKey: string): string {
  const governedBy = (config._governed_by ?? {}) as Record<string, unknown>;
  const keys = (governedBy[versionKey] as string[] | undefined) ?? [];
  const payload = [...keys]
    .sort()
    .map((key) => `${key}=${canonicalValue(config[key])}`)
    .join("\n");
  return createHash("sha256").update(payload, "utf-8").digest("hex");
}

/**
 * Every governed block must match the digest recorded for its version.
 *
 * This is what turns "changing a value means bumping the matching *_version
 * key" from a note in the README into an enforced rule. A governed value
 * cannot change and still load: either the version is bumped and a new digest
 * recorded, or validation fails and names the block that drifted.
 */
export function checkGovernedVersions(config: FrozenConfig): string[] {
  const problems: string[] = [];
  const governedBy = (config._governed_by ?? {}) as Record<string, unknown>;
  const digests = (config._version_digests ?? {}) as Record<string, unknown>;

  for (const versionKey of Object.keys(governedBy)) {
    if (versionKey.startsWith("_")) continue;
    if (!(VERSION_KEYS as readonly string[]).includes(versionKey)) {
      problems.push(`_governed_by names ${versionKey}, which is not a version key`);
      continue;
    }
    const keys = (governedBy[versionKey] as string[] | undefined) ?? [];
    const unknown = keys.filter((key) => !(REQUIRED_KEYS as readonly string[]).includes(key));
    if (unknown.length > 0) {
      problems.push(
        `_governed_by.${versionKey} names key(s) that are not required: ${unknown
          .sort()
          .join(", ")}`,
      );
    }
    const governedPlaceholders = keys.filter((key) =>
      (PLACEHOLDER_KEYS as readonly string[]).includes(key),
    );
    if (governedPlaceholders.length > 0) {
      problems.push(
        `_governed_by.${versionKey} must not govern declared placeholder(s): ${governedPlaceholders
          .sort()
          .join(", ")}`,
      );
    }

    const current = String(config[versionKey]);
    const recorded = (digests[versionKey] as Record<string, string> | undefined)?.[current];
    const actual = governedDigest(config, versionKey);
    if (recorded === undefined) {
      problems.push(
        `no digest recorded for ${versionKey}=${current}. Add ` +
          `_version_digests.${versionKey}["${current}"] = "${actual}"`,
      );
    } else if (recorded !== actual) {
      problems.push(
        `values governed by ${versionKey} have changed but ${versionKey} is still ` +
          `${current}. Bump it and record the new digest ${actual} (recorded: ${recorded})`,
      );
    }
  }
  return problems;
}

export function validateConfig(config: FrozenConfig, source = "config"): void {
  const problems: string[] = [];

  const missing = REQUIRED_KEYS.filter((key) => !(key in config));
  if (missing.length > 0) {
    problems.push(`missing required key(s): ${missing.join(", ")}`);
  }

  const placeholders = new Set<string>(PLACEHOLDER_KEYS);
  const wrongNulls = REQUIRED_KEYS.filter(
    (key) => key in config && config[key] === null && !placeholders.has(key),
  );
  if (wrongNulls.length > 0) {
    problems.push(`key(s) set to null that may not be null: ${wrongNulls.join(", ")}`);
  }

  problems.push(...checkGovernedVersions(config));

  if (problems.length > 0) {
    throw new ConfigError(`${source} failed validation:\n  - ${problems.join("\n  - ")}`);
  }
}

export function loadConfig(path: string = CONFIG_PATH): FrozenConfig {
  if (!fs.existsSync(path)) {
    throw new ConfigError(`Config file not found: ${path}`);
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(fs.readFileSync(path, "utf-8"));
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    throw new ConfigError(`Config file ${path} is not valid JSON: ${message}`);
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    throw new ConfigError(`Config file ${path} must contain a JSON object at the top level`);
  }
  const config = parsed as FrozenConfig;
  validateConfig(config, path);
  return config;
}

export type ConfigLoadResult =
  | { ok: true; config: FrozenConfig; keyCount: number; pendingPlaceholders: string[] }
  | { ok: false; message: string };

/** Never throws. Used by the health page so a bad config is shown, not fatal. */
export function tryLoadConfig(path: string = CONFIG_PATH): ConfigLoadResult {
  try {
    const config = loadConfig(path);
    return {
      ok: true,
      config,
      keyCount: REQUIRED_KEYS.length,
      pendingPlaceholders: PLACEHOLDER_KEYS.filter((key) => config[key] === null),
    };
  } catch (error) {
    return { ok: false, message: error instanceof Error ? error.message : String(error) };
  }
}
