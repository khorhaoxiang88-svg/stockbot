"use client";

import { useMemo, useState } from "react";

import { PriceChart, type ChartMarker, type ChartPoint } from "@/components/price-chart";

type Timeframe = "1M" | "6M" | "1Y" | "5Y" | "MAX";

const DAYS: Record<Exclude<Timeframe, "MAX">, number> = {
  "1M": 31,
  "6M": 183,
  "1Y": 366,
  "5Y": 1827,
};

export function InteractivePriceChart({
  points,
  markers = [],
  symbol,
}: {
  points: ChartPoint[];
  markers?: ChartMarker[];
  symbol: string;
}) {
  const [timeframe, setTimeframe] = useState<Timeframe>("1Y");
  const filtered = useMemo(() => {
    if (timeframe === "MAX" || points.length === 0) return points;
    const latest = new Date(`${points[points.length - 1].date}T00:00:00Z`);
    latest.setUTCDate(latest.getUTCDate() - DAYS[timeframe]);
    const cutoff = latest.toISOString().slice(0, 10);
    return points.filter((point) => point.date >= cutoff);
  }, [points, timeframe]);

  return (
    <section className="stock-chart-card" aria-label={`${symbol} stock chart`}>
      <div className="stock-chart-heading">
        <div>
          <span>Price history</span>
          <strong>{symbol}</strong>
        </div>
        <div className="timeframe-picker" aria-label="Chart timeframe">
          {(["1M", "6M", "1Y", "5Y", "MAX"] as Timeframe[]).map((option) => (
            <button
              type="button"
              key={option}
              onClick={() => setTimeframe(option)}
              aria-pressed={timeframe === option}
            >
              {option}
            </button>
          ))}
        </div>
      </div>
      <PriceChart
        points={filtered}
        markers={markers}
        title={`${timeframe} closing price`}
        subtitle={`${filtered.length} daily closes. Choose a timeframe above.`}
        height={300}
        accent="#4ade80"
      />
    </section>
  );
}
