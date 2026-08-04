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
import {
  getFixtureCoverageInSnapshot,
  getLatestUniverseSnapshotRun,
  getUniverseMembershipChanges,
  getUniverseSnapshotRows,
  type UniverseMember,
} from "@/lib/db";
import { formatEastern } from "@/lib/time";

/**
 * S1: the full rules-based universe. Still non-official -- nothing here
 * feeds an official candidate or an official statistic. Its purpose is
 * membership, status and the reason for every inclusion and exclusion,
 * exactly like the suppression log on /candidates: a list of who's in is
 * not auditable on its own, only a list of everyone considered with a
 * reason is.
 */

export const dynamic = "force-dynamic";

function EmptyState({ children }: { children: React.ReactNode }) {
  return (
    <p className="rounded-lg border border-dashed border-border px-6 py-8 text-muted-foreground">
      {children}
    </p>
  );
}

function money(value: number | null): string {
  if (value === null) return "—";
  return "$" + value.toLocaleString(undefined, { maximumFractionDigits: 0 });
}

function StatusBadge({ status }: { status: UniverseMember["status"] }) {
  const styles: Record<UniverseMember["status"], string> = {
    included: "border-emerald-400/40 bg-emerald-400/15 text-emerald-200",
    watch: "border-amber-400/40 bg-amber-400/15 text-amber-200",
    excluded: "border-border bg-muted/40 text-muted-foreground",
  };
  return (
    <Badge variant="outline" className={`px-2 py-0.5 font-mono text-xs ${styles[status]}`}>
      {status}
    </Badge>
  );
}

function MemberRow({ row }: { row: UniverseMember }) {
  return (
    <TableRow>
      <TableCell>
        <Link href={`/security/${row.security_id}`} className="underline underline-offset-4">
          {row.symbol ?? row.security_id}
        </Link>
        <div className="text-xs text-muted-foreground">{row.name}</div>
      </TableCell>
      <TableCell>
        <StatusBadge status={row.status} />
      </TableCell>
      <TableCell className="text-right font-mono">{money(row.market_cap)}</TableCell>
      <TableCell className="text-right font-mono">{money(row.adv_dollar)}</TableCell>
      <TableCell className="text-right font-mono">
        {row.days_below_retention > 0 ? `${row.days_below_retention}d` : "—"}
      </TableCell>
      <TableCell className="text-sm text-muted-foreground">
        {row.exclusion_reason ?? "—"}
      </TableCell>
    </TableRow>
  );
}

export default async function UniversePage() {
  const monthly = getLatestUniverseSnapshotRun("monthly_membership");
  const rows = monthly.row ? getUniverseSnapshotRows(monthly.row.snapshot_id).rows : [];
  const changes = getUniverseMembershipChanges(200).rows;
  const fixtureCoverage = monthly.row
    ? getFixtureCoverageInSnapshot(monthly.row.snapshot_id)
    : { covered: 0, total: 0 };

  const included = rows.filter((r) => r.status === "included");
  const watch = rows.filter((r) => r.status === "watch");
  const excluded = rows.filter((r) => r.status === "excluded");

  const exclusionCounts = new Map<string, number>();
  for (const row of excluded) {
    const reason = row.exclusion_reason ?? "unknown";
    exclusionCounts.set(reason, (exclusionCounts.get(reason) ?? 0) + 1);
  }
  const exclusionBreakdown = [...exclusionCounts.entries()].sort((a, b) => b[1] - a[1]);

  return (
    <main className="mx-auto max-w-6xl px-6 py-12">
      <p className="mb-2 font-mono text-xs uppercase tracking-widest text-muted-foreground">
        <Link href="/health" className="underline underline-offset-4">
          stockbot
        </Link>{" "}
        / universe
      </p>
      <h1 className="mb-2">S1 rules-based universe</h1>
      <p className="mb-10 text-muted-foreground">
        Replaces the 50-security fixture with the full rules-based universe.{" "}
        <strong>Still non-official</strong> — nothing here feeds an official
        candidate or statistic. Every inclusion and exclusion carries a
        specific reason; a security oscillating around the entry threshold
        cannot flap in and out, because once included it is judged against
        the lower retention thresholds, not entry, until it fails them for a
        full hysteresis window and a monthly run formalises the exit.
      </p>

      {!monthly.row ? (
        <EmptyState>
          No universe snapshot yet. Run pipeline/universe/pool_loader.py to
          discover candidates, then pipeline/universe/membership.py (via a
          monthly_membership compute_snapshot call) to evaluate them.
        </EmptyState>
      ) : (
        <>
          <section className="mb-14 space-y-4">
            <h2>Latest monthly membership run</h2>
            <div className="flex flex-wrap items-center gap-4">
              <Badge
                variant="outline"
                className="border-emerald-400/40 bg-emerald-400/15 px-3 py-1 text-lg text-emerald-200"
              >
                {included.length} included
              </Badge>
              <Badge
                variant="outline"
                className="border-amber-400/40 bg-amber-400/15 px-3 py-1 text-lg text-amber-200"
              >
                {watch.length} on watch
              </Badge>
              <Badge variant="outline" className="px-3 py-1 text-lg">
                {excluded.length} excluded
              </Badge>
              <Badge variant="outline" className="px-3 py-1 font-mono">
                snapshot {monthly.row.snapshot_id}
              </Badge>
              <Badge variant="outline" className="px-3 py-1 font-mono">
                rules {monthly.row.rules_version}
              </Badge>
            </div>
            <p className="text-sm text-muted-foreground">
              Effective {formatEastern(monthly.row.effective_at)}. {rows.length}{" "}
              securities evaluated. Fixture coverage: {fixtureCoverage.covered} of{" "}
              {fixtureCoverage.total} Phase F fixture securities accounted for,
              included or excluded, in this snapshot.
            </p>
          </section>

          {exclusionBreakdown.length > 0 ? (
            <section className="mb-14 space-y-3">
              <h2>Exclusion reasons</h2>
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Reason</TableHead>
                      <TableHead className="text-right">Count</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {exclusionBreakdown.map(([reason, count]) => (
                      <TableRow key={reason}>
                        <TableCell className="text-sm">{reason}</TableCell>
                        <TableCell className="text-right font-mono">{count}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            </section>
          ) : null}

          <section className="mb-14 space-y-3">
            <h2>Membership, status and reason</h2>
            {rows.length === 0 ? (
              <EmptyState>No securities evaluated in this snapshot.</EmptyState>
            ) : (
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Security</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead className="text-right">Market cap</TableHead>
                      <TableHead className="text-right">60d ADV</TableHead>
                      <TableHead className="text-right">Below retention</TableHead>
                      <TableHead>Reason</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {rows.map((row) => (
                      <MemberRow key={row.security_id} row={row} />
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </section>

          <section className="space-y-3">
            <h2>Monthly membership change log</h2>
            <p className="text-sm text-muted-foreground">
              Only monthly runs may formally enter or exit a security. Daily
              safety runs can suspend a member to &ldquo;watch&rdquo;
              immediately, but never change formal membership, so nothing from
              a daily run appears here.
            </p>
            {changes.length === 0 ? (
              <EmptyState>No membership changes recorded yet.</EmptyState>
            ) : (
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Date</TableHead>
                      <TableHead>Security</TableHead>
                      <TableHead>Change</TableHead>
                      <TableHead>Reason</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {changes.map((change) => (
                      <TableRow key={change.change_id}>
                        <TableCell className="whitespace-nowrap font-mono text-sm">
                          {change.effective_date}
                        </TableCell>
                        <TableCell>
                          <Link
                            href={`/security/${change.security_id}`}
                            className="underline underline-offset-4"
                          >
                            {change.symbol ?? change.security_id}
                          </Link>
                        </TableCell>
                        <TableCell>
                          <Badge
                            variant="outline"
                            className={
                              change.change_type === "entered"
                                ? "border-emerald-400/40 bg-emerald-400/15 px-2 py-0.5 font-mono text-xs text-emerald-200"
                                : "border-border bg-muted/40 px-2 py-0.5 font-mono text-xs text-muted-foreground"
                            }
                          >
                            {change.change_type}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-sm text-muted-foreground">
                          {change.reason}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </section>
        </>
      )}
    </main>
  );
}
