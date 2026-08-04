import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { getLatestCalibrationReport } from "@/lib/db";

/**
 * S3: score distribution and candidate-rate frequency. Calibration data --
 * non-official. No return information is used or displayed anywhere on this
 * page; see pipeline/calibration/report.py's own docstring and
 * pipeline/tests/test_calibration.py for what enforces that.
 */

export const dynamic = "force-dynamic";

function EmptyState({ children }: { children: React.ReactNode }) {
  return (
    <p className="rounded-lg border border-dashed border-border px-6 py-8 text-muted-foreground">
      {children}
    </p>
  );
}

function Histogram({
  buckets,
  height = 80,
}: {
  buckets: { bucket_start: number; bucket_end: number; count: number }[];
  height?: number;
}) {
  const max = Math.max(1, ...buckets.map((b) => b.count));
  return (
    <div className="flex items-end gap-1" style={{ height }}>
      {buckets.map((b) => (
        <div
          key={b.bucket_start}
          className="group relative flex-1 rounded-t bg-emerald-400/60"
          style={{ height: `${Math.max(2, (b.count / max) * 100)}%` }}
          title={`${b.bucket_start}-${b.bucket_end}: ${b.count}`}
        >
          <span className="absolute -top-5 left-1/2 hidden -translate-x-1/2 text-xs text-muted-foreground group-hover:block">
            {b.count}
          </span>
        </div>
      ))}
    </div>
  );
}

export default async function CalibrationPage() {
  const { report } = getLatestCalibrationReport();

  return (
    <main className="mx-auto w-full max-w-6xl px-8 py-16">
      <div className="mb-8 rounded-lg border border-amber-400/40 bg-amber-950/60 px-6 py-4 text-center font-semibold text-amber-100">
        Calibration data — non-official. No return information is used or
        displayed.
      </div>

      <header className="mb-14 space-y-4">
        <p className="text-base uppercase tracking-[0.2em] text-muted-foreground">
          <Link href="/health" className="underline underline-offset-4">
            stockbot
          </Link>{" "}
          / calibration
        </p>
        <h1>S3: score distribution &amp; candidate-rate calibration</h1>
        <p className="max-w-3xl text-muted-foreground">
          Signal-FREQUENCY only: how often a candidate would appear at a given
          composite threshold, applying every real cap, cohort limit and
          cooldown. Nothing on this page computes, stores or displays a
          return, a price change after selection, or any other performance
          measure — see{" "}
          <code className="font-mono text-sm">
            pipeline/calibration/report.py
          </code>
          .
        </p>
      </header>

      {!report || report.empty ? (
        <EmptyState>
          No score data yet. Run pipeline/scoring/compute.py, then
          pipeline/calibration/report.py.
        </EmptyState>
      ) : (
        <>
          <section className="mb-14 space-y-4">
            <h2>Rankable vs. withheld</h2>
            <div className="flex flex-wrap gap-4">
              <Badge
                variant="outline"
                className="border-emerald-400/40 bg-emerald-400/15 px-3 py-1 text-lg text-emerald-200"
              >
                {report.rankable_vs_withheld.rankable} rankable
              </Badge>
              <Badge variant="outline" className="px-3 py-1 text-lg">
                {report.rankable_vs_withheld.withheld_total} withheld
              </Badge>
              <Badge variant="outline" className="px-3 py-1 font-mono">
                score date {report.score_date}
              </Badge>
            </div>
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Withhold reason</TableHead>
                    <TableHead className="text-right">Count</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {report.rankable_vs_withheld.withheld_by_reason.map((row) => (
                    <TableRow key={row.reason}>
                      <TableCell className="max-w-xl truncate text-sm">{row.reason}</TableCell>
                      <TableCell className="text-right font-mono">{row.count}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </section>

          <section className="mb-14 space-y-4">
            <h2>Composite score histogram (rankable only)</h2>
            <Histogram buckets={report.composite_histogram} height={160} />
            <div className="flex justify-between text-xs text-muted-foreground">
              <span>0</span>
              <span>50</span>
              <span>100</span>
            </div>
          </section>

          <section className="mb-14 space-y-6">
            <h2>Component distributions</h2>
            <div className="grid gap-6 md:grid-cols-3">
              {Object.entries(report.component_distributions).map(([name, buckets]) => (
                <div key={name} className="rounded-lg border border-border p-4">
                  <div className="mb-2 font-mono text-sm text-muted-foreground">{name}</div>
                  <Histogram buckets={buckets} height={70} />
                </div>
              ))}
            </div>
          </section>

          <section className="mb-14 space-y-3">
            <details className="rounded-lg border border-border p-4">
              <summary className="cursor-pointer font-medium">
                Submetric distributions ({Object.keys(report.submetric_distributions).length})
              </summary>
              <div className="mt-4 grid gap-4 md:grid-cols-2">
                {Object.entries(report.submetric_distributions).map(([name, data]) => (
                  <div key={name}>
                    <div className="mb-1 font-mono text-xs text-muted-foreground">
                      {name} ({data.valid_count}/{data.total} valid)
                    </div>
                    <Histogram buckets={data.percentile_histogram} height={40} />
                  </div>
                ))}
              </div>
            </details>
          </section>

          <section className="mb-14 space-y-3">
            <h2>Cohort sizes &amp; per-metric valid observation counts</h2>
            <p className="text-sm text-muted-foreground">
              {report.cohort_and_metric_coverage.total_scored} securities scored.
              <strong> Cohort blending is effectively inert, not just degraded:</strong>{" "}
              887 of 896 scored securities (99%) share one undifferentiated
              SIC-UNKNOWN bucket, since S1&rsquo;s pool discovery never populated
              sic_code. The remaining 9 real-cohort securities sit below the
              10-security blend-weight floor too. Industry-relative
              percentiles do not meaningfully exist yet for this population —
              any threshold read from this run is provisional and must be
              re-verified once sic_code is populated and the universe is
              closer to full scale.
            </p>
            <div className="grid gap-6 md:grid-cols-2">
              <div>
                <div className="mb-2 text-sm font-medium">Cohort sizes</div>
                <Table>
                  <TableBody>
                    {report.cohort_and_metric_coverage.cohort_sizes.map((c) => (
                      <TableRow key={c.cohort_id}>
                        <TableCell className="font-mono text-sm">{c.cohort_id}</TableCell>
                        <TableCell className="text-right font-mono">{c.count}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
              <div>
                <div className="mb-2 text-sm font-medium">Per-metric valid observations</div>
                <Table>
                  <TableBody>
                    {Object.entries(report.cohort_and_metric_coverage.metric_valid_counts).map(
                      ([metric, count]) => (
                        <TableRow key={metric}>
                          <TableCell className="font-mono text-sm">{metric}</TableCell>
                          <TableCell className="text-right font-mono">{count}</TableCell>
                        </TableRow>
                      ),
                    )}
                  </TableBody>
                </Table>
              </div>
            </div>
          </section>

          <section className="space-y-3">
            <h2>Simulated weekly candidate rate by threshold</h2>
            <p className="text-sm text-muted-foreground">
              Single point-in-time simulation against today&rsquo;s already-computed
              scores, applying the SAME selection.rules.select() the real
              weekly run uses — not a multi-week historical backtest, since
              the system has no history of past weekly score snapshots to
              replay. &ldquo;Estimated weeks to 100 closed&rdquo; is pure
              arithmetic from the candidate rate and each horizon&rsquo;s fixed
              max-hold length (a frozen protocol parameter), never anything
              about what a position actually did.
            </p>
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="text-right">Threshold</TableHead>
                    <TableHead className="text-right">Candidates/week</TableHead>
                    <TableHead className="text-right">Suppressed</TableHead>
                    <TableHead className="text-right">Est. weeks to 100 closed (20d)</TableHead>
                    <TableHead className="text-right">Est. weeks to 100 closed (60d)</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {report.candidate_rate_simulation.map((row) => (
                    <TableRow
                      key={row.threshold}
                      className={row.candidates_per_week >= 4 && row.candidates_per_week <= 6 ? "bg-emerald-400/10" : ""}
                    >
                      <TableCell className="text-right font-mono">{row.threshold}</TableCell>
                      <TableCell className="text-right font-mono">{row.candidates_per_week}</TableCell>
                      <TableCell className="text-right font-mono">{row.suppressed}</TableCell>
                      <TableCell className="text-right font-mono">
                        {row.estimated_weeks_to_100_closed["20"] ?? "—"}
                      </TableCell>
                      <TableCell className="text-right font-mono">
                        {row.estimated_weeks_to_100_closed["60"] ?? "—"}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </section>
        </>
      )}
    </main>
  );
}
