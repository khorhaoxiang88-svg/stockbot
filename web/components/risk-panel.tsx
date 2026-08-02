import { Badge } from "@/components/ui/badge";
import { RISK_FLAG_LABELS, type RiskFlag } from "@/lib/db";

/**
 * "Measured risks and missing evidence".
 *
 * The title is the point. A panel called "Risks" that lists two items reads as
 * "there are two risks"; this one has to say, in its own heading, that some
 * checks produced nothing because they could not be run.
 *
 * Three sections, in this order and never merged:
 *
 *   DETECTED    a check ran and found something, graded high/medium/low
 *   UNKNOWN     a check could not run, with the reason
 *   CONTEXT     neutral information, explicitly not bearish
 *
 * plus a collapsed list of the checks that ran clean, so a reader can see the
 * detector was actually exercised rather than skipped.
 *
 * Visual weight matches the composite score section deliberately: same heading
 * level, same badge sizing, same card treatment. A risk panel rendered smaller
 * than the score would be an editorial claim about which matters more.
 */

const SEVERITY_TONE: Record<string, string> = {
  high: "border-red-400/40 bg-red-400/15 text-red-200",
  medium: "border-amber-400/40 bg-amber-400/15 text-amber-200",
  low: "border-amber-400/30 bg-amber-400/10 text-amber-100",
  unknown: "border-slate-400/40 bg-slate-400/15 text-slate-200",
  context: "border-sky-400/40 bg-sky-400/15 text-sky-200",
  none: "border-emerald-400/30 bg-emerald-400/10 text-emerald-200",
};

function label(flag: RiskFlag): string {
  return RISK_FLAG_LABELS[flag.flag_code] ?? flag.flag_code;
}

function SourceLink({ flag }: { flag: RiskFlag }) {
  const reference = flag.source_accession;
  if (!reference || reference === "none") {
    return (
      <span className="text-xs text-muted-foreground">
        No filing to cite for this result.
      </span>
    );
  }
  if (reference.startsWith("ledger:corporate_actions:")) {
    const parts = reference.split(":");
    const exDate = parts[3];
    return (
      <span className="text-xs text-muted-foreground">
        Source: corporate-actions ledger
        {exDate && exDate !== "none" ? `, ex-date ${exDate}` : ""} — from the price
        vendor, not an SEC filing.
      </span>
    );
  }
  if (flag.source_url) {
    return (
      <a
        href={flag.source_url}
        target="_blank"
        rel="noreferrer"
        className="text-xs underline underline-offset-4"
      >
        Source: {flag.source_form ?? "filing"} {reference}
        {flag.source_filed_date ? ` filed ${flag.source_filed_date}` : ""}
      </a>
    );
  }
  return (
    <span className="text-xs text-muted-foreground">
      Source: accession <span className="font-mono">{reference}</span>
    </span>
  );
}

function FlagCard({ flag }: { flag: RiskFlag }) {
  return (
    <li className={`rounded-lg border p-4 ${SEVERITY_TONE[flag.severity] ?? ""}`}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="font-medium">{label(flag)}</span>
        <Badge variant="outline" className="px-2 py-0.5 font-mono text-xs">
          {flag.severity}
        </Badge>
      </div>
      <p className="mt-2 text-sm leading-relaxed">{flag.evidence_text}</p>
      <div className="mt-2">
        <SourceLink flag={flag} />
      </div>
    </li>
  );
}

export function RiskPanel({
  flags,
  asOfDate,
}: {
  flags: RiskFlag[];
  asOfDate: string | null;
}) {
  const detected = flags.filter((f) => ["high", "medium", "low"].includes(f.severity));
  const unknowns = flags.filter((f) => f.severity === "unknown");
  const context = flags.filter((f) => f.severity === "context");
  const clean = flags.filter((f) => f.severity === "none");

  const counts = {
    high: detected.filter((f) => f.severity === "high").length,
    medium: detected.filter((f) => f.severity === "medium").length,
    low: detected.filter((f) => f.severity === "low").length,
  };

  return (
    <section className="mb-14 space-y-6">
      <h2>Measured risks and missing evidence</h2>

      {flags.length === 0 ? (
        <p className="rounded-lg border border-dashed border-border px-6 py-8 text-muted-foreground">
          No data yet. Run pipeline/riskflags/compute.py to evaluate this security.
        </p>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-4">
            <Badge
              variant="outline"
              className={`px-3 py-1 text-lg ${
                detected.length
                  ? SEVERITY_TONE.high
                  : "border-emerald-400/40 bg-emerald-400/15 text-emerald-200"
              }`}
            >
              {detected.length} detected
            </Badge>
            <Badge variant="outline" className={`px-3 py-1 text-lg ${SEVERITY_TONE.unknown}`}>
              {unknowns.length} could not determine
            </Badge>
            <Badge variant="outline" className="px-3 py-1 font-mono">
              {counts.high} high · {counts.medium} medium · {counts.low} low
            </Badge>
            {asOfDate ? (
              <Badge variant="outline" className="px-3 py-1 font-mono">
                as of {asOfDate}
              </Badge>
            ) : null}
          </div>

          <p className="text-sm text-muted-foreground">
            These are deterministic detectors, not a written bear case. Nothing here
            was generated or interpreted; each entry states what was measured and
            links to the filing it was measured from. An empty section means the
            check ran and found nothing — never that the check was skipped.
          </p>

          <div className="space-y-3">
            <h3 className="text-lg font-semibold">Detected risks</h3>
            {detected.length === 0 ? (
              <p className="rounded-lg border border-dashed border-border px-6 py-6 text-muted-foreground">
                None detected. {clean.length} check
                {clean.length === 1 ? "" : "s"} ran and found nothing; see the
                unknowns below for what could not be checked.
              </p>
            ) : (
              <ul className="grid gap-3">
                {detected.map((flag) => (
                  <FlagCard key={flag.flag_code} flag={flag} />
                ))}
              </ul>
            )}
          </div>

          <div className="space-y-3">
            <h3 className="text-lg font-semibold">Could not determine</h3>
            {unknowns.length === 0 ? (
              <p className="rounded-lg border border-dashed border-border px-6 py-6 text-muted-foreground">
                Every check ran. Nothing was left undetermined for this security.
              </p>
            ) : (
              <ul className="grid gap-3">
                {unknowns.map((flag) => (
                  <FlagCard key={flag.flag_code} flag={flag} />
                ))}
              </ul>
            )}
          </div>

          {context.length ? (
            <div className="space-y-3">
              <h3 className="text-lg font-semibold">Context, not a risk signal</h3>
              <ul className="grid gap-3">
                {context.map((flag) => (
                  <FlagCard key={flag.flag_code} flag={flag} />
                ))}
              </ul>
            </div>
          ) : null}

          {clean.length ? (
            <details className="rounded-lg border border-border p-4">
              <summary className="cursor-pointer font-medium">
                {clean.length} check{clean.length === 1 ? "" : "s"} ran and detected
                nothing
              </summary>
              <ul className="mt-3 grid gap-3">
                {clean.map((flag) => (
                  <FlagCard key={flag.flag_code} flag={flag} />
                ))}
              </ul>
            </details>
          ) : null}
        </>
      )}
    </section>
  );
}
