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

export type FrozenConfig = Record<string, unknown>;

export class ConfigError extends Error {}

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
