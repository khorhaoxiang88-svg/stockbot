import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { tryLoadConfig } from "@/lib/config";
import {
  getAppliedMigrations,
  getFixtureConfidenceCounts,
  getFixtureRows,
  getFixtureTypeCounts,
  getRecentRuns,
  getSourceHealth,
  industryLabel,
} from "@/lib/db";
import { formatEastern } from "@/lib/time";

// Health must reflect the database as it is right now, never a cached build.
export const dynamic = "force-dynamic";

function EmptyState({ children }: { children: React.ReactNode }) {
  return (
    <p className="rounded-lg border border-dashed border-border px-6 py-8 text-muted-foreground">
      {children}
    </p>
  );
}

function StatusBadge({ status }: { status: string }) {
  const tone =
    status === "success"
      ? "border-emerald-400/40 bg-emerald-400/15 text-emerald-200"
      : status === "failed"
        ? "border-red-400/40 bg-red-400/15 text-red-200"
        : status === "running"
          ? "border-sky-400/40 bg-sky-400/15 text-sky-200"
          : "border-amber-400/40 bg-amber-400/15 text-amber-200";
  return (
    <Badge variant="outline" className={`px-3 py-1 text-base ${tone}`}>
      {status}
    </Badge>
  );
}

export default async function HealthPage() {
  const sources = getSourceHealth();
  const runs = getRecentRuns();
  const migrations = getAppliedMigrations();
  const config = tryLoadConfig();
  const fixtureRows = getFixtureRows();
  const typeCounts = getFixtureTypeCounts();
  const confidenceCounts = getFixtureConfidenceCounts();
  const unknownCount =
    typeCounts.rows.find((row) => row.security_type === "unknown")?.n ?? 0;

  const dbState = sources.status.state;

  return (
    <main className="mx-auto w-full max-w-6xl px-8 py-16">
      <header className="mb-16 space-y-4">
        <p className="text-base uppercase tracking-[0.2em] text-muted-foreground">
          stockbot
        </p>
        <h1>System health</h1>
        <p className="max-w-3xl text-muted-foreground">
          Phase 1 skeleton. No market data is collected yet, so empty tables below
          are the expected state. All timestamps are stored in UTC and shown here
          in US Eastern.
        </p>
        <p className="text-base text-muted-foreground">
          <Link href="/" className="underline underline-offset-4 hover:text-foreground">
            Back to overview
          </Link>
        </p>
      </header>

      <div className="mb-14 grid gap-8 md:grid-cols-2">
        <Card className="gap-6 py-8">
          <CardHeader className="px-8">
            <CardTitle className="text-2xl">Database</CardTitle>
            <CardDescription className="text-base break-all">
              {sources.status.path}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-5 px-8">
            <div className="flex items-center gap-4">
              <span className="text-muted-foreground">Status</span>
              {dbState === "ok" ? (
                <Badge
                  variant="outline"
                  className="border-emerald-400/40 bg-emerald-400/15 px-3 py-1 text-base text-emerald-200"
                >
                  connected
                </Badge>
              ) : dbState === "missing" ? (
                <Badge
                  variant="outline"
                  className="border-amber-400/40 bg-amber-400/15 px-3 py-1 text-base text-amber-200"
                >
                  not created yet
                </Badge>
              ) : (
                <Badge
                  variant="outline"
                  className="border-red-400/40 bg-red-400/15 px-3 py-1 text-base text-red-200"
                >
                  error
                </Badge>
              )}
            </div>

            {sources.status.state === "error" && (
              <p className="text-base text-red-200">{sources.status.message}</p>
            )}

            <div className="space-y-3">
              <span className="text-muted-foreground">Migrations applied</span>
              {migrations.rows.length === 0 ? (
                <p className="text-muted-foreground">
                  None yet. Run{" "}
                  <code className="font-mono text-base">python pipeline/migrate.py up</code>
                </p>
              ) : (
                <ul className="space-y-2">
                  {migrations.rows.map((m) => (
                    <li key={m.version} className="flex flex-wrap items-baseline gap-3">
                      <span className="font-mono">{m.version}</span>
                      <span className="text-base text-muted-foreground">
                        {formatEastern(m.applied_at)}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </CardContent>
        </Card>

        <Card className="gap-6 py-8">
          <CardHeader className="px-8">
            <CardTitle className="text-2xl">Frozen configuration</CardTitle>
            <CardDescription className="text-base">
              config.frozen.json — locked for Release 1
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-5 px-8">
            {config.ok ? (
              <>
                <div className="flex items-center gap-4">
                  <span className="text-muted-foreground">Required keys</span>
                  <Badge
                    variant="outline"
                    className="border-emerald-400/40 bg-emerald-400/15 px-3 py-1 text-base text-emerald-200"
                  >
                    {config.keyCount} loaded
                  </Badge>
                </div>
                <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-base">
                  {[
                    ["strategy", "strategy_version"],
                    ["selection rule", "selection_rule_version"],
                    ["protocol", "protocol_version"],
                    ["resolution policy", "resolution_policy_version"],
                    ["accrual policy", "accrual_policy_version"],
                    ["mapping", "mapping_version"],
                  ].map(([label, key]) => (
                    <div key={key} className="flex items-baseline justify-between gap-3">
                      <dt className="text-muted-foreground">{label}</dt>
                      <dd className="font-mono">v{String(config.config[key])}</dd>
                    </div>
                  ))}
                </dl>
                {config.pendingPlaceholders.length > 0 && (
                  <p className="text-base text-amber-200">
                    Placeholder still unset: {config.pendingPlaceholders.join(", ")}
                  </p>
                )}
              </>
            ) : (
              <p className="text-base text-red-200">{config.message}</p>
            )}
          </CardContent>
        </Card>
      </div>

      <section className="mb-14 space-y-6">
        <h2>Fixture</h2>
        {fixtureRows.rows.length === 0 ? (
          <EmptyState>
            No data yet. The fixture manifest has not been loaded — run{" "}
            <code className="font-mono text-base">
              python pipeline/universe/load_fixture.py
            </code>
          </EmptyState>
        ) : (
          <>
            <div className="grid gap-8 md:grid-cols-2">
              <Card className="gap-6 py-8">
                <CardHeader className="px-8">
                  <CardTitle className="text-2xl">Count by type</CardTitle>
                  <CardDescription className="text-base">
                    {fixtureRows.rows.length} securities, manifest version{" "}
                    {fixtureRows.rows[0]?.manifest_version}
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-3 px-8">
                  {typeCounts.rows.map((row) => (
                    <div
                      key={row.security_type}
                      className="flex items-baseline justify-between gap-4 border-b border-border pb-3 last:border-b-0"
                    >
                      <span
                        className={
                          row.security_type === "unknown"
                            ? "text-red-200"
                            : "text-muted-foreground"
                        }
                      >
                        {row.security_type.replace(/_/g, " ")}
                      </span>
                      <span className="font-mono font-medium">{row.n}</span>
                    </div>
                  ))}
                  <p
                    className={`pt-2 text-base ${
                      unknownCount === 0 ? "text-emerald-200" : "text-red-200"
                    }`}
                  >
                    {unknownCount === 0
                      ? "Zero securities classified unknown."
                      : `${unknownCount} securities classified unknown — these are never ranked.`}
                  </p>
                </CardContent>
              </Card>

              <Card className="gap-6 py-8">
                <CardHeader className="px-8">
                  <CardTitle className="text-2xl">Count by confidence</CardTitle>
                  <CardDescription className="text-base">
                    Every classification records its evidence
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-3 px-8">
                  {confidenceCounts.rows.map((row) => (
                    <div
                      key={row.classification_confidence}
                      className="flex items-baseline justify-between gap-4 border-b border-border pb-3 last:border-b-0"
                    >
                      <span className="text-muted-foreground">
                        {row.classification_confidence}
                      </span>
                      <span className="font-mono font-medium">{row.n}</span>
                    </div>
                  ))}
                </CardContent>
              </Card>
            </div>

            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="text-base">Symbol</TableHead>
                  <TableHead className="text-base">id</TableHead>
                  <TableHead className="text-base">Name</TableHead>
                  <TableHead className="text-base">Type</TableHead>
                  <TableHead className="text-base">Confidence</TableHead>
                  <TableHead className="text-base">Category</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {fixtureRows.rows.map((row) => {
                  const industry = industryLabel(row.sic_code);
                  return (
                    <TableRow key={row.security_id}>
                      <TableCell className="font-mono">
                        <Link
                          href={`/security/${row.security_id}`}
                          className="underline underline-offset-4 hover:text-foreground"
                        >
                          {row.symbol_at_selection}
                        </Link>
                      </TableCell>
                      <TableCell className="font-mono">{row.security_id}</TableCell>
                      <TableCell>
                        {row.name}
                        {industry && (
                          <span className="ml-3 text-base text-sky-200">{industry}</span>
                        )}
                      </TableCell>
                      <TableCell>{row.security_type.replace(/_/g, " ")}</TableCell>
                      <TableCell
                        className={
                          row.classification_confidence === "high"
                            ? "text-emerald-200"
                            : row.classification_confidence === "medium"
                              ? "text-amber-200"
                              : "text-red-200"
                        }
                      >
                        {row.classification_confidence}
                      </TableCell>
                      <TableCell className="text-muted-foreground">{row.category}</TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </>
        )}
      </section>

      <section className="mb-14 space-y-6">
        <h2>Source health</h2>
        {sources.rows.length === 0 ? (
          <EmptyState>
            No data yet. No source has reported in — expected until data ingestion is
            built.
          </EmptyState>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="text-base">Source</TableHead>
                <TableHead className="text-base">Last success (ET)</TableHead>
                <TableHead className="text-base">Last error (ET)</TableHead>
                <TableHead className="text-base text-right">Fails in a row</TableHead>
                <TableHead className="text-base text-right">Staleness (h)</TableHead>
                <TableHead className="text-base text-right">Coverage</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {sources.rows.map((s) => (
                <TableRow key={s.source_name}>
                  <TableCell className="font-medium">{s.source_name}</TableCell>
                  <TableCell>{formatEastern(s.last_success)}</TableCell>
                  <TableCell>{formatEastern(s.last_error)}</TableCell>
                  <TableCell className="text-right">{s.consecutive_failures}</TableCell>
                  <TableCell className="text-right">
                    {s.staleness_hours === null ? "—" : s.staleness_hours.toFixed(1)}
                  </TableCell>
                  <TableCell className="text-right">
                    {s.coverage_pct === null ? "—" : `${s.coverage_pct.toFixed(1)}%`}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </section>

      <section className="space-y-6">
        <h2>Recent pipeline runs</h2>
        {runs.rows.length === 0 ? (
          <EmptyState>
            No data yet. The pipeline has not run — expected until a stage is built.
          </EmptyState>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="text-base">Run</TableHead>
                <TableHead className="text-base">Stage</TableHead>
                <TableHead className="text-base">Started (ET)</TableHead>
                <TableHead className="text-base">Finished (ET)</TableHead>
                <TableHead className="text-base">Status</TableHead>
                <TableHead className="text-base text-right">Records</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {runs.rows.map((r) => (
                <TableRow key={r.run_id}>
                  <TableCell className="font-mono text-base">{r.run_id}</TableCell>
                  <TableCell>{r.stage}</TableCell>
                  <TableCell>{formatEastern(r.started_at)}</TableCell>
                  <TableCell>{formatEastern(r.finished_at)}</TableCell>
                  <TableCell>
                    <StatusBadge status={r.status} />
                  </TableCell>
                  <TableCell className="text-right">{r.records_written}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </section>
    </main>
  );
}
