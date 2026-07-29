/**
 * The web rankability rules must stay identical to pipeline/universe/classify.py.
 */

import { describe, expect, it } from "vitest";

import { RANKABLE_SECURITY_TYPES, isRankable, rankExclusionReason } from "@/lib/rank";

const NON_COMMON = [
  "preferred_share",
  "warrant",
  "right",
  "unit",
  "etf",
  "etn",
  "closed_end_fund",
  "adr",
  "trust_unit",
  "test_issue",
];

describe("rankability", () => {
  it("ranks common stock at high and medium confidence", () => {
    expect(isRankable("common_stock", "high")).toBe(true);
    expect(isRankable("common_stock", "medium")).toBe(true);
  });

  it("never ranks a non-common-stock type, at any confidence", () => {
    for (const type of NON_COMMON) {
      for (const confidence of ["high", "medium", "low"]) {
        expect(isRankable(type, confidence), `${type}/${confidence}`).toBe(false);
      }
    }
  });

  it("excludes preferred shares independently of the unknown check", () => {
    const reason = rankExclusionReason("preferred_share", "high");
    expect(reason).not.toBeNull();
    expect(reason).toContain("not common stock");
    expect(reason).not.toContain("unknown");
  });

  it("reports the real reason for each exclusion", () => {
    expect(rankExclusionReason("unknown", "low")).toContain("unknown");
    expect(rankExclusionReason("warrant", "high")).toContain("not common stock");
    expect(rankExclusionReason("common_stock", "low")).toContain("confidence");
    expect(rankExclusionReason("common_stock", "high")).toBeNull();
  });

  it("has only common_stock in the rankable set", () => {
    expect([...RANKABLE_SECURITY_TYPES]).toEqual(["common_stock"]);
  });
});
