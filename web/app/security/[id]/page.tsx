import Link from "next/link";
import { notFound } from "next/navigation";

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
import { PriceChart, type ChartMarker } from "@/components/price-chart";
import { RiskPanel } from "@/components/risk-panel";
import { ScoreBreakdown } from "@/components/score-breakdown";
import {
  adjustedSeries,
  largestSingleDayMove,
  returnAcrossDate,
} from "@/lib/adjust";
import {
  getCorporateActions,
  getCurrentDatasetVersion,
  getDilutionSignal,
  getFacts,
  getFactsBySemanticHash,
  getFactsSummary,
  getFixtureEntryFor,
  getFundamentalPeriods,
  getInsiderClusterSummary,
  getInsiderTableOne,
  getInsiderTableTwo,
  getKnowledgeStates,
  getLatestFundamentals,
  getListingsFor,
  getPriceRevisions,
  getPrices,
  getProvenance,
  getRankedCount,
  getRestatements,
  getRiskAsOf,
  getRiskFlags,
  getScore,
  getSecurityById,
  industryLabel,
  parseExplanation,
  type DilutionEvidence,
  PIOTROSKI_METRICS,
  SCALAR_METRICS,
} from "@/lib/db";
import { rankExclusionReason } from "@/lib/rank";
import { formatEastern } from "@/lib/time";

export const dynamic = "force-dynamic";

function ConfidenceBadge({ confidence }: { confidence: string }) {
  const tone =
    confidence === "high"
      ? "border-emerald-400/40 bg-emerald-400/15 text-emerald-200"
      : confidence === "medium"
        ? "border-amber-400/40 bg-amber-400/15 text-amber-200"
        : "border-red-400/40 bg-red-400/15 text-red-200";
  return (
    <Badge variant="outline" className={`px-3 py-1 ${tone}`}>
      {confidence} confidence
    </Badge>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-4 border-b border-border py-4 last:border-b-0">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="text-right font-medium">{children}</dd>
    </div>
  );
}

function EmptySection({ title, note }: { title: string; note: string }) {
  return (
    <section className="space-y-4">
      <h2>{title}</h2>
      <p className="rounded-lg border border-dashed border-border px-6 py-8 text-muted-foreground">
        {note}
      </p>
    </section>
  );
}

export default async function SecurityPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const securityId = Number(id);
  if (!Number.isInteger(securityId) || securityId <= 0) notFound();

  const security = getSecurityById(securityId);
  if (!security.row) notFound();

  const listings = getListingsFor(securityId);
  const fixture = getFixtureEntryFor(securityId);
  const row = security.row;
  const industry = industryLabel(row.sic_code);
  const rankReason = rankExclusionReason(
    row.security_type,
    row.classification_confidence,
  );

  const prices = getPrices(securityId);
  const actions = getCorporateActions(securityId);
  const revisions = getPriceRevisions(securityId);
  const datasetVersion = getCurrentDatasetVersion();
  const provenance = getProvenance(securityId);

  const adjusted = adjustedSeries(prices.rows, actions.rows);
  const splits = actions.rows.filter((action) => action.action_type === "split");
  const dividends = actions.rows.filter((action) => action.action_type === "dividend");

  const markers: ChartMarker[] = [
    ...splits.map((split) => ({
      date: split.ex_date,
      kind: "split" as const,
      label:
        split.ratio && split.ratio >= 1
          ? `${Number(split.ratio.toFixed(4))}-for-1 split`
          : `1-for-${split.ratio ? Number((1 / split.ratio).toFixed(4)) : "?"} reverse split`,
    })),
    ...dividends.map((dividend) => ({
      date: dividend.ex_date,
      kind: "dividend" as const,
      label: `dividend ${dividend.cash_amount}`,
    })),
  ];

  const worstRaw = largestSingleDayMove(prices.rows);
  const worstAdjusted = largestSingleDayMove(adjusted);
  const firstSplit = splits[0];
  const rawSplitReturn = firstSplit ? returnAcrossDate(prices.rows, firstSplit.ex_date) : null;
  const adjustedSplitReturn = firstSplit
    ? returnAcrossDate(adjusted, firstSplit.ex_date)
    : null;
  const percent = (value: number | null) =>
    value === null ? "—" : `${(value * 100).toFixed(2)}%`;

  const factsSummary = getFactsSummary(row.cik);
  const facts = getFacts(row.cik, undefined, 60);
  const restatements = getRestatements(row.cik, 6);
  const restatementDetail = restatements.rows.map((group) => ({
    group,
    versions: getFactsBySemanticHash(group.semantic_hash).rows,
  }));
  const acceptanceRate =
    factsSummary.row && factsSummary.row.total > 0
      ? (factsSummary.row.usable / factsSummary.row.total) * 100
      : null;
  const compactNumber = (value: number | null) => {
    if (value === null) return "—";
    const abs = Math.abs(value);
    if (abs >= 1e9) return `${(value / 1e9).toFixed(2)}B`;
    if (abs >= 1e6) return `${(value / 1e6).toFixed(2)}M`;
    if (abs >= 1e3) return value.toLocaleString(undefined, { maximumFractionDigits: 0 });
    return String(value);
  };
  const fundamentals = getLatestFundamentals(securityId);
  const fundamentalPeriods = getFundamentalPeriods(securityId);
  const latestPeriod = fundamentals.row?.period_end as string | undefined;
  const knowledgeStates = latestPeriod
    ? getKnowledgeStates(securityId, latestPeriod).rows
    : [];
  const missingFields: Record<string, string> = fundamentals.row?.missing_fields_json
    ? JSON.parse(String(fundamentals.row.missing_fields_json))
    : {};

  const METRIC_LABELS: Record<string, string> = {
    pe: "P/E",
    pb: "P/B",
    ev_ebitda: "EV/EBITDA",
    fcf_yield: "FCF yield",
    roic: "ROIC",
    interest_coverage: "Interest coverage",
    debt_ebitda: "Debt/EBITDA",
    current_ratio: "Current ratio",
    gross_margin: "Gross margin",
    revenue_growth_yoy: "Revenue growth YoY",
    shares_outstanding: "Shares outstanding",
    piotroski_roa_positive: "1. ROA positive",
    piotroski_cfo_positive: "2. CFO positive",
    piotroski_roa_improved: "3. ROA improved",
    piotroski_accruals: "4. CFO exceeds net income",
    piotroski_leverage_decreased: "5. Leverage decreased",
    piotroski_current_ratio_improved: "6. Current ratio improved",
    piotroski_no_new_shares: "7. No new shares",
    piotroski_gross_margin_improved: "8. Gross margin improved",
    piotroski_asset_turnover_improved: "9. Asset turnover improved",
  };
  const RATIO_METRICS = new Set([
    "gross_margin",
    "revenue_growth_yoy",
    "fcf_yield",
    "roic",
  ]);

  const formatMetric = (name: string, value: unknown) => {
    if (value === null || value === undefined) return null;
    const numeric = Number(value);
    if (PIOTROSKI_METRICS.includes(name as never)) {
      return numeric === 1 ? "yes (1)" : "no (0)";
    }
    if (name === "shares_outstanding") return compactNumber(numeric);
    if (RATIO_METRICS.has(name)) return (numeric * 100).toFixed(2) + "%";
    return numeric.toFixed(2);
  };

  const insiderTableOne = getInsiderTableOne(securityId, 80);
  const insiderTableTwo = getInsiderTableTwo(securityId, 40);
  const insiderCluster = getInsiderClusterSummary(securityId);

  const PLAN_STYLE: Record<string, string> = {
    confirmed_10b5_1: "text-sky-200",
    discretionary: "text-emerald-200",
    unknown: "text-amber-200",
  };
  const PLAN_LABEL: Record<string, string> = {
    confirmed_10b5_1: "10b5-1 plan",
    discretionary: "discretionary",
    unknown: "unknown",
  };
  const CODE_LABEL: Record<string, string> = {
    P: "open-market purchase",
    S: "sale",
    A: "grant",
    M: "option exercise",
    F: "tax withholding",
    G: "gift",
  };

  const riskFlags = getRiskFlags(securityId);
  const riskAsOf = getRiskAsOf(securityId);

  const score = getScore(securityId);
  const scoreExplanation = parseExplanation(score.row?.explanation_json ?? null);
  const rankedCount = score.row ? getRankedCount(score.row.score_date) : 0;

  const dilution = getDilutionSignal(securityId);
  const dilutionEvidence: DilutionEvidence[] = dilution.row?.evidence_json
    ? JSON.parse(dilution.row.evidence_json)
    : [];
  const TIER_LABEL: Record<string, string> = {
    D1: "D1 capacity",
    D2: "D2 issuance",
    D3: "D3 structural",
  };
  const OUTCOME_STYLE: Record<string, string> = {
    equity_offering: "text-amber-200",
    atm_programme: "text-amber-200",
    variable_convertible: "text-red-200",
    shelf_415: "text-sky-200",
    debt_or_structured: "text-muted-foreground",
    unknown: "text-muted-foreground",
  };

  const currentSymbol =
    listings.rows.find((listing) => listing.valid_to === null)?.symbol ??
    listings.rows[0]?.symbol ??
    "—";

  return (
    <main className="mx-auto w-full max-w-5xl px-8 py-16">
      <header className="mb-14 space-y-4">
        <p className="text-base uppercase tracking-[0.2em] text-muted-foreground">
          <Link href="/health" className="underline underline-offset-4 hover:text-foreground">
            stockbot / health
          </Link>
        </p>
        <h1>{row.name}</h1>
        <div className="flex flex-wrap items-center gap-4">
          <Badge variant="outline" className="px-3 py-1 font-mono">
            {currentSymbol}
          </Badge>
          <Badge variant="outline" className="px-3 py-1">
            {row.security_type.replace(/_/g, " ")}
          </Badge>
          <ConfidenceBadge confidence={row.classification_confidence} />
          {industry && (
            <Badge
              variant="outline"
              className="border-sky-400/40 bg-sky-400/15 px-3 py-1 text-sky-200"
            >
              {industry}
            </Badge>
          )}
          {row.is_active === 0 && (
            <Badge
              variant="outline"
              className="border-red-400/40 bg-red-400/15 px-3 py-1 text-red-200"
            >
              delisted
            </Badge>
          )}
        </div>
        <p className="text-muted-foreground">
          Internal id <span className="font-mono">{row.security_id}</span>. The symbol
          above is a label this security wears today, not its identity.
        </p>
      </header>

      <div className="mb-14 grid gap-8 md:grid-cols-2">
        <Card className="gap-6 py-8">
          <CardHeader className="px-8">
            <CardTitle className="text-2xl">Identity</CardTitle>
            <CardDescription className="text-base">
              Stable across symbol changes
            </CardDescription>
          </CardHeader>
          <CardContent className="px-8">
            <dl>
              <Field label="security_id">
                <span className="font-mono">{row.security_id}</span>
              </Field>
              <Field label="CIK">
                <span className="font-mono">{row.cik ?? "—"}</span>
              </Field>
              <Field label="Share class">{row.share_class ?? "—"}</Field>
              <Field label="SIC code">
                <span className="font-mono">{row.sic_code ?? "—"}</span>
                {industry ? ` · ${industry}` : ""}
              </Field>
              <Field label="First seen">{formatEastern(row.first_seen)}</Field>
              <Field label="Last seen">{formatEastern(row.last_seen)}</Field>
              {row.delisted_date && (
                <Field label="Delisted">{row.delisted_date}</Field>
              )}
            </dl>
          </CardContent>
        </Card>

        <Card className="gap-6 py-8">
          <CardHeader className="px-8">
            <CardTitle className="text-2xl">Classification</CardTitle>
            <CardDescription className="text-base">
              Every decision records its evidence
            </CardDescription>
          </CardHeader>
          <CardContent className="px-8">
            <dl>
              <Field label="Security type">{row.security_type.replace(/_/g, " ")}</Field>
              <Field label="Confidence">{row.classification_confidence}</Field>
              <Field label="Source">
                <span className="font-mono break-all">{row.classification_source}</span>
              </Field>
              <Field label="Rankable">
                {rankReason === null ? (
                  "yes"
                ) : (
                  <span className="text-amber-200">no — {rankReason}</span>
                )}
              </Field>
              {fixture.row && (
                <>
                  <Field label="Fixture category">{fixture.row.category}</Field>
                  <Field label="Manifest version">
                    <span className="font-mono">{fixture.row.manifest_version}</span>
                  </Field>
                </>
              )}
            </dl>
            {fixture.row && (
              <p className="mt-6 text-base text-muted-foreground">
                {fixture.row.inclusion_reason}
              </p>
            )}
          </CardContent>
        </Card>
      </div>

      <section className="mb-14 space-y-6">
        <h2>Listing history</h2>
        {listings.rows.length === 0 ? (
          <p className="rounded-lg border border-dashed border-border px-6 py-8 text-muted-foreground">
            No listing rows for this security.
          </p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Symbol</TableHead>
                <TableHead>Exchange</TableHead>
                <TableHead>Valid from</TableHead>
                <TableHead>Valid to</TableHead>
                <TableHead>Primary</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {listings.rows.map((listing) => (
                <TableRow key={`${listing.symbol}-${listing.valid_from}`}>
                  <TableCell className="font-mono">{listing.symbol}</TableCell>
                  <TableCell>{listing.exchange}</TableCell>
                  <TableCell>{listing.valid_from}</TableCell>
                  <TableCell>{listing.valid_to ?? "current"}</TableCell>
                  <TableCell>{listing.is_primary ? "yes" : "no"}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </section>

      <section className="mb-14 space-y-8">
        <h2>Prices</h2>
        {prices.rows.length === 0 ? (
          <p className="rounded-lg border border-dashed border-border px-6 py-8 text-muted-foreground">
            No data yet. The price provider returned nothing for this symbol —
            expected for a delisted security.
          </p>
        ) : (
          <>
            <PriceChart
              points={prices.rows}
              markers={markers}
              title="Raw traded price"
              subtitle={`${prices.rows.length} daily closes, exactly as traded. Splits are visible as cliffs, because no adjustment has been applied.`}
              accent="#7dd3fc"
            />
            <PriceChart
              points={adjusted}
              markers={markers}
              title="Split-adjusted price"
              subtitle="Computed at read time from the corporate actions ledger. Nothing stored is adjusted."
              accent="#c4b5fd"
            />

            {firstSplit && (
              <div className="rounded-xl border border-border bg-card p-6">
                <p className="mb-3 font-medium">
                  Split continuity check — {firstSplit.ex_date}
                </p>
                <dl className="grid gap-3 md:grid-cols-2">
                  <div className="flex items-baseline justify-between gap-4">
                    <dt className="text-muted-foreground">Raw return across ex-date</dt>
                    <dd className="font-mono text-amber-200">{percent(rawSplitReturn)}</dd>
                  </div>
                  <div className="flex items-baseline justify-between gap-4">
                    <dt className="text-muted-foreground">Adjusted return across ex-date</dt>
                    <dd className="font-mono text-emerald-200">
                      {percent(adjustedSplitReturn)}
                    </dd>
                  </div>
                  <div className="flex items-baseline justify-between gap-4">
                    <dt className="text-muted-foreground">Largest raw 1-day move</dt>
                    <dd className="font-mono">
                      {percent(worstRaw.move)} on {worstRaw.date ?? "—"}
                    </dd>
                  </div>
                  <div className="flex items-baseline justify-between gap-4">
                    <dt className="text-muted-foreground">Largest adjusted 1-day move</dt>
                    <dd className="font-mono">
                      {percent(worstAdjusted.move)} on {worstAdjusted.date ?? "—"}
                    </dd>
                  </div>
                </dl>
              </div>
            )}
          </>
        )}
      </section>

      <div className="mb-14 grid gap-8 md:grid-cols-2">
        <Card className="gap-6 py-8">
          <CardHeader className="px-8">
            <CardTitle className="text-2xl">Price dataset</CardTitle>
            <CardDescription className="text-base">
              Version applies to the whole dataset, not just this security
            </CardDescription>
          </CardHeader>
          <CardContent className="px-8">
            <dl>
              <Field label="Current version">
                <span className="font-mono">
                  {datasetVersion.row ? `v${datasetVersion.row.dataset_version}` : "—"}
                </span>
              </Field>
              <Field label="Created">
                {datasetVersion.row ? formatEastern(datasetVersion.row.created_at) : "—"}
              </Field>
              <Field label="Provider">{datasetVersion.row?.provider ?? "—"}</Field>
              <Field label="Reason">
                <span className="text-base">{datasetVersion.row?.reason ?? "—"}</span>
              </Field>
              <Field label="Bars stored">{prices.rows.length}</Field>
              {provenance.rows.map((entry) => (
                <Field key={entry.valid_from} label="Series provenance">
                  {entry.provider} from {formatEastern(entry.valid_from)}
                  {entry.valid_to ? ` to ${formatEastern(entry.valid_to)}` : " (current)"}
                </Field>
              ))}
            </dl>
          </CardContent>
        </Card>

        <Card className="gap-6 py-8">
          <CardHeader className="px-8">
            <CardTitle className="text-2xl">Revisions</CardTitle>
            <CardDescription className="text-base">
              Vendor corrections affecting this security
            </CardDescription>
          </CardHeader>
          <CardContent className="px-8">
            {revisions.rows.length === 0 ? (
              <p className="text-muted-foreground">
                None. No bar for this security has been corrected since first ingest.
              </p>
            ) : (
              <ul className="space-y-5">
                {revisions.rows.slice(0, 8).map((revision) => (
                  <li
                    key={`${revision.date}-${revision.revision}`}
                    className="border-b border-border pb-4 last:border-b-0"
                  >
                    <p className="font-medium">
                      {revision.date}{" "}
                      <span className="text-base text-muted-foreground">
                        revision {revision.revision} · v
                        {revision.price_data_version_before} → v
                        {revision.price_data_version_after}
                      </span>
                    </p>
                    <p className="font-mono text-base">
                      close {revision.old_close ?? "—"} → {revision.new_close ?? "—"}
                    </p>
                    <p className="font-mono text-base text-muted-foreground">
                      volume {revision.old_volume ?? "—"} → {revision.new_volume ?? "—"}
                    </p>
                    <p className="text-base text-muted-foreground">
                      detected {formatEastern(revision.detected_at)}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>

      <section className="mb-14 space-y-6">
        <h2>Corporate actions</h2>
        {actions.rows.length === 0 ? (
          <p className="rounded-lg border border-dashed border-border px-6 py-8 text-muted-foreground">
            No data yet. No splits or dividends recorded in the window.
          </p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Ex-date</TableHead>
                <TableHead>Type</TableHead>
                <TableHead className="text-right">Ratio</TableHead>
                <TableHead className="text-right">Cash</TableHead>
                <TableHead>Provider</TableHead>
                <TableHead>Review</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {[...actions.rows].reverse().slice(0, 20).map((action) => (
                <TableRow key={`${action.ex_date}-${action.action_type}`}>
                  <TableCell className="font-mono">{action.ex_date}</TableCell>
                  <TableCell
                    className={action.action_type === "split" ? "text-amber-200" : undefined}
                  >
                    {action.action_type}
                  </TableCell>
                  <TableCell className="text-right font-mono">
                    {action.ratio ?? "—"}
                  </TableCell>
                  <TableCell className="text-right font-mono">
                    {action.cash_amount ?? "—"}
                  </TableCell>
                  <TableCell>{action.provider}</TableCell>
                  <TableCell>
                    {action.requires_manual_review ? (
                      <span className="text-amber-200">needs review</span>
                    ) : (
                      "—"
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </section>

      <section className="mb-14 space-y-6">
        <h2>Accounting facts</h2>
        {!factsSummary.row || factsSummary.row.total === 0 ? (
          <p className="rounded-lg border border-dashed border-border px-6 py-8 text-muted-foreground">
            No data yet. No SEC XBRL facts stored for this CIK — expected for a
            security with no SEC registrant, such as a SPAC warrant.
          </p>
        ) : (
          <>
            <div className="grid gap-8 md:grid-cols-2">
              <Card className="gap-6 py-8">
                <CardHeader className="px-8">
                  <CardTitle className="text-2xl">Fact coverage</CardTitle>
                  <CardDescription className="text-base">
                    Source endpoint: companyfacts
                  </CardDescription>
                </CardHeader>
                <CardContent className="px-8">
                  <dl>
                    <Field label="Facts stored">
                      {factsSummary.row.total.toLocaleString()}
                    </Field>
                    <Field label="Distinct concepts">
                      {factsSummary.row.concepts.toLocaleString()}
                    </Field>
                    <Field label="Distinct accessions">
                      {factsSummary.row.accessions.toLocaleString()}
                    </Field>
                    <Field label="Period range">
                      {factsSummary.row.earliest ?? "—"} to {factsSummary.row.latest ?? "—"}
                    </Field>
                    <Field label="Usable (accepted_at set)">
                      <span
                        className={
                          acceptanceRate !== null && acceptanceRate >= 95
                            ? "text-emerald-200"
                            : "text-amber-200"
                        }
                      >
                        {factsSummary.row.usable.toLocaleString()}
                        {acceptanceRate !== null && ` (${acceptanceRate.toFixed(2)}%)`}
                      </span>
                    </Field>
                    <Field label="Unusable (no accepted_at)">
                      <span
                        className={
                          factsSummary.row.unusable > 0 ? "text-amber-200" : undefined
                        }
                      >
                        {factsSummary.row.unusable.toLocaleString()}
                      </span>
                    </Field>
                  </dl>
                </CardContent>
              </Card>

              <Card className="gap-6 py-8">
                <CardHeader className="px-8">
                  <CardTitle className="text-2xl">Known limitation</CardTitle>
                  <CardDescription className="text-base">
                    What this endpoint cannot tell us
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4 px-8">
                  <p className="text-muted-foreground">
                    SEC Company Facts returns consolidated facts only. It does not
                    return the XBRL <span className="font-mono">decimals</span>{" "}
                    attribute, nil flags, or dimensional members, so those columns
                    are stored as NULL rather than guessed.
                  </p>
                  <p className="text-muted-foreground">
                    Recovering them means parsing instance documents, which Release 1
                    deliberately does not do.
                  </p>
                </CardContent>
              </Card>
            </div>

            {restatementDetail.length > 0 && (
              <div className="space-y-5 rounded-xl border border-amber-400/30 bg-amber-400/5 p-6">
                <div>
                  <h3 className="text-amber-100">Restated facts</h3>
                  <p className="text-base text-muted-foreground">
                    Same concept and period, reported differently in different
                    filings. Every version is kept as its own row — nothing was
                    overwritten.
                  </p>
                </div>
                {restatementDetail.map(({ group, versions }) => (
                  <div key={group.semantic_hash} className="space-y-2">
                    <p className="font-medium">
                      {group.concept}{" "}
                      <span className="text-base text-muted-foreground">
                        [{group.unit}] {group.period_start ?? ""}
                        {group.period_start ? " to " : ""}
                        {group.period_end} · {group.n_accessions} filings,{" "}
                        {group.n_values} distinct values
                      </span>
                    </p>
                    <div className="overflow-x-auto">
                      <table className="w-full">
                        <tbody>
                          {versions.map((version) => (
                            <tr key={version.fact_id} className="border-b border-border/50">
                              <td className="py-2 pr-6 text-right font-mono">
                                {compactNumber(version.normalized_numeric_value)}
                              </td>
                              <td className="py-2 pr-6 font-mono text-base">
                                {version.accession_no}
                              </td>
                              <td className="py-2 pr-6 text-base">{version.form_type}</td>
                              <td className="py-2 pr-6 text-base text-muted-foreground">
                                filed {version.filed_date}
                              </td>
                              <td className="py-2 text-base text-muted-foreground">
                                accepted {version.accepted_at ?? "unresolved"}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                ))}
              </div>
            )}

            <div>
              <h3 className="mb-3">Raw facts</h3>
              <p className="mb-4 text-base text-muted-foreground">
                Most recent {facts.rows.length} of{" "}
                {factsSummary.row.total.toLocaleString()}, newest period first.
              </p>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Concept</TableHead>
                    <TableHead>Period</TableHead>
                    <TableHead className="text-right">Value</TableHead>
                    <TableHead>Form</TableHead>
                    <TableHead>Filed</TableHead>
                    <TableHead>Accepted at (UTC)</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {facts.rows.map((fact) => (
                    <TableRow key={fact.fact_id}>
                      <TableCell>
                        {fact.concept}
                        <span className="ml-2 text-base text-muted-foreground">
                          {fact.unit}
                        </span>
                      </TableCell>
                      <TableCell className="font-mono text-base">
                        {fact.period_start ? `${fact.period_start} → ` : ""}
                        {fact.period_end}
                      </TableCell>
                      <TableCell className="text-right font-mono">
                        {compactNumber(fact.normalized_numeric_value)}
                      </TableCell>
                      <TableCell className="text-base">{fact.form_type}</TableCell>
                      <TableCell className="font-mono text-base">{fact.filed_date}</TableCell>
                      <TableCell className="font-mono text-base">
                        {fact.accepted_at ? (
                          fact.accepted_at
                        ) : (
                          <span className="text-amber-200">
                            unresolved — unusable for official candidates
                          </span>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </>
        )}
      </section>

      <section className="mb-14 space-y-6">
        <h2>Fundamentals</h2>
        {!fundamentals.row ? (
          <p className="rounded-lg border border-dashed border-border px-6 py-8 text-muted-foreground">
            No data yet. No derived fundamentals for this security, which is
            expected when there are no SEC facts or no annual period.
          </p>
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-4">
              <Badge variant="outline" className="px-3 py-1 font-mono">
                period {String(fundamentals.row.period_end)}
              </Badge>
              <Badge variant="outline" className="px-3 py-1 font-mono">
                knew {String(fundamentals.row.knowledge_date)}
              </Badge>
              <Badge variant="outline" className="px-3 py-1">
                mapping v{String(fundamentals.row.mapping_version)}
              </Badge>
              {Number(fundamentals.row.model_applicable) === 0 ? (
                <Badge
                  variant="outline"
                  className="border-amber-400/40 bg-amber-400/15 px-3 py-1 text-amber-200"
                >
                  model not applicable, never ranked
                </Badge>
              ) : (
                <Badge
                  variant="outline"
                  className="border-emerald-400/40 bg-emerald-400/15 px-3 py-1 text-emerald-200"
                >
                  model applicable
                </Badge>
              )}
            </div>

            {fundamentals.row.market_cap === null ? (
              <p className="text-muted-foreground">
                Market cap: <span className="text-amber-200">not available</span>
              </p>
            ) : (
              <div className="rounded-xl border border-border bg-card p-6">
                <p className="font-medium">
                  Market cap {compactNumber(Number(fundamentals.row.market_cap))}{" "}
                  <span
                    className={
                      fundamentals.row.market_cap_confidence === "high"
                        ? "text-base text-emerald-200"
                        : fundamentals.row.market_cap_confidence === "medium"
                          ? "text-base text-amber-200"
                          : "text-base text-red-200"
                    }
                  >
                    ({String(fundamentals.row.market_cap_confidence)} confidence)
                  </span>
                </p>
                <p className="text-base text-muted-foreground">
                  {compactNumber(Number(fundamentals.row.market_cap_shares_used))} shares from{" "}
                  <span className="font-mono">
                    {String(fundamentals.row.market_cap_concept_used)}
                  </span>{" "}
                  at {String(fundamentals.row.market_cap_price_used)} close on{" "}
                  {String(fundamentals.row.market_cap_price_date)}
                </p>
                {fundamentals.row.market_cap_ambiguity_reason ? (
                  <p className="mt-2 text-base text-amber-200">
                    {String(fundamentals.row.market_cap_ambiguity_reason)}
                  </p>
                ) : null}
              </div>
            )}

            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Metric</TableHead>
                  <TableHead className="text-right">Value</TableHead>
                  <TableHead>Concept tag it came from</TableHead>
                  <TableHead>Source accession</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {[...SCALAR_METRICS, ...PIOTROSKI_METRICS].map((name) => {
                  const shown = formatMetric(name, fundamentals.row?.[name]);
                  const concept = fundamentals.row?.[name + "_concept_used"];
                  const accession = fundamentals.row?.[name + "_accession"];
                  return (
                    <TableRow key={name}>
                      <TableCell>{METRIC_LABELS[name] ?? name}</TableCell>
                      <TableCell className="text-right font-mono">
                        {shown === null ? (
                          <span className="text-amber-200">not available</span>
                        ) : (
                          shown
                        )}
                      </TableCell>
                      <TableCell className="font-mono text-base">
                        {concept ? (
                          String(concept)
                        ) : (
                          <span className="text-muted-foreground">
                            {missingFields[name] ?? "not available"}
                          </span>
                        )}
                      </TableCell>
                      <TableCell className="font-mono text-base">
                        {accession ? String(accession) : "not available"}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>

            {knowledgeStates.length > 1 ? (
              <div className="space-y-3 rounded-xl border border-border bg-card p-6">
                <p className="font-medium">
                  {knowledgeStates.length} knowledge states for period{" "}
                  {String(fundamentals.row.period_end)}
                </p>
                <p className="text-base text-muted-foreground">
                  An amendment adds a row rather than overwriting one, so an earlier
                  view of this period is still answerable.
                </p>
                <div className="overflow-x-auto">
                  <table className="w-full">
                    <tbody>
                      {knowledgeStates.map((state) => (
                        <tr
                          key={String(state.knowledge_date)}
                          className="border-b border-border/50"
                        >
                          <td className="py-2 pr-6 font-mono text-base">
                            {String(state.knowledge_date)}
                          </td>
                          <td className="py-2 pr-6 text-base">
                            gross margin{" "}
                            {state.gross_margin === null
                              ? "not available"
                              : (Number(state.gross_margin) * 100).toFixed(2) + "%"}
                          </td>
                          <td className="py-2 pr-6 text-base">
                            debt/EBITDA{" "}
                            {state.debt_ebitda === null
                              ? "not available"
                              : Number(state.debt_ebitda).toFixed(4)}
                          </td>
                          <td className="py-2 font-mono text-base text-muted-foreground">
                            {String(state.fact_set_hash).slice(0, 10)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            ) : null}

            {fundamentalPeriods.rows.length > 0 ? (
              <p className="text-base text-muted-foreground">
                Periods stored:{" "}
                {fundamentalPeriods.rows
                  .map((entry) => entry.period_end + " (" + entry.states + " states)")
                  .join(", ")}
              </p>
            ) : null}
          </>
        )}
      </section>

      <section className="mb-14 space-y-6">
        <h2>Insider transactions</h2>
        {insiderTableOne.rows.length === 0 ? (
          <p className="rounded-lg border border-dashed border-border px-6 py-8 text-muted-foreground">
            No data yet. No Form 4 filings ingested for this security.
          </p>
        ) : (
          <>
            {insiderCluster.row && insiderCluster.row.purchases > 0 ? (
              <div className="rounded-xl border border-emerald-400/30 bg-emerald-400/5 p-6">
                <p className="font-medium text-emerald-100">
                  {insiderCluster.row.purchasers} distinct insiders made{" "}
                  {insiderCluster.row.purchases} open-market purchases
                </p>
                <p className="text-base text-muted-foreground">
                  {insiderCluster.row.first_date} to {insiderCluster.row.last_date}
                  {insiderCluster.row.total_value
                    ? ", " + compactNumber(Number(insiderCluster.row.total_value)) + " total"
                    : ""}
                  . Only Table I code P counts; grants and exercises are excluded.
                </p>
              </div>
            ) : null}

            <div className="flex flex-wrap items-center gap-5 text-base text-muted-foreground">
              <span>Plan status:</span>
              <span className="text-emerald-200">discretionary</span>
              <span className="text-sky-200">10b5-1 plan</span>
              <span className="text-amber-200">unknown (not determinable)</span>
              <span className="ml-4">Rows struck through are superseded by an amendment.</span>
            </div>

            <div>
              <h3 className="mb-3">Table I — non-derivative (the only table scored)</h3>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Date</TableHead>
                    <TableHead>Insider</TableHead>
                    <TableHead>Code</TableHead>
                    <TableHead className="text-right">Shares</TableHead>
                    <TableHead className="text-right">Price</TableHead>
                    <TableHead className="text-right">Value</TableHead>
                    <TableHead>Plan</TableHead>
                    <TableHead>Filing</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {insiderTableOne.rows.map((tx) => {
                    const superseded = tx.superseded_by_accession !== null;
                    const isPurchase = tx.transaction_code === "P";
                    return (
                      <TableRow
                        key={tx.accession_no + "-" + tx.line_no}
                        className={superseded ? "opacity-45" : undefined}
                      >
                        <TableCell className="font-mono text-base">
                          {tx.transaction_date ?? "not available"}
                        </TableCell>
                        <TableCell>
                          {tx.insider_name}
                          {tx.officer_title ? (
                            <span className="ml-2 text-base text-muted-foreground">
                              {tx.officer_title}
                            </span>
                          ) : null}
                        </TableCell>
                        <TableCell>
                          <span
                            className={
                              isPurchase
                                ? "rounded bg-emerald-400/20 px-2 py-1 font-mono text-emerald-100"
                                : "font-mono text-muted-foreground"
                            }
                          >
                            {tx.transaction_code}
                          </span>
                          <span className="ml-2 text-base text-muted-foreground">
                            {CODE_LABEL[tx.transaction_code ?? ""] ?? ""}
                          </span>
                        </TableCell>
                        <TableCell className="text-right font-mono">
                          {tx.shares === null ? "not available" : compactNumber(tx.shares)}
                        </TableCell>
                        <TableCell className="text-right font-mono">
                          {tx.price_per_share === null
                            ? "not available"
                            : tx.price_per_share.toFixed(2)}
                        </TableCell>
                        <TableCell className="text-right font-mono">
                          {tx.total_value === null
                            ? "not available"
                            : compactNumber(tx.total_value)}
                        </TableCell>
                        <TableCell className={PLAN_STYLE[tx.plan_status]}>
                          {PLAN_LABEL[tx.plan_status]}
                          <span className="ml-1 text-base text-muted-foreground">
                            ({tx.plan_status_source})
                          </span>
                        </TableCell>
                        <TableCell className="font-mono text-base">
                          {superseded ? (
                            <span className="text-amber-200">
                              superseded by {tx.superseded_by_accession}
                            </span>
                          ) : tx.is_amendment ? (
                            <span>
                              {tx.accession_no}{" "}
                              <span className="text-sky-200">
                                (amends {tx.amends_accession})
                              </span>
                            </span>
                          ) : (
                            tx.accession_no
                          )}
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>

            {insiderTableTwo.rows.length > 0 ? (
              <details className="rounded-xl border border-border bg-card p-6">
                <summary className="cursor-pointer font-medium">
                  Table II — derivative ({insiderTableTwo.rows.length} rows).{" "}
                  <span className="text-amber-200">Not scored.</span>
                </summary>
                <p className="mt-3 text-base text-muted-foreground">
                  Options, warrants and other derivatives. Stored for completeness and
                  deliberately excluded from every scored set, including code P.
                </p>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Date</TableHead>
                      <TableHead>Insider</TableHead>
                      <TableHead>Code</TableHead>
                      <TableHead className="text-right">Shares</TableHead>
                      <TableHead className="text-right">Price</TableHead>
                      <TableHead>Filing</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {insiderTableTwo.rows.map((tx) => (
                      <TableRow
                        key={tx.accession_no + "-" + tx.line_no}
                        className={
                          tx.superseded_by_accession !== null ? "opacity-45" : undefined
                        }
                      >
                        <TableCell className="font-mono text-base">
                          {tx.transaction_date ?? "not available"}
                        </TableCell>
                        <TableCell>{tx.insider_name}</TableCell>
                        <TableCell className="font-mono text-muted-foreground">
                          {tx.transaction_code}
                        </TableCell>
                        <TableCell className="text-right font-mono">
                          {tx.shares === null ? "not available" : compactNumber(tx.shares)}
                        </TableCell>
                        <TableCell className="text-right font-mono">
                          {tx.price_per_share === null
                            ? "not available"
                            : tx.price_per_share.toFixed(2)}
                        </TableCell>
                        <TableCell className="font-mono text-base">{tx.accession_no}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </details>
            ) : null}
          </>
        )}
      </section>

      <section className="mb-14 space-y-6">
        <h2>Dilution</h2>
        {!dilution.row ? (
          <p className="rounded-lg border border-dashed border-border px-6 py-8 text-muted-foreground">
            No data yet. No dilution signal computed for this security.
          </p>
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-4">
              <Badge
                variant="outline"
                className={
                  dilution.row.is_disqualified
                    ? "border-red-400/40 bg-red-400/15 px-3 py-1 text-red-200"
                    : "border-emerald-400/40 bg-emerald-400/15 px-3 py-1 text-emerald-200"
                }
              >
                score {dilution.row.dilution_score.toFixed(1)} of 30
                {dilution.row.is_disqualified ? " — DISQUALIFIED" : ""}
              </Badge>
              <Badge variant="outline" className="px-3 py-1 font-mono">
                as of {dilution.row.as_of_date}
              </Badge>
              <span className="text-muted-foreground">
                share growth YoY (split-adjusted):{" "}
                {dilution.row.shares_yoy_growth === null ? (
                  <span className="text-amber-200">not available</span>
                ) : (
                  <span className="font-mono">
                    {(dilution.row.shares_yoy_growth * 100).toFixed(1)}%
                  </span>
                )}
              </span>
            </div>

            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Tier</TableHead>
                  <TableHead className="text-right">Points</TableHead>
                  <TableHead>Basis</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                <TableRow>
                  <TableCell>D1 capacity</TableCell>
                  <TableCell className="text-right font-mono">
                    {dilution.row.d1_capacity.toFixed(0)} / 4
                  </TableCell>
                  <TableCell className="text-base text-muted-foreground">
                    unexpired qualifying shelf on file
                  </TableCell>
                </TableRow>
                <TableRow>
                  <TableCell>D2 issuance</TableCell>
                  <TableCell className="text-right font-mono">
                    {dilution.row.d2_issuance.toFixed(0)} / 10
                  </TableCell>
                  <TableCell className="text-base text-muted-foreground">
                    qualifying 424B2/424B5 takedowns in trailing 12 months
                  </TableCell>
                </TableRow>
                <TableRow>
                  <TableCell>D3 structural</TableCell>
                  <TableCell className="text-right font-mono">
                    {dilution.row.d3_structural.toFixed(0)} / 8
                  </TableCell>
                  <TableCell className="text-base text-muted-foreground">
                    ATM programme (4) or variable convertible (8), maximum not sum
                  </TableCell>
                </TableRow>
                <TableRow>
                  <TableCell>D4 realised</TableCell>
                  <TableCell className="text-right font-mono">
                    {dilution.row.d4_realised.toFixed(1)} / 12
                  </TableCell>
                  <TableCell className="text-base text-muted-foreground">
                    split-adjusted share growth above the 5% floor
                  </TableCell>
                </TableRow>
              </TableBody>
            </Table>

            <div>
              <h3 className="mb-3">Filings considered</h3>
              <p className="mb-3 text-base text-muted-foreground">
                Unknown means the filing could not be established as common-equity
                related. It scores zero and is not treated as risk.
              </p>
              {dilutionEvidence.length === 0 ? (
                <p className="rounded-lg border border-dashed border-border px-6 py-6 text-muted-foreground">
                  No candidate filings in the window.
                </p>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Form</TableHead>
                      <TableHead>Filed</TableHead>
                      <TableHead>Classification</TableHead>
                      <TableHead>Awards</TableHead>
                      <TableHead>Reason</TableHead>
                      <TableHead>Filing</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {dilutionEvidence.slice(0, 40).map((item) => (
                      <TableRow key={item.accession}>
                        <TableCell className="font-mono text-base">{item.form}</TableCell>
                        <TableCell className="font-mono text-base">
                          {item.filed_date}
                        </TableCell>
                        <TableCell className={OUTCOME_STYLE[item.outcome] ?? ""}>
                          {item.outcome.replace(/_/g, " ")}
                        </TableCell>
                        <TableCell className="text-base">
                          {item.scores && item.tier ? (
                            <span className="text-amber-200">
                              {TIER_LABEL[item.tier] ?? item.tier}
                            </span>
                          ) : (
                            <span className="text-muted-foreground">no points</span>
                          )}
                        </TableCell>
                        <TableCell className="text-base text-muted-foreground">
                          {item.reason}
                        </TableCell>
                        <TableCell className="text-base">
                          {item.url ? (
                            <a
                              href={item.url}
                              className="font-mono underline underline-offset-4"
                              target="_blank"
                              rel="noreferrer"
                            >
                              {item.accession}
                            </a>
                          ) : (
                            <span className="font-mono">{item.accession}</span>
                          )}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </div>

            {dilution.row.classification_notes ? (
              <p className="text-base text-muted-foreground">
                {dilution.row.classification_notes.split(" | ")[0]}
              </p>
            ) : null}
          </>
        )}
      </section>

      {score.row && scoreExplanation ? (
        <ScoreBreakdown
          score={score.row}
          explanation={scoreExplanation}
          rankedCount={rankedCount}
        />
      ) : (
        <EmptySection
          title="Composite score"
          note="No data yet. Run pipeline/scoring/compute.py to score this security."
        />
      )}

      <RiskPanel flags={riskFlags.rows} asOfDate={riskAsOf} />

      <section className="mb-14 space-y-4">
        <h2>Universe membership</h2>
        {scoreExplanation ? (
          <dl className="rounded-lg border border-border px-6">
            <Field label="Official snapshot">
              <span className="font-mono">{scoreExplanation.snapshot_id}</span>
            </Field>
            <Field label="Status">
              {score.row?.rankable === 1 ? "included and ranked" : "not ranked"}
            </Field>
            <Field label="Cohort">
              <span className="font-mono">{scoreExplanation.cohort_id}</span>{" "}
              {scoreExplanation.cohort_label}
            </Field>
          </dl>
        ) : (
          <p className="rounded-lg border border-dashed border-border px-6 py-8 text-muted-foreground">
            No data yet. No official universe snapshot covers this security.
          </p>
        )}
      </section>
    </main>
  );
}
