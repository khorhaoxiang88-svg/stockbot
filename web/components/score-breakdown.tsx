import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type {
  PiotroskiSignal,
  ScoreComponent,
  ScoreExplanation,
  ScoreRow,
  ScoreSubmetric,
} from "@/lib/db";

/**
 * The score breakdown, rendered from explanation_json alone.
 *
 * Every number on this screen comes out of the stored explanation, not out of a
 * second calculation in the browser. If the pipeline and this page ever
 * disagreed, the page would be lying about what was stored, so it is not
 * allowed to compute anything except the sums a reader would do by hand.
 *
 * An unrankable security shows its withhold reason where the score would be. It
 * never shows a zero: zero is a real score meaning "worst in the universe", and
 * a security we could not evaluate has not earned it.
 */

const NUMBER_MISSING = "—";

function num(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return NUMBER_MISSING;
  return value.toFixed(digits);
}

function signed(value: number | null | undefined, digits = 4): string {
  if (value === null || value === undefined) return NUMBER_MISSING;
  return `${value >= 0 ? "+" : "−"}${Math.abs(value).toFixed(digits)}`;
}

function metricLabel(metric: string): string {
  const labels: Record<string, string> = {
    pe: "P/E",
    pb: "P/B",
    ev_ebitda: "EV/EBITDA",
    fcf_yield: "FCF yield",
    roic: "ROIC",
    interest_coverage: "Interest coverage",
    debt_ebitda: "Debt/EBITDA",
    current_ratio: "Current ratio",
    piotroski_f_score: "Piotroski F-score",
    rs_21: "Relative strength 21d",
    rs_63: "Relative strength 63d",
    rs_126: "Relative strength 126d",
    rs_252: "Relative strength 252d",
    range52: "52-week range position",
    trend: "Trend (SMA50 / SMA200)",
    volratio: "Volume ratio ADV20/ADV90",
  };
  return labels[metric] ?? metric;
}

function SubmetricTable({ detail }: { detail: ScoreComponent }) {
  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Submetric</TableHead>
            <TableHead className="text-right">Raw</TableHead>
            <TableHead className="text-right">Nominal w</TableHead>
            <TableHead className="text-right">Effective w</TableHead>
            <TableHead>Compared against</TableHead>
            <TableHead className="text-right">Blend w</TableHead>
            <TableHead className="text-right">Percentile</TableHead>
            <TableHead className="text-right">Contribution</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {detail.submetrics.map((item: ScoreSubmetric) => (
            <TableRow key={item.metric} className={item.valid ? "" : "opacity-60"}>
              <TableCell>
                <div className="font-medium">{metricLabel(item.metric)}</div>
                {item.lower_is_better ? (
                  <div className="text-xs text-muted-foreground">
                    lower is better; percentile inverted after ranking
                  </div>
                ) : null}
                {!item.valid ? (
                  <div className="text-xs text-amber-200">
                    not valid: {item.reason ?? "no reason recorded"}
                  </div>
                ) : null}
              </TableCell>
              <TableCell className="text-right font-mono">
                {item.raw_value === null ? NUMBER_MISSING : num(item.raw_value, 4)}
              </TableCell>
              <TableCell className="text-right font-mono">
                {num(item.nominal_weight, 2)}
              </TableCell>
              <TableCell className="text-right font-mono">
                {item.valid ? num(item.effective_weight, 4) : "0"}
              </TableCell>
              <TableCell className="text-sm text-muted-foreground">
                {item.kind === "absolute" ? (
                  <span>absolute value, not ranked against anything</span>
                ) : item.comparison ? (
                  <>
                    <div>
                      market: {item.comparison.market_count} valid, pct{" "}
                      <span className="font-mono">
                        {num(item.comparison.market_percentile, 2)}
                      </span>
                    </div>
                    <div>
                      cohort {item.comparison.cohort_population ?? "not used"}:{" "}
                      {item.comparison.cohort_count} valid, pct{" "}
                      <span className="font-mono">
                        {num(item.comparison.cohort_percentile, 2)}
                      </span>
                    </div>
                  </>
                ) : null}
              </TableCell>
              <TableCell className="text-right font-mono">
                {item.comparison ? num(item.comparison.blend_weight_w, 2) : NUMBER_MISSING}
              </TableCell>
              <TableCell className="text-right font-mono">
                {num(item.value_used, 2)}
              </TableCell>
              <TableCell className="text-right font-mono">
                {num(item.contribution, 4)}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

function PiotroskiTable({ signals }: { signals: PiotroskiSignal[] }) {
  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Signal</TableHead>
            <TableHead>Test</TableHead>
            <TableHead className="text-right">Points</TableHead>
            <TableHead>Concept used</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {signals.map((signal) => (
            <TableRow key={signal.signal}>
              <TableCell className="font-medium">{signal.signal}</TableCell>
              <TableCell className="font-mono text-sm">{signal.test}</TableCell>
              <TableCell className="text-right font-mono">
                {signal.points === null ? (
                  <span className="text-amber-200">not computable</span>
                ) : (
                  signal.points
                )}
              </TableCell>
              <TableCell className="font-mono text-xs text-muted-foreground">
                {signal.concept_used ?? NUMBER_MISSING}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

function SubBonus({
  title,
  formula,
  value,
  children,
}: {
  title: string;
  formula: string;
  value: number | null | undefined;
  children?: React.ReactNode;
}) {
  return (
    <div className="rounded-lg border border-border p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="font-medium">{title}</span>
        <span className="font-mono">{num(value, 4)}</span>
      </div>
      <div className="mt-1 font-mono text-xs text-muted-foreground">{formula}</div>
      {children}
    </div>
  );
}

export function ScoreBreakdown({
  score,
  explanation,
  rankedCount,
}: {
  score: ScoreRow;
  explanation: ScoreExplanation;
  rankedCount: number;
}) {
  const rankable = score.rankable === 1;
  const components = explanation.components;
  const bonus = explanation.insider_bonus;
  const cluster = bonus?.b1_cluster as Record<string, unknown> | undefined;
  const size = bonus?.b3_size as Record<string, unknown> | undefined;

  return (
    <section className="mb-14 space-y-6">
      <h2>Composite score</h2>

      {!rankable ? (
        <div className="rounded-lg border border-amber-400/40 bg-amber-400/10 px-6 py-5">
          <div className="text-lg font-semibold text-amber-100">
            Not ranked — no composite score
          </div>
          <p className="mt-2 text-amber-100/90">{score.withhold_reason}</p>
          <p className="mt-3 text-sm text-amber-100/70">
            A withheld security stores no score at all. Zero is a real score
            meaning &ldquo;worst in the universe&rdquo;, so it is never used as a
            stand-in for &ldquo;we could not tell&rdquo;.
          </p>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-4">
          <Badge
            variant="outline"
            className="border-emerald-400/40 bg-emerald-400/15 px-3 py-1 text-lg text-emerald-200"
          >
            {num(score.composite_score, 4)} of 100
          </Badge>
          <Badge variant="outline" className="px-3 py-1 font-mono">
            rank {score.rank} of {rankedCount}
          </Badge>
          <Badge variant="outline" className="px-3 py-1 font-mono">
            {score.score_date}
          </Badge>
          <Badge variant="outline" className="px-3 py-1 font-mono">
            cohort {score.cohort_id}
          </Badge>
        </div>
      )}

      <p className="text-sm text-muted-foreground">
        {explanation.cohort_label} · {explanation.cohort_basis}
      </p>

      {rankable && explanation.composite?.terms ? (
        <div className="space-y-3">
          <h3 className="text-lg font-semibold">The arithmetic</h3>
          <p className="font-mono text-sm text-muted-foreground">
            {explanation.composite.formula}
          </p>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Term</TableHead>
                <TableHead className="text-right">Component</TableHead>
                <TableHead className="text-right">Contribution</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {explanation.composite.terms.map((term) => (
                <TableRow key={term.term}>
                  <TableCell className="font-mono">{term.term}</TableCell>
                  <TableCell className="text-right font-mono">
                    {num(term.component, 4)}
                  </TableCell>
                  <TableCell className="text-right font-mono">
                    {signed(term.contribution)}
                  </TableCell>
                </TableRow>
              ))}
              <TableRow>
                <TableCell className="font-medium">Total before clamping</TableCell>
                <TableCell />
                <TableCell className="text-right font-mono">
                  {num(explanation.composite.unclamped, 4)}
                </TableCell>
              </TableRow>
              <TableRow>
                <TableCell className="font-medium">Composite (clamped 0–100)</TableCell>
                <TableCell />
                <TableCell className="text-right font-mono font-semibold">
                  {num(explanation.composite.clamped, 4)}
                </TableCell>
              </TableRow>
            </TableBody>
          </Table>
        </div>
      ) : null}

      {components
        ? (["value", "quality", "momentum"] as const).map((name) => {
            const component = components[name];
            if (!component) return null;
            return (
              <div key={name} className="space-y-3">
                <div className="flex flex-wrap items-baseline justify-between gap-3">
                  <h3 className="text-lg font-semibold capitalize">{name}</h3>
                  <span className="font-mono">
                    {component.score === null ? (
                      <span className="text-amber-200">gate failed — no score</span>
                    ) : (
                      `${num(component.score, 4)} × weight ${num(component.weight, 2)}`
                    )}
                  </span>
                </div>
                <p className="text-sm text-muted-foreground">
                  Gate: {component.gate}
                  {component.population ? ` · Ranked against the ${component.population}.` : ""}
                </p>
                {name === "quality" && component.piotroski ? (
                  <div className="space-y-2 rounded-lg border border-border p-4">
                    <div className="flex flex-wrap items-baseline justify-between gap-2">
                      <span className="font-medium">
                        Piotroski F-score{" "}
                        {component.piotroski.f_score === null
                          ? NUMBER_MISSING
                          : `${component.piotroski.f_score} of 9`}
                      </span>
                      <span className="font-mono text-sm">
                        {component.piotroski.formula}
                      </span>
                    </div>
                    <p className="text-sm text-muted-foreground">
                      Fiscal years compared: {component.piotroski.prior_period_end ?? "—"} →{" "}
                      {component.piotroski.period_end ?? "—"}. Its 0.40 share never changes,
                      whatever else in Quality is missing.
                    </p>
                    {component.piotroski.reason ? (
                      <p className="text-sm text-amber-200">{component.piotroski.reason}</p>
                    ) : null}
                    <PiotroskiTable signals={component.piotroski.signals} />
                  </div>
                ) : null}
                <SubmetricTable detail={component.detail} />
                <p className="text-xs text-muted-foreground">
                  Effective weights over valid submetrics sum to{" "}
                  <span className="font-mono">
                    {num(component.detail.effective_weight_sum, 4)}
                  </span>
                  . Renormalisation happens only inside this component; no weight is
                  ever moved to another component.
                </p>
              </div>
            );
          })
        : null}

      {bonus ? (
        <div className="space-y-3">
          <div className="flex flex-wrap items-baseline justify-between gap-3">
            <h3 className="text-lg font-semibold">Insider bonus</h3>
            <span className="font-mono">
              {bonus.value === null ? (
                <span className="text-amber-200">unknown — ranking withheld</span>
              ) : (
                num(bonus.value, 4)
              )}
            </span>
          </div>
          <p className="text-sm text-muted-foreground">
            {bonus.formula} · Qualifying: {bonus.qualifying_definition} ·{" "}
            {bonus.qualifying_purchases} qualifying purchases on file.
          </p>
          <div
            className={
              bonus.coverage.complete
                ? "rounded-lg border border-emerald-400/30 bg-emerald-400/10 p-4"
                : "rounded-lg border border-amber-400/40 bg-amber-400/10 p-4"
            }
          >
            <div className="font-medium">
              Form 4 coverage:{" "}
              {bonus.coverage.complete ? "complete and current" : "UNKNOWN"}
            </div>
            <p className="mt-1 text-sm">{bonus.coverage.reason}</p>
            <p className="mt-2 text-xs text-muted-foreground">{bonus.coverage.note}</p>
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            <SubBonus
              title="B1 cluster"
              formula={String(cluster?.formula ?? "")}
              value={cluster?.value as number}
            >
              <p className="mt-2 text-sm text-muted-foreground">
                N = {String(cluster?.distinct_insiders_N ?? 0)} distinct insiders in the
                last {String(cluster?.window_days ?? 90)} days. One insider contributes one
                q&nbsp;value however many times they bought.
              </p>
            </SubBonus>
            <SubBonus
              title="B2 executive"
              formula={String((bonus.b2_executive as Record<string, unknown>).formula ?? "")}
              value={(bonus.b2_executive as Record<string, unknown>).value as number}
            />
            <SubBonus
              title="B3 size"
              formula={String(size?.formula ?? "")}
              value={size?.value as number}
            >
              <p className="mt-2 text-sm text-muted-foreground">
                S = {size?.S === null || size?.S === undefined ? "—" : num(size.S as number, 8)}
                , ranked against {String(size?.population_count ?? 0)} securities with S
                &gt; 0.
              </p>
            </SubBonus>
            <SubBonus
              title="B4 conviction"
              formula={String((bonus.b4_conviction as Record<string, unknown>).formula ?? "")}
              value={(bonus.b4_conviction as Record<string, unknown>).value as number}
            />
          </div>
          <p className="text-sm text-muted-foreground">
            Sum before the cap: <span className="font-mono">{num(bonus.sum_before_cap, 4)}</span>
            , capped at 10.
          </p>
        </div>
      ) : null}

      <div className="space-y-2">
        <h3 className="text-lg font-semibold">Provenance</h3>
        <div className="grid gap-2 text-sm md:grid-cols-2">
          <div>
            strategy version{" "}
            <span className="font-mono">{score.strategy_version}</span>
          </div>
          <div>
            config hash <span className="font-mono">{score.config_hash.slice(0, 16)}</span>
          </div>
          <div>
            mapping version <span className="font-mono">{score.mapping_version}</span>
          </div>
          <div>
            price dataset version{" "}
            <span className="font-mono">{score.price_dataset_version ?? NUMBER_MISSING}</span>
          </div>
          <div>
            price snapshot{" "}
            <span className="font-mono">
              {score.price_snapshot_hash?.slice(0, 16) ?? NUMBER_MISSING}
            </span>
          </div>
          <div>
            universe snapshot <span className="font-mono">{explanation.snapshot_id}</span>
          </div>
          <div>
            knowledge cutoff{" "}
            <span className="font-mono">{explanation.knowledge_cutoff ?? NUMBER_MISSING}</span>
          </div>
          <div>
            dilution penalty <span className="font-mono">{num(score.dilution_penalty, 2)}</span>
          </div>
        </div>
        {explanation.altman_z_note ? (
          <p className="text-xs text-muted-foreground">{explanation.altman_z_note}</p>
        ) : null}
        {explanation.winsorisation_note ? (
          <p className="text-xs text-muted-foreground">{explanation.winsorisation_note}</p>
        ) : null}
      </div>
    </section>
  );
}
