import Link from "next/link";

import {
  getCandidatesForRun,
  getPublishedSelectionRun,
  getTopRankedScores,
} from "@/lib/db";

export const dynamic = "force-dynamic";

function number(value: number | null) {
  return value === null ? "—" : value.toFixed(1);
}

export default function RankingsPage() {
  const ranked = getTopRankedScores(50).rows;
  const run = getPublishedSelectionRun();
  const candidates = run.row ? getCandidatesForRun(run.row.run_id).rows : [];
  const selected = new Set(candidates.map((candidate) => candidate.security_id));

  return (
    <main className="dashboard-shell rankings-page">
      <p className="dashboard-kicker">Latest bot scores</p>
      <h1>Top ranked stocks</h1>
      <p className="page-lede">
        Ranked means a high research score. A green “Selected” label appears only when
        the stock also passed every safety and portfolio filter.
      </p>
      <div className="ranking-status-box">
        <span><small>Ranked here</small><strong>{ranked.length}</strong></span>
        <span><small>Actually selected</small><strong>{candidates.length}</strong></span>
        <Link href="/candidates">See selection filters →</Link>
      </div>

      {ranked.length ? (
        <section className="rank-grid">
          {ranked.map((row) => (
            <Link href={`/security/${row.security_id}`} className="rank-card" key={row.security_id}>
              <div className="rank-card-head">
                <span className="rank-number">#{row.rank}</span>
                <div><strong>{row.symbol ?? row.security_id}</strong><small>{row.name}</small></div>
                <b>{row.composite_score.toFixed(1)}</b>
              </div>
              <div className="score-track"><i style={{ width: `${Math.max(0, Math.min(100, row.composite_score))}%` }} /></div>
              <div className="rank-components">
                <span><small>Value</small>{number(row.value_score)}</span>
                <span><small>Quality</small>{number(row.quality_score)}</span>
                <span><small>Momentum</small>{number(row.momentum_score)}</span>
                <span><small>Dilution</small>{row.dilution_penalty.toFixed(1)}</span>
              </div>
              <p>
                {selected.has(row.security_id) ? <strong className="selected-label">✓ Selected</strong> : "Ranked, not selected"}
                <span className="open-stock-label">Open chart →</span>
              </p>
            </Link>
          ))}
        </section>
      ) : (
        <div className="rank-empty">No ranked stocks are stored yet.</div>
      )}
    </main>
  );
}
