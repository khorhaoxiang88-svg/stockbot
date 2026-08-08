import Link from "next/link";

import { searchSecurities } from "@/lib/db";

export const dynamic = "force-dynamic";

function number(value: number | null) {
  return value === null ? "—" : value.toFixed(1);
}

export default async function SearchPage({
  searchParams,
}: {
  searchParams: Promise<{ q?: string }>;
}) {
  const { q } = await searchParams;
  const query = (q ?? "").trim();
  const results = query ? searchSecurities(query, 30).rows : [];

  return (
    <main className="dashboard-shell rankings-page">
      <p className="dashboard-kicker">Look up a stock</p>
      <h1>Search</h1>
      <p className="page-lede">
        Ticker or company name. Shows the same score this system uses to rank every
        stock it tracks — not an opinion on whether to trade it.
      </p>

      <form action="/search" method="get" className="search-form">
        <input
          type="text"
          name="q"
          defaultValue={query}
          placeholder="e.g. AAPL or Apple"
          autoFocus
          aria-label="Search by ticker or company name"
        />
        <button type="submit">Search</button>
      </form>

      {!query ? null : results.length ? (
        <section className="rank-grid">
          {results.map((row) => (
            <Link href={`/security/${row.security_id}`} className="rank-card" key={row.security_id}>
              <div className="rank-card-head">
                <span className="rank-number">{row.rank ? `#${row.rank}` : "—"}</span>
                <div><strong>{row.symbol ?? row.security_id}</strong><small>{row.name}</small></div>
                <b>{number(row.composite_score)}</b>
              </div>
              {row.composite_score !== null ? (
                <div className="score-track">
                  <i style={{ width: `${Math.max(0, Math.min(100, row.composite_score))}%` }} />
                </div>
              ) : null}
              {row.composite_score !== null ? (
                <div className="rank-components">
                  <span><small>Value</small>{number(row.value_score)}</span>
                  <span><small>Quality</small>{number(row.quality_score)}</span>
                  <span><small>Momentum</small>{number(row.momentum_score)}</span>
                  <span><small>Dilution</small>{number(row.dilution_penalty)}</span>
                </div>
              ) : (
                <p className="score-withheld">
                  Not scored{row.withhold_reason ? `: ${row.withhold_reason}` : "."}
                </p>
              )}
              <p><span className="open-stock-label">Open chart →</span></p>
            </Link>
          ))}
        </section>
      ) : (
        <div className="rank-empty">No stock matches “{query}”.</div>
      )}
    </main>
  );
}
