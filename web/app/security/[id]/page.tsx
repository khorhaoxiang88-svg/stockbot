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
import {
  adjustedSeries,
  largestSingleDayMove,
  returnAcrossDate,
} from "@/lib/adjust";
import {
  getCorporateActions,
  getCurrentDatasetVersion,
  getFacts,
  getFactsBySemanticHash,
  getFactsSummary,
  getFixtureEntryFor,
  getFundamentalPeriods,
  getKnowledgeStates,
  getLatestFundamentals,
  getListingsFor,
  getPriceRevisions,
  getPrices,
  getProvenance,
  getRestatements,
  getSecurityById,
  industryLabel,
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

      <div className="grid gap-10 md:grid-cols-2">
        <EmptySection
          title="Universe membership"
          note="No data yet. Universe snapshots arrive in a later phase."
        />
        <EmptySection
          title="Signals"
          note="No data yet. Scoring arrives in a later phase."
        />
      </div>
    </main>
  );
}
