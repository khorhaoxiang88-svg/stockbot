import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  classify,
  fetchStatus,
  isValidStatus,
  DISPLAY_COLOR_VAR,
  DISPLAY_LABEL,
  STATUS_URL,
} from "../app/status.mjs";

async function fixture(name) {
  const url = new URL(`./fixtures/status-${name}.json`, import.meta.url);
  return JSON.parse(await readFile(url, "utf-8"));
}

// A fixed reference "now" close to the fixtures' own timestamps -- never
// Date.now(), so these tests never flake with real wall-clock time.
const NOW = Date.parse("2026-08-09T12:00:00Z");
const FAR_FUTURE = Date.parse("2026-08-11T00:00:00Z"); // for the stale fixture

test("running fixture, checked promptly, classifies as idle (no distinct running color)", async () => {
  const status = await fixture("running");
  assert.equal(classify(status, NOW), "idle");
});

test("succeeded fixture classifies as healthy", async () => {
  const status = await fixture("succeeded");
  assert.equal(classify(status, NOW), "healthy");
});

test("failed fixture classifies as failed", async () => {
  const status = await fixture("failed");
  assert.equal(classify(status, NOW), "failed");
});

test("idle fixture classifies as idle", async () => {
  const status = await fixture("idle");
  assert.equal(classify(status, NOW), "idle");
});

test("stale fixture (old last_activity_at) overrides a 'succeeded' state to stale", async () => {
  const status = await fixture("stale");
  assert.equal(status.scanner_state, "succeeded", "fixture must exercise the override path");
  assert.equal(classify(status, FAR_FUTURE), "stale");
});

test("a running state that hasn't heartbeated recently enough is stale, not running", async () => {
  const status = await fixture("running");
  const twoHoursAndAMinuteLater = Date.parse(status.last_activity_at) + 2 * 60 * 60 * 1000 + 60_000;
  assert.equal(classify(status, twoHoursAndAMinuteLater), "stale");
});

test("a partial state classifies as partial, distinct from healthy and failed", () => {
  const status = { scanner_state: "partial", last_activity_at: "2026-08-09T11:00:00Z" };
  assert.equal(classify(status, NOW), "partial");
});

test("no status at all (null) classifies as unavailable", () => {
  assert.equal(classify(null, NOW), "unavailable");
});

test("a malformed last_activity_at classifies as unavailable rather than crashing", () => {
  assert.equal(classify({ scanner_state: "succeeded", last_activity_at: "not-a-date" }, NOW), "unavailable");
});

test("every classify() output has both a color and a label defined", async () => {
  for (const name of ["running", "succeeded", "failed", "idle", "stale"]) {
    const status = await fixture(name);
    const now = name === "stale" ? FAR_FUTURE : NOW;
    const state = classify(status, now);
    assert.ok(DISPLAY_COLOR_VAR[state], `missing color for ${state}`);
    assert.ok(DISPLAY_LABEL[state], `missing label for ${state}`);
  }
  assert.ok(DISPLAY_COLOR_VAR.unavailable);
  assert.ok(DISPLAY_LABEL.unavailable);
});

test("isValidStatus rejects a payload missing scanner_state", () => {
  assert.equal(isValidStatus({ generated_at: "x", last_activity_at: "x" }), false);
});

test("isValidStatus rejects null and non-objects", () => {
  assert.equal(isValidStatus(null), false);
  assert.equal(isValidStatus("healthy"), false);
  assert.equal(isValidStatus(42), false);
});

test("isValidStatus accepts a real fixture shape", async () => {
  const status = await fixture("succeeded");
  assert.equal(isValidStatus(status), true);
});

test("fetchStatus returns null on a non-200 response, never throws", async (t) => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response("not found", { status: 404 });
  t.after(() => { globalThis.fetch = originalFetch; });

  const result = await fetchStatus(STATUS_URL);
  assert.equal(result, null);
});

test("fetchStatus returns null on a network error, never throws", async (t) => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => { throw new TypeError("network error"); };
  t.after(() => { globalThis.fetch = originalFetch; });

  const result = await fetchStatus(STATUS_URL);
  assert.equal(result, null);
});

test("fetchStatus returns null on a malformed JSON body, never throws", async (t) => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response("{ not json", { status: 200 });
  t.after(() => { globalThis.fetch = originalFetch; });

  const result = await fetchStatus(STATUS_URL);
  assert.equal(result, null);
});

test("fetchStatus returns null on a well-formed but wrong-shaped JSON body", async (t) => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response(JSON.stringify({ hello: "world" }), { status: 200 });
  t.after(() => { globalThis.fetch = originalFetch; });

  const result = await fetchStatus(STATUS_URL);
  assert.equal(result, null);
});

test("fetchStatus returns the parsed payload on success", async (t) => {
  const fixtureData = await fixture("succeeded");
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response(JSON.stringify(fixtureData), { status: 200 });
  t.after(() => { globalThis.fetch = originalFetch; });

  const result = await fetchStatus(STATUS_URL);
  assert.deepEqual(result, fixtureData);
});

test("fetchStatus never exposes anything path- or price-shaped even from a hostile payload", async (t) => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () =>
    new Response(
      JSON.stringify({
        scanner_state: "succeeded",
        generated_at: "2026-08-09T00:00:00Z",
        last_activity_at: "2026-08-09T00:00:00Z",
        api_key: "sk-should-never-be-here",
        db_path: "C:\\Users\\USER\\stockbot\\data\\stockbot.db",
      }),
      { status: 200 },
    );
  t.after(() => { globalThis.fetch = originalFetch; });

  const result = await fetchStatus(STATUS_URL);
  assert.equal(result.scanner_state, "succeeded");
  assert.equal(result.api_key, undefined, "sanitize() must strip unlisted fields");
  assert.equal(result.db_path, undefined, "sanitize() must strip unlisted fields");
  assert.deepEqual(
    new Set(Object.keys(result)),
    new Set(["scanner_state", "generated_at", "last_activity_at"]),
  );
});
