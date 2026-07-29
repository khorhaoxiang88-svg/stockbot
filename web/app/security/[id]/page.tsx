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
import {
  getFixtureEntryFor,
  getListingsFor,
  getSecurityById,
  industryLabel,
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

      <div className="grid gap-10 md:grid-cols-2">
        <EmptySection
          title="Prices"
          note="No data yet. Price history arrives in a later phase."
        />
        <EmptySection
          title="Fundamentals"
          note="No data yet. Fundamentals arrive in a later phase."
        />
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
