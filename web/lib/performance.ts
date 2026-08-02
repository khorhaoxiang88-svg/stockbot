/**
 * Pure statistics for the /performance page. No I/O here — every function
 * takes already-loaded rows and returns numbers, so the arithmetic can be
 * tested without a database.
 *
 * "Engineering validation dataset — not strategy performance." That banner is
 * not decoration: these numbers describe a fixture of 50 securities executed
 * once under a frozen protocol, not a strategy's live track record. Nothing in
 * this module estimates, smooths or infers past what the closed-trade ledger
 * actually contains.
 */

export type ClosedTrade = {
  exit_date: string;
  net_pnl: number;
  gross_pnl: number;
  pnl_pct: number;
  cohort_id: string | null;
};

/**
 * 95% Wilson score interval on a win rate. Chosen over the normal
 * (Wald) approximation because Wald is badly behaved at small n and at win
 * rates near 0 or 1 — exactly the regime a 50-security fixture sits in. Wilson
 * stays a valid interval (never below 0 or above 1) at any sample size.
 */
export function wilsonInterval(
  wins: number,
  n: number,
  z = 1.959963985,
): { lower: number; upper: number; center: number } | null {
  if (n <= 0) return null;
  const p = wins / n;
  const z2 = z * z;
  const denominator = 1 + z2 / n;
  const centre = p + z2 / (2 * n);
  const margin = z * Math.sqrt((p * (1 - p)) / n + z2 / (4 * n * n));
  return {
    lower: Math.max(0, (centre - margin) / denominator),
    upper: Math.min(1, (centre + margin) / denominator),
    center: centre / denominator,
  };
}

export type ProfitFactor =
  | { defined: true; value: number }
  | { defined: false; reason: string };

export function profitFactor(trades: ClosedTrade[]): ProfitFactor {
  const grossWin = trades.filter((t) => t.net_pnl > 0).reduce((s, t) => s + t.net_pnl, 0);
  const grossLoss = trades.filter((t) => t.net_pnl < 0).reduce((s, t) => s + t.net_pnl, 0);
  if (grossLoss === 0 && grossWin === 0) {
    return { defined: false, reason: "no closed trades" };
  }
  if (grossLoss === 0) {
    return { defined: false, reason: "no losing trades yet; profit factor is undefined, not infinite" };
  }
  return { defined: true, value: grossWin / Math.abs(grossLoss) };
}

export function averageWinLoss(trades: ClosedTrade[]): {
  avgWin: number | null;
  avgLoss: number | null;
  winCount: number;
  lossCount: number;
  scratchCount: number;
} {
  const wins = trades.filter((t) => t.net_pnl > 0);
  const losses = trades.filter((t) => t.net_pnl < 0);
  const scratches = trades.filter((t) => t.net_pnl === 0);
  return {
    avgWin: wins.length ? wins.reduce((s, t) => s + t.net_pnl, 0) / wins.length : null,
    avgLoss: losses.length ? losses.reduce((s, t) => s + t.net_pnl, 0) / losses.length : null,
    winCount: wins.length,
    lossCount: losses.length,
    scratchCount: scratches.length,
  };
}

/**
 * Maximum drawdown of the book's NAV curve, replayed from the closed-trade
 * ledger against the FIXED starting NAV.
 *
 * There is no stored NAV time series — books.current_nav is a single running
 * total, not a curve — so the curve is reconstructed here by replaying closed
 * trades in exit-date order and accumulating net_pnl onto the fixed starting
 * NAV. This is reproducible from the ledger alone and needs no extra storage:
 * anyone can re-derive the same curve from the same trades.
 */
export function maxDrawdown(
  trades: ClosedTrade[],
  startingNav: number,
): { drawdownPct: number; troughNav: number; peakNav: number } {
  const ordered = [...trades].sort((a, b) => a.exit_date.localeCompare(b.exit_date));
  let nav = startingNav;
  let peak = startingNav;
  let worstDrawdown = 0;
  let troughNav = startingNav;
  let peakNav = startingNav;
  for (const trade of ordered) {
    nav += trade.net_pnl;
    if (nav > peak) peak = nav;
    const drawdown = (peak - nav) / startingNav;
    if (drawdown > worstDrawdown) {
      worstDrawdown = drawdown;
      troughNav = nav;
      peakNav = peak;
    }
  }
  return { drawdownPct: worstDrawdown, troughNav, peakNav };
}

export function sectorConcentration(
  trades: ClosedTrade[],
): { cohort: string; count: number; pct: number }[] {
  if (trades.length === 0) return [];
  const counts = new Map<string, number>();
  for (const trade of trades) {
    const key = trade.cohort_id ?? "SIC-UNKNOWN";
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return [...counts.entries()]
    .map(([cohort, count]) => ({ cohort, count, pct: count / trades.length }))
    .sort((a, b) => b.count - a.count);
}

export function observationWindow(trades: ClosedTrade[]): { start: string; end: string } | null {
  if (trades.length === 0) return null;
  const dates = trades.map((t) => t.exit_date).sort();
  return { start: dates[0], end: dates[dates.length - 1] };
}

export type PendingPosition = {
  notional: number;
  dividends_received: number;
  cohort_id: string | null;
};

/**
 * Pending-resolution positions, valued at the PROVISIONAL policy value for
 * statistics purposes — zero, the same value the recorded resolution policy
 * would assign if 180 days elapsed today with no verified recovery.
 *
 * This is deliberately NOT how paper_positions itself is ever written: a
 * pending position is never actually closed or given an exit_price by the
 * pipeline until either a verified recovery arrives or 180 days genuinely
 * pass (pipeline/execution/delisting.py). This function exists only so the
 * "including pending" statistic can be computed at all — R1-PROTOCOL-1.1
 * requires both an including and an excluding view be reported side by side,
 * and the including view has to value the still-open position at SOMETHING.
 * Zero is the only value consistent with the recorded policy rather than an
 * invented haircut.
 */
export function pendingAsProvisionalTrades(
  pending: PendingPosition[],
  asOfDate: string,
): ClosedTrade[] {
  return pending.map((p) => {
    const netPnl = p.dividends_received - p.notional; // provisional: equity -> 0
    return {
      exit_date: asOfDate,
      net_pnl: netPnl,
      gross_pnl: -p.notional,
      pnl_pct: netPnl / p.notional,
      cohort_id: p.cohort_id,
    };
  });
}
