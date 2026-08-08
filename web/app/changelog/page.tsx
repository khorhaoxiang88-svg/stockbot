import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { getPublishedDefects, type Defect } from "@/lib/db";
import { formatEastern } from "@/lib/time";

/**
 * O3's bug-correction policy, published. defect_log (migration 023) is the
 * audit trail; this page renders only PUBLISHED rows (published_at IS NOT
 * NULL) -- a defect under investigation is not shown until the policy's own
 * workflow reaches that step.
 *
 * Severity maps directly to the policy text:
 *   cosmetic       -- no experiment restart, logged.
 *   data_correction -- no official candidate affected, audited and logged.
 *   material       -- an official candidate was affected: the strategy
 *                      version is COMPROMISED (experiments.compromised_reason,
 *                      migration 022), every record is preserved untouched
 *                      (migration 023's immutability triggers), and a new,
 *                      separately-reported strategy_version begins.
 */

export const dynamic = "force-dynamic";

function EmptyState({ children }: { children: React.ReactNode }) {
  return (
    <p className="rounded-lg border border-dashed border-border px-6 py-8 text-muted-foreground">
      {children}
    </p>
  );
}

function SeverityBadge({ severity }: { severity: Defect["severity"] }) {
  const tone =
    severity === "material"
      ? "border-red-400/40 bg-red-400/15 text-red-200"
      : severity === "data_correction"
        ? "border-amber-400/40 bg-amber-400/15 text-amber-200"
        : "border-border bg-muted/40 text-muted-foreground";
  const label =
    severity === "material"
      ? "Material — official candidate affected"
      : severity === "data_correction"
        ? "Data correction — no official candidate affected"
        : "Cosmetic";
  return (
    <Badge variant="outline" className={`px-3 py-1 text-sm ${tone}`}>
      {label}
    </Badge>
  );
}

export default async function ChangelogPage() {
  const { rows: defects } = getPublishedDefects();

  return (
    <main className="mx-auto max-w-4xl px-6 py-12">
      <p className="mb-2 font-mono text-xs uppercase tracking-widest text-muted-foreground">
        <Link href="/health" className="underline underline-offset-4">
          stockbot
        </Link>{" "}
        / changelog
      </p>
      <h1 className="mb-2">Changelog</h1>
      <p className="mb-10 text-muted-foreground">
        Every published defect that touched this system, however small. A
        material defect that affected an official candidate compromises that
        strategy version permanently (see /performance and /health for its
        marker) rather than being corrected in place — official results are
        never silently rewritten.
      </p>

      {defects.length === 0 ? (
        <EmptyState>
          No defect has been published yet. This is the correct state for a
          system with no known defects, not a missing feature.
        </EmptyState>
      ) : (
        <div className="space-y-6">
          {defects.map((d) => (
            <div key={d.defect_id} className="rounded-lg border border-border p-5 space-y-3">
              <div className="flex flex-wrap items-center gap-3">
                <SeverityBadge severity={d.severity} />
                <Badge variant="outline" className="px-3 py-1 font-mono text-xs">
                  {d.defect_id}
                </Badge>
                <span className="text-xs text-muted-foreground">
                  discovered {formatEastern(d.discovered_at)}
                  {d.published_at ? ` · published ${formatEastern(d.published_at)}` : ""}
                </span>
              </div>
              <p className="text-sm">{d.description}</p>
              {d.affected_strategy_version !== null ? (
                <p className="font-mono text-xs text-muted-foreground">
                  affected strategy v{d.affected_strategy_version}
                  {d.new_strategy_version !== null
                    ? ` → compromised → new strategy v${d.new_strategy_version}, separately reported`
                    : ""}
                </p>
              ) : null}
              {d.resolution ? (
                <p className="text-sm text-muted-foreground">
                  <span className="font-semibold text-foreground">Resolution: </span>
                  {d.resolution}
                </p>
              ) : (
                <p className="text-xs text-amber-200">Resolution not yet recorded.</p>
              )}
            </div>
          ))}
        </div>
      )}
    </main>
  );
}
