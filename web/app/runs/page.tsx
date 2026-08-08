import Link from "next/link";

import { getRecentRuns } from "@/lib/db";
import { formatEastern } from "@/lib/time";

export const dynamic = "force-dynamic";

export default function RunsPage() {
  const runs = getRecentRuns(75).rows;

  return (
    <main className="dashboard-shell runs-page">
      <p className="dashboard-kicker">Pipeline activity</p>
      <h1>Complete run history</h1>
      <p className="page-lede">The latest bot jobs, newest first.</p>
      <div className="run-card-list">
        {runs.map((run) => (
          <article key={run.run_id} className="run-history-card">
            <div>
              <strong>{run.stage.replace(/_/g, " ")}</strong>
              <span className={`run-state ${run.status}`}>{run.status}</span>
            </div>
            <p>{formatEastern(run.started_at)}</p>
            <small>{run.records_written.toLocaleString()} records · {run.run_id}</small>
          </article>
        ))}
      </div>
      <Link href="/health" className="dashboard-link-button">Open detailed system health →</Link>
    </main>
  );
}
