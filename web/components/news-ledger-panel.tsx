import { Badge } from "@/components/ui/badge";
import { NEWS_EVENT_TYPE_LABELS, type NewsEvent, type NewsFiling } from "@/lib/db";

/**
 * News Ledger, Stage A (R2-NEWS-1.0) -- shadow mode.
 *
 * The banner is not decoration: this panel exists specifically to prove the
 * extraction is accurate before anything trusts it (spec section 1), so
 * every render says, unambiguously, that nothing here has touched a score.
 *
 * Four sections, same "detected / could not determine" split RiskPanel uses,
 * because the honesty requirement is the same: a filing with no qualifying
 * event is a checked-and-clean result, not a skipped one.
 *
 *   BINDING           confirmation_tier = 'binding'
 *   NON-BINDING/RUMOR  confirmation_tier in ('non_binding_loi', 'rumor') -- display only, spec section 4
 *   COULD NOT CLASSIFY  is_abstain = 1
 *
 * Nothing in this file links to /candidates, a composite score, or any
 * selection concept -- Stage B does not exist yet.
 */

const TIER_TONE: Record<string, string> = {
  binding: "border-emerald-400/40 bg-emerald-400/15 text-emerald-200",
  non_binding_loi: "border-sky-400/40 bg-sky-400/15 text-sky-200",
  rumor: "border-slate-400/40 bg-slate-400/15 text-slate-200",
  abstain: "border-amber-400/40 bg-amber-400/15 text-amber-200",
};

function EdgarLink({ event }: { event: NewsEvent }) {
  if (!event.primary_doc_url) {
    return (
      <span className="text-xs text-muted-foreground">
        Source: accession <span className="font-mono">{event.accession_no}</span>
      </span>
    );
  }
  return (
    <a
      href={event.primary_doc_url}
      target="_blank"
      rel="noreferrer"
      className="text-xs underline underline-offset-4"
    >
      Source: {event.accession_no}
      {event.filed_date ? ` filed ${event.filed_date}` : ""}
    </a>
  );
}

function EventCard({ event }: { event: NewsEvent }) {
  const tone = event.is_abstain ? TIER_TONE.abstain : TIER_TONE[event.confirmation_tier ?? ""] ?? "";
  return (
    <li className={`rounded-lg border p-4 ${tone}`}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="font-medium">
          {event.is_abstain
            ? "Could not classify"
            : NEWS_EVENT_TYPE_LABELS[event.event_type_candidate ?? ""] ?? event.event_type_candidate}
        </span>
        <Badge variant="outline" className="px-2 py-0.5 font-mono text-xs">
          {event.is_abstain ? "abstain" : event.confirmation_tier}
        </Badge>
      </div>

      {event.is_abstain ? (
        <p className="mt-2 text-sm leading-relaxed">{event.abstain_reason}</p>
      ) : (
        <>
          <p className="mt-2 text-sm leading-relaxed italic">&ldquo;{event.supporting_passage}&rdquo;</p>
          <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-muted-foreground sm:grid-cols-4">
            <div>
              <dt className="uppercase tracking-wide">Amount</dt>
              <dd className="font-mono text-foreground">
                {event.amount_explicit && event.amount_stated !== null
                  ? `${event.currency ?? ""} ${event.amount_stated.toLocaleString()}${
                      event.amount_type ? ` (${event.amount_type})` : ""
                    }`.trim()
                  : "not stated"}
              </dd>
            </div>
            <div>
              <dt className="uppercase tracking-wide">Duration</dt>
              <dd className="font-mono text-foreground">
                {event.contract_duration_months !== null
                  ? `${event.contract_duration_months} mo`
                  : "not stated"}
              </dd>
            </div>
            <div>
              <dt className="uppercase tracking-wide">Document</dt>
              <dd className="font-mono text-foreground">{event.source_document}</dd>
            </div>
            <div>
              <dt className="uppercase tracking-wide">Model</dt>
              <dd className="font-mono text-foreground">{event.extraction_model_version}</dd>
            </div>
          </dl>
        </>
      )}

      <div className="mt-2">
        <EdgarLink event={event} />
      </div>
    </li>
  );
}

export function NewsLedgerPanel({
  filings,
  events,
}: {
  filings: NewsFiling[];
  events: NewsEvent[];
}) {
  const binding = events.filter((e) => !e.is_abstain && e.confirmation_tier === "binding");
  const displayOnly = events.filter(
    (e) => !e.is_abstain && e.confirmation_tier !== "binding",
  );
  const abstained = events.filter((e) => e.is_abstain);

  return (
    <section className="mb-14 space-y-6">
      <h2>News ledger</h2>

      <div className="rounded-lg border border-amber-400/40 bg-amber-950/40 px-4 py-2 text-sm text-amber-100">
        Stage A -- shadow mode. Extraction and display only. Zero influence on any
        score, candidate or selection.
      </div>

      {filings.length === 0 ? (
        <p className="rounded-lg border border-dashed border-border px-6 py-8 text-muted-foreground">
          No 8-K/8-K-A filings ingested yet. Run pipeline/news/ingest.py for this security.
        </p>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-4">
            <Badge variant="outline" className="px-3 py-1 font-mono">
              {filings.length} filing{filings.length === 1 ? "" : "s"} ingested
            </Badge>
            <Badge variant="outline" className={`px-3 py-1 ${TIER_TONE.binding}`}>
              {binding.length} binding
            </Badge>
            <Badge variant="outline" className="px-3 py-1">
              {displayOnly.length} non-binding / rumor (display only)
            </Badge>
            <Badge variant="outline" className={`px-3 py-1 ${TIER_TONE.abstain}`}>
              {abstained.length} could not classify
            </Badge>
          </div>

          <p className="text-sm text-muted-foreground">
            AI-assisted structured extraction over 8-K text, not neutral fact
            retrieval -- every classification links to the literal passage it was
            drawn from and the accession it came from. An amount is shown only
            when the filing explicitly stated one; nothing here is inferred.
          </p>

          <div className="space-y-3">
            <h3 className="text-lg font-semibold">Binding agreements</h3>
            {binding.length === 0 ? (
              <p className="rounded-lg border border-dashed border-border px-6 py-6 text-muted-foreground">
                None found.
              </p>
            ) : (
              <ul className="grid gap-3">
                {binding.map((event) => (
                  <EventCard key={event.event_id} event={event} />
                ))}
              </ul>
            )}
          </div>

          <div className="space-y-3">
            <h3 className="text-lg font-semibold">Non-binding / rumor -- display only, never scored</h3>
            {displayOnly.length === 0 ? (
              <p className="rounded-lg border border-dashed border-border px-6 py-6 text-muted-foreground">
                None found.
              </p>
            ) : (
              <ul className="grid gap-3">
                {displayOnly.map((event) => (
                  <EventCard key={event.event_id} event={event} />
                ))}
              </ul>
            )}
          </div>

          {abstained.length ? (
            <details className="rounded-lg border border-border p-4">
              <summary className="cursor-pointer font-medium">
                {abstained.length} filing{abstained.length === 1 ? "" : "s"} could not be
                confidently classified
              </summary>
              <ul className="mt-3 grid gap-3">
                {abstained.map((event) => (
                  <EventCard key={event.event_id} event={event} />
                ))}
              </ul>
            </details>
          ) : null}
        </>
      )}
    </section>
  );
}
