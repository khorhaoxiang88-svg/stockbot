/**
 * O3's language audit, automated rather than manual: every .tsx/.ts source
 * file under app/ and components/ (never tests/ -- this file's own
 * assertions would otherwise trip on themselves) is scanned for the
 * prohibited phrases the brief names. "Research candidate" language, never
 * "recommendation"/"buy"/"top pick"/"signal to act".
 *
 * Audit result at the time this test was written: zero instances of any
 * prohibited phrase existed anywhere in app/ or components/ -- the site's
 * existing copy (candidates page, suppression labels, experiment banner)
 * already used "research candidate" / "candidate" consistently. Nothing was
 * changed as a result; this test exists to keep it that way.
 *
 * "buy" is matched at a word boundary, case-insensitively, so it catches
 * "Buy" but not "buyback" or "buying" -- compound forms the brief does not
 * name and that have legitimate uses (e.g. a future share-buyback risk
 * flag description).
 */

import fs from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

const WEB_ROOT = path.resolve(__dirname, "..");
const SCAN_DIRS = ["app", "components"];

const PROHIBITED: { name: string; pattern: RegExp }[] = [
  { name: "recommendation", pattern: /recommendation/i },
  { name: "top pick", pattern: /top pick/i },
  { name: "signal to act", pattern: /signal to act/i },
  { name: "buy", pattern: /\bbuy\b/i },
];

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      walk(full, out);
    } else if (/\.(tsx|ts)$/.test(entry.name)) {
      out.push(full);
    }
  }
  return out;
}

describe("language audit: no prohibited advisory language in user-facing source", () => {
  const files = SCAN_DIRS.flatMap((d) => walk(path.join(WEB_ROOT, d)));

  it("scans a non-trivial number of source files", () => {
    // A canary against the scan silently finding nothing to check (e.g. a
    // path typo) and the whole suite passing vacuously.
    expect(files.length).toBeGreaterThan(10);
  });

  for (const { name, pattern } of PROHIBITED) {
    it(`contains no instance of "${name}"`, () => {
      const hits: string[] = [];
      for (const file of files) {
        const text = fs.readFileSync(file, "utf-8");
        if (pattern.test(text)) {
          hits.push(path.relative(WEB_ROOT, file));
        }
      }
      expect(hits).toEqual([]);
    });
  }
});
