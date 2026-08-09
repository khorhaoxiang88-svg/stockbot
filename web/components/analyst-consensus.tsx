import type { AnalystSnapshot } from "@/lib/db";
import { formatEastern } from "@/lib/time";

/**
 * Third-party Wall Street opinion (Yahoo Finance via yfinance), never this
 * system's own view. Deliberately never echoes Yahoo's own rating word --
 * the label here is derived only from the numeric 1.0-5.0 consensus mean,
 * same neutral vocabulary this site's language audit already holds the
 * bot's own copy to (see web/tests/language-audit.test.ts).
 */

type Props = {
  snapshot: AnalystSnapshot | null;
  currentPrice?: number | null;
};

const TIERS = [
  { max: 1.5, label: "Strong bullish" },
  { max: 2.5, label: "Bullish" },
  { max: 3.5, label: "Neutral" },
  { max: 4.5, label: "Bearish" },
  { max: Infinity, label: "Strong bearish" },
];

function consensusLabel(mean: number | null): string | null {
  if (mean === null) return null;
  return TIERS.find((tier) => mean <= tier.max)?.label ?? null;
}

function money(value: number | null, currency: string | null) {
  if (value === null) return "—";
  return currency === "USD" || !currency
    ? `$${value.toFixed(2)}`
    : `${value.toFixed(2)} ${currency}`;
}

export function AnalystConsensus({ snapshot, currentPrice }: Props) {
  if (!snapshot) {
    return (
      <section className="analyst-consensus friendly-empty mb-8">
        <strong>No analyst data fetched for this stock yet.</strong>
        <p>Covers the bot&rsquo;s currently-included universe only.</p>
      </section>
    );
  }

  if (snapshot.fetch_error) {
    return (
      <section className="analyst-consensus friendly-empty mb-8">
        <strong>Analyst data unavailable.</strong>
        <p>Last attempt {formatEastern(snapshot.fetched_at)}: {snapshot.fetch_error}</p>
      </section>
    );
  }

  const ratings = [
    { label: "Strong bullish", count: snapshot.rating_strong_buy },
    { label: "Bullish", count: snapshot.rating_buy },
    { label: "Neutral", count: snapshot.rating_hold },
    { label: "Bearish", count: snapshot.rating_sell },
    { label: "Strong bearish", count: snapshot.rating_strong_sell },
  ].filter((tier) => tier.count !== null && tier.count > 0);
  const totalRatings = ratings.reduce((sum, tier) => sum + (tier.count ?? 0), 0);

  const hasTargets =
    snapshot.target_low !== null || snapshot.target_mean !== null || snapshot.target_high !== null;
  const label = consensusLabel(snapshot.consensus_mean);

  return (
    <section className="analyst-consensus mb-8">
      <div className="analyst-consensus-head">
        <div>
          <p className="dashboard-kicker">Wall Street analyst consensus</p>
          <p className="section-explainer">
            Third-party opinion via Yahoo Finance, not this bot&rsquo;s own assessment.
            {snapshot.num_analysts ? ` ${snapshot.num_analysts} analysts.` : ""}
          </p>
        </div>
        {label ? <span className="quiet-count">{label}</span> : null}
      </div>

      {totalRatings > 0 ? (
        <div className="analyst-rating-bar" aria-label="Analyst rating distribution">
          {ratings.map((tier) => (
            <i
              key={tier.label}
              style={{ width: `${((tier.count ?? 0) / totalRatings) * 100}%` }}
              title={`${tier.label}: ${tier.count}`}
            />
          ))}
        </div>
      ) : null}
      {totalRatings > 0 ? (
        <div className="analyst-rating-legend">
          {ratings.map((tier) => (
            <span key={tier.label}>{tier.label} <b>{tier.count}</b></span>
          ))}
        </div>
      ) : null}

      {hasTargets ? (
        <div className="analyst-target-range">
          <div className="analyst-target-labels">
            <span>Low {money(snapshot.target_low, snapshot.currency)}</span>
            <span>Mean {money(snapshot.target_mean, snapshot.currency)}</span>
            <span>High {money(snapshot.target_high, snapshot.currency)}</span>
          </div>
          <div className="analyst-target-track">
            {snapshot.target_low !== null && snapshot.target_high !== null && snapshot.target_high > snapshot.target_low ? (
              <>
                {currentPrice !== null && currentPrice !== undefined ? (
                  <i
                    className="analyst-target-current"
                    style={{
                      left: `${Math.max(0, Math.min(100,
                        ((currentPrice - snapshot.target_low) / (snapshot.target_high - snapshot.target_low)) * 100,
                      ))}%`,
                    }}
                    title={`Current price ${money(currentPrice, snapshot.currency)}`}
                  />
                ) : null}
                {snapshot.target_mean !== null ? (
                  <i
                    className="analyst-target-mean"
                    style={{
                      left: `${Math.max(0, Math.min(100,
                        ((snapshot.target_mean - snapshot.target_low) / (snapshot.target_high - snapshot.target_low)) * 100,
                      ))}%`,
                    }}
                  />
                ) : null}
              </>
            ) : null}
          </div>
          {currentPrice !== null && currentPrice !== undefined ? (
            <p className="analyst-target-current-label">Current price {money(currentPrice, snapshot.currency)}</p>
          ) : null}
        </div>
      ) : null}

      <p className="analyst-consensus-foot">Fetched {formatEastern(snapshot.fetched_at)}</p>
    </section>
  );
}
