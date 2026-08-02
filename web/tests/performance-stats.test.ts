import { describe, expect, it } from "vitest";

import {
  averageWinLoss,
  maxDrawdown,
  observationWindow,
  pendingAsProvisionalTrades,
  profitFactor,
  sectorConcentration,
  wilsonInterval,
  type ClosedTrade,
} from "@/lib/performance";

function trade(overrides: Partial<ClosedTrade>): ClosedTrade {
  return {
    exit_date: "2026-02-01",
    net_pnl: 0,
    gross_pnl: 0,
    pnl_pct: 0,
    cohort_id: "SIC-D",
    ...overrides,
  };
}

describe("wilsonInterval", () => {
  it("returns null rather than an interval when there is no sample", () => {
    expect(wilsonInterval(0, 0)).toBeNull();
  });

  it("is centred near p_hat and widens as n shrinks", () => {
    const large = wilsonInterval(60, 100)!;
    const small = wilsonInterval(6, 10)!;
    expect(large.lower).toBeGreaterThan(0.4);
    expect(large.upper).toBeLessThan(0.75);
    expect(small.upper - small.lower).toBeGreaterThan(large.upper - large.lower);
  });

  it("never produces a bound outside [0, 1], even at the extremes", () => {
    const allWins = wilsonInterval(3, 3)!;
    const allLosses = wilsonInterval(0, 3)!;
    expect(allWins.upper).toBeLessThanOrEqual(1);
    expect(allWins.lower).toBeGreaterThanOrEqual(0);
    expect(allLosses.lower).toBeGreaterThanOrEqual(0);
    expect(allLosses.upper).toBeLessThanOrEqual(1);
  });
});

describe("profitFactor", () => {
  it("is undefined, not Infinity, when there are no losses yet", () => {
    const result = profitFactor([trade({ net_pnl: 100 }), trade({ net_pnl: 50 })]);
    expect(result.defined).toBe(false);
    if (!result.defined) {
      expect(result.reason).toContain("undefined, not infinite");
    }
  });

  it("computes gross win over absolute gross loss", () => {
    const result = profitFactor([
      trade({ net_pnl: 100 }),
      trade({ net_pnl: 50 }),
      trade({ net_pnl: -60 }),
    ]);
    expect(result.defined).toBe(true);
    if (result.defined) {
      expect(result.value).toBeCloseTo(150 / 60);
    }
  });

  it("is undefined with no trades at all", () => {
    const result = profitFactor([]);
    expect(result.defined).toBe(false);
  });
});

describe("averageWinLoss", () => {
  it("separates wins, losses and scratches, and averages each independently", () => {
    const result = averageWinLoss([
      trade({ net_pnl: 100 }),
      trade({ net_pnl: 200 }),
      trade({ net_pnl: -50 }),
      trade({ net_pnl: 0 }),
    ]);
    expect(result.avgWin).toBeCloseTo(150);
    expect(result.avgLoss).toBeCloseTo(-50);
    expect(result.winCount).toBe(2);
    expect(result.lossCount).toBe(1);
    expect(result.scratchCount).toBe(1);
  });

  it("returns null averages, not zero, when a side has no trades", () => {
    const result = averageWinLoss([trade({ net_pnl: 100 })]);
    expect(result.avgLoss).toBeNull();
  });
});

describe("maxDrawdown", () => {
  it("is measured against the FIXED starting NAV, not a moving baseline", () => {
    const trades = [
      trade({ exit_date: "2026-01-01", net_pnl: -5000 }),
      trade({ exit_date: "2026-01-02", net_pnl: -5000 }),
      trade({ exit_date: "2026-01-03", net_pnl: 20000 }), // recovers past the old peak
    ];
    const result = maxDrawdown(trades, 100_000);
    // Trough was 90,000 against a 100,000 starting NAV -> 10% drawdown, not
    // measured against the new 110,000 peak that comes after.
    expect(result.drawdownPct).toBeCloseTo(0.10);
  });

  it("replays trades in exit-date order regardless of array order", () => {
    const trades = [
      trade({ exit_date: "2026-01-03", net_pnl: 20000 }),
      trade({ exit_date: "2026-01-01", net_pnl: -5000 }),
      trade({ exit_date: "2026-01-02", net_pnl: -5000 }),
    ];
    const result = maxDrawdown(trades, 100_000);
    expect(result.drawdownPct).toBeCloseTo(0.10);
  });

  it("is zero when every trade is a win", () => {
    const result = maxDrawdown([trade({ net_pnl: 100 }), trade({ net_pnl: 200 })], 100_000);
    expect(result.drawdownPct).toBe(0);
  });
});

describe("sectorConcentration", () => {
  it("groups by SIC-derived cohort and sums to 100%", () => {
    const result = sectorConcentration([
      trade({ cohort_id: "SIC-D" }),
      trade({ cohort_id: "SIC-D" }),
      trade({ cohort_id: "SIC-G" }),
    ]);
    expect(result).toEqual([
      { cohort: "SIC-D", count: 2, pct: 2 / 3 },
      { cohort: "SIC-G", count: 1, pct: 1 / 3 },
    ]);
  });

  it("labels a missing cohort explicitly rather than dropping the trade", () => {
    const result = sectorConcentration([trade({ cohort_id: null })]);
    expect(result[0].cohort).toBe("SIC-UNKNOWN");
  });
});

describe("observationWindow", () => {
  it("is null with no trades", () => {
    expect(observationWindow([])).toBeNull();
  });

  it("spans the earliest to the latest exit date", () => {
    const window = observationWindow([
      trade({ exit_date: "2026-03-01" }),
      trade({ exit_date: "2026-01-15" }),
      trade({ exit_date: "2026-02-10" }),
    ]);
    expect(window).toEqual({ start: "2026-01-15", end: "2026-03-01" });
  });
});

describe("pendingAsProvisionalTrades", () => {
  it("values a pending position at zero equity, never at its notional", () => {
    const trades = pendingAsProvisionalTrades(
      [{ notional: 1000, dividends_received: 0, cohort_id: "SIC-G" }],
      "2026-08-02",
    );
    expect(trades).toHaveLength(1);
    expect(trades[0].net_pnl).toBe(-1000);
    expect(trades[0].pnl_pct).toBeCloseTo(-1);
    expect(trades[0].exit_date).toBe("2026-08-02");
  });

  it("nets any dividends already received against the zero valuation", () => {
    const trades = pendingAsProvisionalTrades(
      [{ notional: 1000, dividends_received: 15, cohort_id: "SIC-D" }],
      "2026-08-02",
    );
    expect(trades[0].net_pnl).toBe(-985);
  });
});
