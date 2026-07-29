/**
 * Read-time split adjustment. Mirrors pipeline/prices/adjust.py — change one,
 * change the other, and the tests on both sides.
 *
 * Nothing stored is adjusted. The database holds raw traded prices, and the
 * adjusted series is derived here from the corporate actions ledger.
 *
 *   factor(d)   = product of ratios of every split with ex_date AFTER d
 *   adjusted(d) = raw(d) / factor(d)
 */

import type { CorporateAction, PriceBar } from "./db";

export type AdjustedBar = PriceBar & { factor: number };

export function splitEvents(actions: CorporateAction[]): { ex_date: string; ratio: number }[] {
  return actions
    .filter(
      (action) =>
        action.action_type === "split" &&
        action.ratio !== null &&
        action.ratio > 0 &&
        action.requires_manual_review === 0,
    )
    .map((action) => ({ ex_date: action.ex_date, ratio: action.ratio as number }))
    .sort((a, b) => a.ex_date.localeCompare(b.ex_date));
}

export function splitFactorForDate(
  splits: { ex_date: string; ratio: number }[],
  date: string,
): number {
  let factor = 1;
  for (const split of splits) {
    if (split.ex_date > date) factor *= split.ratio;
  }
  return factor;
}

export function adjustedSeries(bars: PriceBar[], actions: CorporateAction[]): AdjustedBar[] {
  const splits = splitEvents(actions);
  return bars.map((bar) => {
    const factor = splitFactorForDate(splits, bar.date);
    const scale = (value: number | null) =>
      value === null ? null : Number((value / factor).toFixed(6));
    return {
      ...bar,
      open: scale(bar.open),
      high: scale(bar.high),
      low: scale(bar.low),
      close: scale(bar.close),
      volume: bar.volume === null ? null : Math.round(bar.volume * factor),
      factor,
    };
  });
}

/** Biggest absolute one-day move, used to show a split left no artificial gap. */
export function largestSingleDayMove(bars: { date: string; close: number | null }[]) {
  let worst = 0;
  let worstDate: string | null = null;
  let previous: { date: string; close: number | null } | null = null;
  for (const bar of bars) {
    if (previous?.close && bar.close) {
      const change = Math.abs(bar.close / previous.close - 1);
      if (change > worst) {
        worst = change;
        worstDate = bar.date;
      }
    }
    previous = bar;
  }
  return { move: worst, date: worstDate };
}

export function returnAcrossDate(
  bars: { date: string; close: number | null }[],
  date: string,
): number | null {
  let previous: { date: string; close: number | null } | null = null;
  for (const bar of bars) {
    if (bar.date === date) {
      if (previous?.close && bar.close) return bar.close / previous.close - 1;
      return null;
    }
    previous = bar;
  }
  return null;
}
