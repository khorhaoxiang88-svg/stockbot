/**
 * The web adjustment must agree with pipeline/prices/adjust.py.
 */

import { describe, expect, it } from "vitest";

import {
  adjustedSeries,
  largestSingleDayMove,
  returnAcrossDate,
  splitFactorForDate,
} from "@/lib/adjust";
import type { CorporateAction, PriceBar } from "@/lib/db";

function bar(date: string, close: number, volume = 1_000_000): PriceBar {
  return {
    date,
    open: close,
    high: close,
    low: close,
    close,
    volume,
    revision: 0,
    price_data_version: 1,
  };
}

// Raw traded prices around a 10-for-1 split.
const RAW: PriceBar[] = [
  bar("2024-06-06", 1205.0, 41_000_000),
  bar("2024-06-07", 1208.0, 41_238_600),
  bar("2024-06-10", 121.8, 313_434_100),
  bar("2024-06-11", 123.0, 300_000_000),
];

const SPLIT: CorporateAction = {
  ex_date: "2024-06-10",
  action_type: "split",
  ratio: 10,
  cash_amount: null,
  provider: "test",
  requires_manual_review: 0,
};

describe("split adjustment", () => {
  it("computes the cumulative factor from splits after the date", () => {
    const splits = [
      { ex_date: "2024-06-10", ratio: 10 },
      { ex_date: "2025-01-15", ratio: 2 },
    ];
    expect(splitFactorForDate(splits, "2024-06-09")).toBe(20);
    expect(splitFactorForDate(splits, "2024-06-10")).toBe(2);
    expect(splitFactorForDate(splits, "2025-01-15")).toBe(1);
  });

  it("leaves no artificial gap across the split", () => {
    const rawReturn = returnAcrossDate(RAW, "2024-06-10");
    expect(rawReturn).not.toBeNull();
    expect(rawReturn as number).toBeLessThan(-0.85); // the real cliff survives

    const adjusted = adjustedSeries(RAW, [SPLIT]);
    const adjustedReturn = returnAcrossDate(adjusted, "2024-06-10");
    expect(Math.abs(adjustedReturn as number)).toBeLessThan(0.05);
  });

  it("scales pre-split bars down and leaves post-split bars alone", () => {
    const byDate = new Map(adjustedSeries(RAW, [SPLIT]).map((b) => [b.date, b]));
    expect(byDate.get("2024-06-07")?.close).toBeCloseTo(120.8, 6);
    expect(byDate.get("2024-06-07")?.factor).toBe(10);
    expect(byDate.get("2024-06-10")?.close).toBeCloseTo(121.8, 6);
    expect(byDate.get("2024-06-10")?.factor).toBe(1);
  });

  it("scales volume the opposite way to price", () => {
    const byDate = new Map(adjustedSeries(RAW, [SPLIT]).map((b) => [b.date, b]));
    expect(byDate.get("2024-06-07")?.volume).toBe(412_386_000);
  });

  it("ignores a split flagged for manual review", () => {
    const flagged = { ...SPLIT, requires_manual_review: 1 };
    const adjusted = adjustedSeries(RAW, [flagged]);
    expect(adjusted.every((b) => b.factor === 1)).toBe(true);
  });

  it("ignores dividends when adjusting for splits", () => {
    const dividend: CorporateAction = {
      ex_date: "2024-06-07",
      action_type: "dividend",
      ratio: null,
      cash_amount: 0.1,
      provider: "test",
      requires_manual_review: 0,
    };
    const adjusted = adjustedSeries(RAW, [SPLIT, dividend]);
    expect(adjusted.find((b) => b.date === "2024-06-07")?.close).toBeCloseTo(120.8, 6);
  });

  it.each(["other", "spinoff"])(
    "ignores a %s ratio, because a spin-off is not a split",
    (actionType) => {
      // Honeywell's Solstice spin-off arrives from Yahoo as ratio 1.061.
      const spinoff: CorporateAction = {
        ex_date: "2024-06-10",
        action_type: actionType,
        ratio: 1.061,
        cash_amount: null,
        provider: "test",
        requires_manual_review: 1,
      };
      const adjusted = adjustedSeries(RAW, [spinoff]);
      expect(adjusted.every((b) => b.factor === 1)).toBe(true);
      expect(adjusted.find((b) => b.date === "2024-06-07")?.close).toBeCloseTo(1208.0, 6);
    },
  );

  it("finds the largest one-day move", () => {
    const adjusted = adjustedSeries(RAW, [SPLIT]);
    const { move } = largestSingleDayMove(adjusted);
    expect(move).toBeLessThan(0.05);

    const rawWorst = largestSingleDayMove(RAW);
    expect(rawWorst.move).toBeGreaterThan(0.85);
    expect(rawWorst.date).toBe("2024-06-10");
  });
});
