// Public-safe status: fetch, staleness classification, color/label mapping.
//
// Plain ESM (not .ts) on purpose -- both the "use client" panel component
// and tests/status.test.mjs import this file directly, with no build step
// required for the pure logic. See migrations/025 and
// pipeline/scheduler/publish_status.py for what actually writes the JSON
// this fetches; SAFE_FIELDS below is the only shape this file ever trusts.

export const STATUS_URL =
  "https://raw.githubusercontent.com/khorhaoxiang88-svg/stockbot/bot-status/status.json";

// Known, real limitation of this free approach (no paid backend, per
// project decision): raw.githubusercontent.com's own CDN can lag a fresh
// push by several minutes even with a cache-busting query param and
// cache:"no-store" -- confirmed live during testing (a push landed on
// GitHub and via curl immediately, but the CDN kept serving the prior
// version for a few minutes after). The 60s poll interval below is this
// site's own cadence; actual data freshness is bounded by GitHub's cache,
// not just this site's polling. IDLE_STALE_MS is generous enough (26h)
// that this lag never causes a false "stale" reading.

// 2h: generous against the pipeline's own documented longest orchestration
// runs (README: up to ~4h20m for a large batch) while a stage is actively
// "running" but hasn't heartbeated recently enough to still trust it.
export const RUNNING_STALE_MS = 2 * 60 * 60 * 1000;

// 26h: the daily job's own cadence (24h) plus a buffer -- if NOTHING has
// updated in over a day, the last known state is no longer trustworthy
// regardless of what it says, even if it was "succeeded".
export const IDLE_STALE_MS = 26 * 60 * 60 * 1000;

const SAFE_FIELDS = [
  "schema_version", "generated_at", "scanner_state", "current_stage",
  "progress", "last_activity_at", "last_success_at", "latest_score_date",
  "ranked_count", "latest_selection_status", "selected_count",
  "next_scheduled_run",
];

/** Rejects anything not shaped like the real export -- a malformed or
 * unexpected payload (wrong branch, a stray file, a fork of this repo)
 * must degrade to "unavailable", never render as if it were real. */
export function isValidStatus(data) {
  if (typeof data !== "object" || data === null) return false;
  if (typeof data.scanner_state !== "string") return false;
  if (typeof data.generated_at !== "string" || typeof data.last_activity_at !== "string") {
    return false;
  }
  return true;
}

/** Copies ONLY the named safe fields onto a fresh object -- enforced in
 * code, not just documented, so a hostile or accidentally-overshared
 * status.json (an extra field some future pipeline change adds without
 * updating this list) can never reach the rendered page even if it passes
 * isValidStatus's shape check. */
function sanitize(data) {
  const clean = {};
  for (const key of SAFE_FIELDS) {
    if (key in data) clean[key] = data[key];
  }
  return clean;
}

export async function fetchStatus(url = STATUS_URL, timeoutMs = 8000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const bust = url.includes("?") ? "&" : "?";
    const res = await fetch(`${url}${bust}t=${Date.now()}`, {
      cache: "no-store",
      signal: controller.signal,
    });
    if (!res.ok) return null;
    const data = await res.json();
    return isValidStatus(data) ? sanitize(data) : null;
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

/**
 * @param {object|null} status
 * @param {number} nowMs
 * @returns {"healthy"|"partial"|"idle"|"failed"|"stale"|"unavailable"}
 */
export function classify(status, nowMs) {
  if (!status) return "unavailable";
  const lastActivity = Date.parse(status.last_activity_at);
  if (Number.isNaN(lastActivity)) return "unavailable";
  const age = nowMs - lastActivity;

  // A "running" state that hasn't heartbeated recently enough looks stuck,
  // not actively healthy -- surfaced as stale rather than a false "running".
  if (status.scanner_state === "running" && age > RUNNING_STALE_MS) return "stale";
  if (age > IDLE_STALE_MS) return "stale";

  switch (status.scanner_state) {
    case "succeeded":
      return "healthy";
    case "partial":
      return "partial";
    case "failed":
      return "failed";
    case "idle":
      return "idle";
    case "running":
      // No distinct color is named for "running" in the spec (green/amber/
      // blue/red map to healthy/partial-or-stale/idle/failed) -- treated as
      // the same "operating normally" blue as idle, not a 5th color.
      return "idle";
    default:
      return "unavailable";
  }
}

export const DISPLAY_COLOR_VAR = {
  healthy: "--green",
  partial: "--amber",
  stale: "--amber",
  idle: "--blue",
  failed: "--red",
  unavailable: "--muted",
};

export const DISPLAY_LABEL = {
  healthy: "Healthy",
  partial: "Partial",
  stale: "Stale",
  idle: "Idle",
  failed: "Failed",
  unavailable: "Status unavailable",
};
