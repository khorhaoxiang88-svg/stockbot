# stockbot

US stock research system. Phases F1–F12 are built and committed: identity,
classification, prices, SEC facts, derived fundamentals, insider transactions,
dilution, the composite score, the risk-flag panel, weekly candidate selection
with the two simulated books, execution under a frozen protocol, and the
automated harness proving Phase F's exit criteria.

What exists right now:

- a Python pipeline with eleven ingest, compute and verification stages,
- fifteen applied migrations over a SQLite database,
- a frozen, version-governed configuration file for Release 1,
- a Next.js web app with a `/health` page (now including the Phase F
  verification report), a `/security/[id]` page carrying identity, price
  charts, an XBRL fact browser, fundamentals, insider activity, dilution, the
  full composite score breakdown and the risk panel, a `/candidates` page
  carrying the weekly selection and its suppression log, a `/performance` page
  reporting execution results per horizon, and a `/debug/[security_id]` page
  dumping every stored row for one security, raw, across every table.
- the "Engineering validation dataset — not strategy performance" banner on
  every page, in the root layout.

The fixture is 50 securities. Current contents: 34,593 price bars, 474
corporate actions, 1,103,138 XBRL facts across 3,034 payloads, 748 derived
fundamentals, 6,788 insider rows (198 scored purchases), 50 dilution signals,
50 composite scores (16 rankable) and 650 risk flags. The current weekly
selection produces zero candidates, which is the correct result: the price
ingest is outside its freshness SLA and `composite_threshold` is still the
declared null placeholder. Both are recorded in the suppression log, and F11's
execution run is therefore a correct no-op — there is nothing to execute yet.

**Phase F exit-criteria verification: 10 of 10 checks PASS — Phase F exit gate
clear as of 2026-08-04.** The tenth, 20 Form 4 filings hand-verified against
live EDGAR documents (6 of them amendments, zero discrepancies), was a named
human task with zero mechanism to fake, and it is now recorded as real rows in
`filing_verifications` rather than a manufactured PASS. That table lives in
`data/`, which `.gitignore` excludes — the verification is durable in the
database, not in git; see
[Phase F exit-criteria verification](#phase-f-exit-criteria-verification-migration-015)
below.

**Phase S has started.** S1 (migration 016) replaces the fixture with a
rules-based universe, still non-official. S2 (migration 017) adds resumable,
rate-limited orchestration and has run twice for real: 250 securities (zero
failures, 48.7 min) and 700 more (zero failures, 4h20m). 937 securities
evaluated total: 367 included, 570 excluded, all 50 fixture securities
accounted for. The full multi-thousand-ticker candidate set is not yet
ingested — see
[S1: rules-based universe](#s1-rules-based-universe-migration-016) and
[S2: scaled ingestion](#s2-scaled-ingestion-migration-017) below.

---

## Folder map

```
stockbot/
  config.frozen.json      Frozen Release 1 parameters. Version-governed.
  .env.example            Template for .env. Copy it, never commit .env.
  .gitignore              Excludes .env, /data, and session tooling files.
  migrations/             Numbered SQL, one .up.sql and one .down.sql each.
  pipeline/               Python side.
    .venv/                Virtual environment (not committed).
    requirements.txt
    migrate.py            Migration runner.
    config_loader.py      Loads, validates and version-guards the frozen config.
    universe/             Identity, symbol history, classification, the fixture,
                          and (S1) the candidate pool and membership rules.
    prices/               Provider interface, ingest, read-time adjustment,
                          revisions and dataset versions.
    sec/                  Raw payload store, XBRL facts, acceptance timestamps.
    fundamentals/         Concept mapping, derived metrics, knowledge states.
    insider/              Form 4 parsing and ingest, supersede semantics.
    dilution/             Filing classification and the D1-D4 dilution score.
    scoring/              Percentiles, cohorts, the three components, the
                          insider bonus and the composite.
    riskflags/            Deterministic risk detectors, Altman Z'', and the
                          going-concern phrase detector.
    selection/            The trading-week calendar, freshness gates, the
                          selection rule, and the two books.
    execution/            R1-PROTOCOL-1.1: entry, slippage, corporate actions
                          mid-hold, exits, and delisting resolution.
    verification/         The ten Phase F exit-criteria checks and the harness
                          that runs and records them.
    orchestrate/          (S2) Resumable, rate-limited scaled ingestion.
    tests/                pytest suite.
  data/                   SQLite database and raw payloads. Never committed.
    stockbot.db
    raw/                  Raw source payloads land here, one folder per source.
  web/                    Next.js 15 App Router, TypeScript, Tailwind, shadcn/ui.
    app/layout.tsx         Root layout; renders the Phase F banner on every page.
    app/health/page.tsx   The health page, plus the verification report.
    app/security/[id]/    The per-security page.
    app/candidates/       This week's selection and the suppression log.
    app/performance/      Execution results, per horizon, never pooled.
    app/universe/          (S1) Membership, status, reason, change log.
    app/debug/[security_id]/  Every stored row for one security, raw.
    components/           Phase banner, price chart, score breakdown, risk panel.
    lib/                  db, config, adjust, rank, performance, paths, time helpers.
    tests/                vitest suite.
```

## Rules this project follows

1. **Timestamps are stored in UTC.** Conversion to US Eastern happens only for
   display and market-calendar logic (`web/lib/time.ts`). `prices.date`,
   `ex_date` and `filed_date` are ET *trading dates* by design.
2. **Schema changes only ever go in a new numbered migration.** Never edit a
   migration that has already been applied. Two briefs have specified a
   migration number that was already taken; both times the migration was
   renumbered rather than the applied file edited.
3. **`config.frozen.json` is frozen for Release 1.** Changing a value means
   bumping the matching `*_version` key, and for the values listed under
   `_governed_by` that rule is **enforced**, not merely documented — see
   [Version-governed configuration](#version-governed-configuration).
4. **A ticker is never identity.** `security_id` is the only stable key and
   every symbol lookup takes a date.
5. **Never impute, never zero-fill.** A missing input yields NULL plus a reason,
   never a substituted zero.
6. **`.env` and `/data/` are never committed.**

---

## First-time setup

Run these from the `stockbot` folder.

**1. Python environment** (already created; recreate only if it is missing)

```bash
python -m venv pipeline/.venv
pipeline/.venv/Scripts/python.exe -m pip install -r pipeline/requirements.txt
```

**2. Your local environment file**

```bash
cp .env.example .env
```

Then add these two lines to `.env`. The SEC rejects automated requests that do
not declare who is making them, so nothing SEC-related runs without the first
one:

```
SEC_USER_AGENT=Your Name your.email@example.com
SEC_MAX_RPS=8
```

**3. Create the database**

```bash
pipeline/.venv/Scripts/python.exe pipeline/migrate.py up
```

**4. Web dependencies** (already installed; only needed on a fresh clone)

```bash
npm install --prefix web
```

---

## Everyday commands

Check which migrations are applied:

```bash
pipeline/.venv/Scripts/python.exe pipeline/migrate.py status
```

Apply everything pending (safe to run twice — it does nothing the second time):

```bash
pipeline/.venv/Scripts/python.exe pipeline/migrate.py up
```

Roll back the newest migration:

```bash
pipeline/.venv/Scripts/python.exe pipeline/migrate.py down
```

Roll back everything:

```bash
pipeline/.venv/Scripts/python.exe pipeline/migrate.py down --to 000
```

Run the Python tests (isolated only -- every test builds its own temp
database from the migrations, never touches `data/stockbot.db`; this is
what CI runs):

```bash
pipeline/.venv/Scripts/python.exe -m pytest pipeline/tests -q -m "not live_db"
```

Run the live-database audit separately, against a real checkout with a real
`data/stockbot.db` (never in CI, which has no production database to check;
never concurrently with a scheduled pipeline run -- see
[S6: unattended scheduling](#s6-unattended-scheduling-pipelinescheduler) for
the cross-process lock both share):

```bash
pipeline/.venv/Scripts/python.exe -m pytest pipeline/tests -q -m live_db
```

Run the web tests:

```bash
npm test --prefix web
```

Start the web app, then open <http://localhost:3000/health>:

```bash
npm run dev --prefix web
```

Never run `npm run build` while `npm run dev` is running: both write
`web/.next` and it breaks the dev server.

### The full pipeline, in dependency order

Each stage depends on the ones above it. Re-running any stage is safe.

```bash
pipeline/.venv/Scripts/python.exe pipeline/universe/load_fixture.py
pipeline/.venv/Scripts/python.exe pipeline/prices/ingest.py
pipeline/.venv/Scripts/python.exe pipeline/sec/ingest_facts.py
pipeline/.venv/Scripts/python.exe pipeline/fundamentals/compute.py
pipeline/.venv/Scripts/python.exe pipeline/insider/ingest.py --since 2015-01-01
pipeline/.venv/Scripts/python.exe pipeline/dilution/compute.py
pipeline/.venv/Scripts/python.exe pipeline/scoring/compute.py --as-of 2026-07-29
pipeline/.venv/Scripts/python.exe pipeline/riskflags/compute.py --as-of 2026-07-30
pipeline/.venv/Scripts/python.exe pipeline/selection/compute.py --as-of 2026-08-02
pipeline/.venv/Scripts/python.exe pipeline/execution/compute.py --as-of 2026-08-02
pipeline/.venv/Scripts/python.exe pipeline/verification/compute.py
```

Verification takes no as-of date: it checks whatever is currently stored, not
a point in time. Run it after any of the stages above to confirm nothing
regressed.

The last four take different as-of dates on purpose. A score belongs to a
trading session, so `scoring` snaps back to the last session. A risk flag is a
statement about filings and accounts, which arrive on calendar days, so
`riskflags` uses the date given verbatim - passing a session date would put the
panel behind evidence that was already public. `selection` takes today's date
and resolves the week itself, refusing to run for a week that is not provably
over. `execution` also takes today's date: it is how far forward the
walk-forward pass advances every open position, and it is independent of which
week `selection` last ran for.

`riskflags` fetches filing documents from EDGAR for the going-concern check and
therefore needs `SEC_USER_AGENT`. Add `--no-network` to skip it; the flag then
records itself as an explicit unknown rather than as a clean result.

---

## How migrations work

- Files are named `NNN_snake_case_name.up.sql` and `NNN_snake_case_name.down.sql`.
- The runner applies them in ascending number order.
- Every applied version is written to the `schema_migrations` table, so a second
  `up` is a no-op.
- Each migration runs inside one transaction: it fully applies or nothing changes.
- `schema_migrations` is deliberately **not** dropped by a rollback. It is the
  ledger the runner needs, and the runner recreates it on connect. After a full
  rollback the table is still there with zero rows.

The fifteen applied migrations, and what each one owns:

| # | Tables and views |
|---|---|
| 001 | `pipeline_runs`, `source_health`, `schema_migrations` |
| 002 | `securities`, `listings`, `universe_snapshot_runs`, `universe_snapshots`, `fixture_manifest` |
| 003 | `prices`, `price_revisions`, `price_dataset_versions`, `price_series_provenance`, `corporate_actions` |
| 004 | `raw_payloads`, `filings`, `xbrl_facts` (+ `usable_facts`) |
| 005 | `concept_mappings`, `derived_fundamentals` (+ `latest_fundamentals`) |
| 006 | `insider_transactions` (+ `scored_insider_purchases`, `effective_insider_transactions`) |
| 007 | rebuild of 006 to allow a NULL `amends_accession` |
| 008 | `dilution_signals` |
| 009 | `scores` (+ `latest_scores`) |
| 010 | `risk_flags` (+ `latest_risk_flags`) |
| 011 | `research_candidates`, `suppressed_signals`, `books`, `positions` (+ `latest_selection`) |
| 012 | rebuild of 010 to add the `overdue_issuer_filing` flag code |
| 013 | `paper_positions`, `benchmark_positions`, `cancelled_entries`, `position_events`; drops `positions` (superseded — see below) |
| 014 | two triggers backstopping "at most one open position per (security, horizon)" at the storage layer |
| 015 | `verification_results`, `filing_verifications` (+ `latest_verification_results`) |
| 016 | `universe_candidate_pool`, `universe_membership_changes`; adds `run_type` to `universe_snapshot_runs`; rebuild of `universe_snapshots` to require a reason for `watch` too |
| 017 | `orchestration_progress` |
| 018 | `calibration_reports` |

Migration 001 creates operations tables only. Every market-data table arrives
from 002 onward.

## Identity model (migration 002)

A ticker symbol is **never** a primary key. Symbols change hands: Big Lots' `BIG`
is gone, and some other company can be issued it later.

- `securities.security_id` is the only stable identity. It is an
  `INTEGER PRIMARY KEY AUTOINCREMENT`, so SQLite never reissues a number.
- Securities rows are never deleted. A delisting sets `is_active = 0` and a
  `delisted_date`.
- `listings` holds symbol history: `(security_id, symbol, valid_from, valid_to)`.
  `valid_from` is inclusive, `valid_to` is exclusive, `NULL` means current.
- Every symbol lookup needs a date: `identity.resolve_symbol(conn, "XYZ", "2010-05-05")`.

Load the 50-security fixture (needs `SEC_USER_AGENT` in `.env`):

```bash
pipeline/.venv/Scripts/python.exe pipeline/universe/load_fixture.py
```

Re-running is safe. Securities are matched on `(cik, share_class)`, so the same
`security_id` is reused rather than a duplicate row being created.

## Classification

`pipeline/universe/classify.py` decides `security_type` and a confidence, and
records `classification_source` for every decision. It never guesses from a
ticker suffix.

Evidence order:

1. Nasdaq Trader **Test Issue** flag.
2. Nasdaq Trader **ETF** flag.
3. The official **Security Name** text in the directory files.
4. For securities no longer in those files, the registered security title on the
   most recent 10-K cover page (`dei` "Title of each class").

Anything unresolved is `unknown` with `low` confidence. Unknown is never
rankable, enforced in three places: the `Classification` dataclass, the
`is_rankable` helper, and a `CHECK` constraint in migration 002.

Four rule-ordering bugs were found by running the classifier against the live
directory files, and each has a regression test:

- "American Depositary Shares" was read as a preferred share because of the word
  "Depositary". ADR now outranks preferred.
- A SPAC unit was read as a warrant, because a unit's official name describes the
  warrant inside it. Unit now outranks warrant.
- "Class C **Capital Stock**" (Alphabet) matched nothing.
- "Lennar Corporation Class B" has no instrument word at all. It now resolves to
  common stock at **medium** confidence, with its own source label.

## Prices (migration 003)

Ingest 3 years of raw daily bars for every fixture security:

```bash
pipeline/.venv/Scripts/python.exe pipeline/prices/ingest.py
```

Limit to a few symbols while developing:

```bash
pipeline/.venv/Scripts/python.exe pipeline/prices/ingest.py --symbols NVDA CMG WMT
```

### Raw only

`prices` has no adjusted-close column. Adjustment happens at read time in
`pipeline/prices/adjust.py` (and its mirror `web/lib/adjust.ts`) from the
`corporate_actions` ledger:

```
factor(d)   = product of ratios of every split with ex_date AFTER d
adjusted(d) = raw(d) / factor(d)
```

A later correction to a split ratio therefore fixes every chart at once, with no
rewrite of price history.

**The trap this hit.** yfinance's `auto_adjust=False` only turns off *dividend*
adjustment. The OHLC it returns is still **split-adjusted by Yahoo at source**.
Verified 2026-07-29: Yahoo reports NVDA's 2024-06-07 close as 120.89, but NVDA
traded near 1208.90 that day and did not split until 2024-06-10. Storing that
and then adjusting again produced a **+907% artificial return** across the split.
`YFinanceProvider` now un-adjusts on the way in, so what reaches the database is
genuine traded price. `pipeline/tests/test_yfinance_provider.py` guards it
offline.

### Splits vs spin-offs

Yahoo packs spin-off adjustment factors into the **same** `Stock Splits` column
as real splits, and exposes nothing that tells them apart — `Ticker.actions` has
only `Dividends` and `Stock Splits`.

Confirmed against SEC filings on 2026-07-29:

| Symbol | Date | Yahoo "split" | What it actually was |
|---|---|---|---|
| HON | 2025-10-30 | 1.061 | Solstice Advanced Materials spin-off (8-K; Form 10-12B CIK 0002064953) |
| HON | 2026-06-29 | 0.9535 | Honeywell Aerospace spin-off (8-K) |
| LEN | 2025-01-21 | 1.033 | Millrose Properties spin-off (8-K) |
| LEN.B | 2025-01-21 | 1.052 | same event, different class |

Three independent tells:

1. **The ratio is not a split ratio.** Real splits are small whole-number
   ratios. `is_clean_split_ratio()` accepts a value only if it (or its
   reciprocal) is a fraction with denominator ≤ 20.
2. **The implied move is wrong.** HON's 0.9535 implies a +4.88% move; the stock
   actually fell 1.90%.
3. **Two share classes got different ratios on one date.** LEN 1.033 vs LEN.B
   1.052. A real split applies identically to every class of an issuer.

So an unclean ratio is filed as `action_type = 'other'` with
`requires_manual_review = 1`, recording what is actually known — a ratio-bearing
action of undetermined type — instead of asserting "split". It is then excluded
from adjustment twice over, since `adjusted_series` takes only
`action_type = 'split'` with the review flag clear.

**Un-adjusting and classifying are separate jobs.** The provider still undoes
*every* factor Yahoo applied, spin-offs included, because that is what recovers
the traded price. Only the ledger entry changes. Filtering the un-adjustment
would leave spin-off securities permanently mis-scaled.

Promoting these rows from `other` to `spinoff` needs an authoritative source
(SEC 8-K item 2.01 / Form 10-12B), which is a candidate for a later phase. Until
then they stay flagged rather than guessed.

### Swapping provider

Everything goes through `PriceProvider` in `pipeline/prices/base.py`. To change
vendor, write one class and change `ACTIVE_PROVIDER` in
`pipeline/prices/registry.py`.

Series are never spliced. Switching vendors means refetching a security's whole
history and closing its `price_series_provenance` window —
`ingest.switch_provider()` does both. Stitching a new vendor onto the end of an
old series would put a fake return at the seam.

### Revisions and dataset versions

On re-fetch, each bar is compared against what is stored. A difference beyond
half a cent (or any volume change) writes the **complete** old and new OHLCV to
`price_revisions`, increments that row's `revision`, creates a new global
`price_dataset_versions` row, and only then updates the canonical row. Nothing
is absorbed silently.

Re-running on unchanged data updates `last_verified_at` and nothing else — no
new dataset version.

`pipeline/prices/versions.py` reconstructs the series as of any earlier dataset
version by walking revisions backwards, which is what makes a backtest
reproducible after the vendor revises history underneath it.

### Date semantics

`prices.date` and `corporate_actions.ex_date` are US market **trading dates**
(Eastern calendar dates), because a daily bar belongs to a session, not to a UTC
instant. Every true timestamp column stays UTC.

## SEC facts (migration 004)

```bash
pipeline/.venv/Scripts/python.exe pipeline/sec/ingest_facts.py
```

### Why this table cannot be retrofitted

Once source facts have been collapsed or overwritten, the evidence needed to
validate them later is gone. So `xbrl_facts` is **append-only, enforced by
database triggers**, not by convention. `UPDATE` and `DELETE` both raise.

Uniqueness is `(payload_id, source_fact_key)` — **source** identity.
`source_fact_key` is `taxonomy|concept|unit|position`, the position being the
fact's index in the source document. Two facts that normalise identically are
still two rows, which is exactly what a restatement is.

- `semantic_hash` covers `(taxonomy, concept, unit, context_type, period_start,
  period_end, dimensions_json)` and is used **only** for grouping and duplicate
  detection. It is never a uniqueness constraint.
- `context_hash` covers the context fields alone, for context grouping.

**`semantic_hash` deliberately excludes the CIK**, because the specified field
list excludes it. 71,910 hash values are therefore shared by more than one
company. Every duplicate or restatement query must group by
`(cik, semantic_hash)`, never `semantic_hash` alone.

### Payload preservation

Every response is written to
`data/raw/{source}/{yyyy}/{mm}/{content_hash}.json.gz`, addressed by the sha256
of its **uncompressed** bytes. SQLite stores metadata only. `read_payload()`
verifies the hash before returning anything and raises `PayloadCorruptError` on
a mismatch — a payload that does not match its recorded hash is evidence of
corruption, and continuing would poison everything derived from it.

### Acceptance timestamps

`filed_date` cannot support an intraday cutoff, so `accepted_at` is resolved
through the accession in EDGAR submissions metadata and stored in UTC.

`submissions.recent` holds only the most recent ~1000 filings, and Company Facts
routinely references older accessions than that (AAPL: 71 fact-bearing
accessions, 27 of them outside the recent window). The paginated
`filings.files` pages are fetched too. Skipping them would have left a large
share of historical facts unresolved.

A fact with no resolvable `accepted_at` is **unusable for official candidates**.
That rule lives in one place, the `usable_facts` view.

### Known limitation, implemented not worked around

SEC Company Facts returns consolidated facts only. Every key an entry can carry
was enumerated from a live payload: `accn, end, filed, form, fp, frame, fy,
start, val`. There is no `decimals`, no nil flag, and no dimensional member.

So `decimals`, `is_nil` and `dimensions_json` are stored as **NULL** and
`source_endpoint` is `'companyfacts'`. They are not inferred or defaulted.
Recovering them means parsing instance documents, which Release 1 does not do.

## Derived fundamentals (migration 005)

```bash
pipeline/.venv/Scripts/python.exe pipeline/fundamentals/compute.py
```

Wider window for a specific issuer (used to reach older restated periods):

```bash
pipeline/.venv/Scripts/python.exe pipeline/fundamentals/compute.py --symbols SMCI --since 2014-01-01 --max-periods 12
```

### knowledge_date

`(security_id, period_end, knowledge_date)` is the primary key. A knowledge state
is one acceptance timestamp at which some filing reported that period. An
amendment **adds a row**; it never overwrites one. Without that, "what did we
know on 2024-08-01" is unanswerable and a backtest silently uses restated figures
the strategy could not have seen.

SMCI demonstrates it: gross margin for FY2016 reads 0.165816, then 0.165142, then
0.149193 across three knowledge dates, the last from the delinquent catch-up
filing. All three rows exist.

`latest_fundamentals` is a convenience view for "as known today". **Point-in-time
queries must not use it** — they filter `derived_fundamentals` on
`knowledge_date <= cutoff`.

### Concept mapping

Issuers tag the same quantity differently. `pipeline/fundamentals/mappings.py`
holds a priority-ordered, versioned mapping (`MAPPING_VERSION`), seeded into
`concept_mappings`. Revenue alone maps five tags, from the ASC 606
`RevenueFromContractWithCustomerExcludingAssessedTax` down to the retired
`SalesRevenueNet`.

Every derived value stores `<metric>_concept_used` and `<metric>_accession`, so
each number is traceable to one tag in one filing. Nothing is blended or averaged.

### Validity rules

Never impute, never zero-fill. A missing or invalid input yields NULL plus a
reason in `missing_fields_json`.

| Metric | Invalid when | Special value |
|---|---|---|
| P/E | earnings ≤ 0 | — |
| P/B | book value ≤ 0 | — |
| EV/EBITDA | EBITDA ≤ 0 | — |
| ROIC | invested capital ≤ 0 | — |
| Interest coverage | debt > 0 and interest missing | cap 50 when debt = 0 |
| Debt/EBITDA | EBITDA ≤ 0 | 0 when debt = 0 |
| Current ratio | current liabilities = 0 | capped at 5.0 |

`Input.__bool__` raises on purpose: a zero value is *present*, and truthiness
would silently treat it as missing.

### Point-in-time market cap

`dei:EntityCommonStockSharesOutstanding` is a cover-page instant dated when the
filing was prepared, not at the fiscal period end, so matching it to the period
almost never hits. Market cap is specified as point-in-time, so the share count
is the latest one knowable at the knowledge date, priced from the corresponding
raw close.

Multi-class issuers are genuinely ambiguous — one class's share count times that
class's price is not the whole company. The inputs, a confidence state and an
ambiguity reason are all stored, and the figure is never presented as exact.

### Model applicability

SIC division H (6000–6799) — banks, insurers, other financials and REITs — get
`model_applicable = false` and are never ranked. EV/EBITDA is meaningless when
debt is raw material rather than financing, and a current ratio has no
interpretation for a balance sheet with no operating cycle.

Excluded in the fixture: JPM, USB (6021), O, PLD, ABR$D (6798), BRK.B (6331),
VAC (6531). VAC is a timeshare operator whose SIC says real estate; the rule is
applied as written rather than maintaining an exception list.

## Insider transactions (migration 006)

```bash
pipeline/.venv/Scripts/python.exe pipeline/insider/ingest.py --since 2015-01-01
```

### Table I vs Table II

Table I (non-derivative) and Table II (derivative) are different instruments.
Both are stored and `table_type` distinguishes them, but **only Table I is ever
scored**. A Table II code P is a derivative purchase, not shares bought on the
open market, so `scored_insider_purchases` excludes it.

### Transaction codes

All codes are stored verbatim. **Only P is scored as a purchase.** A grant (A)
or an option exercise (M) is not cash conviction, and counting either as a
purchase would manufacture the exact edge this system claims to measure.

The rule lives in one view, `scored_insider_purchases` — Table I, code P,
superseded rows excluded — so no query can invent its own definition.

### Plan status is never guessed

`plan_status` is `discretionary`, `confirmed_10b5_1` or `unknown`, and
`plan_status_source` records how it was decided.

The Rule 10b5-1 checkbox (`aff10b5One`) is a recent addition to Form 4.
Verified 2026-07-30: present in all 14 modern filings sampled, **absent** from a
2018 amendment. Resolution order:

1. checkbox present → `confirmed_10b5_1` if set, `discretionary` if clear, source `checkbox`
2. otherwise footnote text → `confirmed_10b5_1`, or `discretionary` when the
   footnote *denies* a plan ("not made pursuant to a Rule 10b5-1 plan"), source `footnote`
3. otherwise `unknown`, source `absent`

A database CHECK enforces that `unknown` carries `absent` and that a determined
status never does, so a default can't creep in through a later code path.

### Amendments supersede, they do not delete

A 4/A sets `superseded_by_accession` on the original rows. **The original is
retained.** Reads filter through `effective_insider_transactions` or
`scored_insider_purchases`, so a corrected filing is counted once, never twice
and never zero times.

A 4/A carries `dateOfOriginalSubmission` but **not** the original's accession, so
the link is derived from (security, insider, period of report) filed on or
before the amendment.

## Data sources

Both were verified against live responses on 2026-07-29.

| Source | Endpoint | Notes |
|---|---|---|
| SEC ticker map | `www.sec.gov/files/company_tickers.json` | Requires `SEC_USER_AGENT`. Max 10 requests/second; the client caps itself at 8. |
| SEC submissions | `data.sec.gov/submissions/CIK##########.json` | Company name, SIC code, exchanges, filing history. |
| SEC insider data | `www.sec.gov/files/structureddata/data/insider-transactions-data-sets/<q>_form345.zip` | Quarterly Form 3/4/5 tables. Used to compute insider clusters from `TRANS_CODE = 'P'`. |
| Nasdaq Trader | `www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt`, `otherlisted.txt` | Pipe-delimited. Drop the `File Creation Time:` footer row. |
| Prices | yfinance 1.5.2 (Yahoo Finance) | Private, non-commercial prototype source only — yfinance is not affiliated with Yahoo, and the Yahoo Finance API is documented as personal use. Returns split-adjusted OHLC even with `auto_adjust=False`; the provider un-adjusts it. |

Two gotchas confirmed from the live data:

- The published Nasdaq field-definitions page **omits** the `ETF` and
  `NextShares` columns that `nasdaqlisted.txt` actually has, so the client parses
  the header row instead of assuming a column order.
- The two sources disagree on symbol punctuation: Nasdaq writes `BRK.B` and
  `ABR$D`, the SEC writes `BRK-B` and `ABR-PD`. `sec_symbol()` converts between
  them. This is the clearest evidence that a symbol is not an identity.

## Configuration notes

- `composite_threshold` is intentionally `null`. It is a declared placeholder and
  gets its value in Phase S. Both config loaders allow null for this key only,
  and both the health page and `config_loader.py` report it as outstanding.
- `freshness_sla` lists a per-source maximum staleness in hours, **measured from
  the moment selection runs**, not from the evidence cutoff. Finalised in F10:
  `prices_daily` and `corporate_actions` moved 24h → 72h and `filings` 48h → 96h,
  because a Friday-close selection is legitimately three days old by Monday and
  the first-pass budgets failed every weekend without the pipeline being
  unhealthy. `fundamentals` (720h) and `symbol_master` (168h) are unchanged.
  Nothing in the config is flagged `_provisional` any more.
- `high_leverage_debt_ebitda` (4.0x) was added in F9. The brief specified the
  high-leverage flag as "debt/EBITDA above configured threshold" but supplied no
  number and no existing key carried one.
- The required-key list exists twice on purpose: `pipeline/config_loader.py` for
  Python and `web/lib/config.ts` for the web app. Adding a key means editing both.

### Version-governed configuration

"Changing a value means bumping the matching `*_version` key" used to be a rule
in this file that nothing checked. For the values listed under `_governed_by`
it is now enforced by both loaders.

`_governed_by` names, per version key, the parameters whose value changes what a
stored number *means* — thresholds and caps, not operational settings.
`_version_digests` records the sha256 of those values at each version, as a
**history** rather than a single current value, so a past version's meaning
cannot be quietly rewritten.

Change a governed value and validation fails:

```
config.frozen.json failed validation:
  - values governed by strategy_version have changed but strategy_version is
    still 2. Bump it and record the new digest 9f5ac193... (recorded: 61a5aa1b...)
```

The workflow is: change the value, bump `strategy_version`, add the new digest.
It has been exercised once already: F10 finalised `freshness_sla`, which took
`strategy_version` from 1 to 2. Version 1's digest is still in the file, because
the map only ever grows.

```bash
pipeline/.venv/Scripts/python.exe pipeline/config_loader.py --digest
```

Declared placeholders are deliberately **not** governed. Filling
`composite_threshold` in Phase S is an expected event already tracked in
`_placeholders`, and governing it would force a version bump for something the
config says is coming.

The digest is taken over `key=value` lines with values canonicalised, not over
JSON text, because the two languages serialise differently. Python renders `4.0`
as `"4.0"` and JavaScript renders it as `"4"`; Python renders a dict as
`"{'a': 1}"` and JavaScript renders it as `"[object Object]"`. Either mismatch
would give the two loaders different digests and fire the guard on every page
load, so numbers are normalised to the shortest round-trip form and objects and
arrays are walked with their keys sorted. `freshness_sla` is a governed object
and exercises that path. A test on each side asserts both compute the digest
recorded in the file.

## Dependency notes

- `better-sqlite3` is pinned to **12.x**. Version 13 dropped its prebuilt
  binaries and compiles from source, which needs Visual Studio build tools on
  Windows. 12.x ships a prebuilt binary and installs cleanly.
- `next.config.ts` lists `better-sqlite3` under `serverExternalPackages` so the
  native module is loaded by Node instead of being bundled.

## Dilution (migration 008)

```bash
pipeline/.venv/Scripts/python.exe pipeline/dilution/compute.py
```

**Numbering:** the F7 brief called this "migration 007", but 007
(`amendment_link_nullable`) was already applied in F6. Editing an applied
migration is forbidden, so the table is **008**. Contents unchanged.

### Gates run before any point is awarded

The fixture holds **126,659 424B2 filings across 39 issuers**, overwhelmingly
bank medium-term notes and structured notes. Counting 424B2 filings without
classifying them would disqualify JPMorgan and US Bancorp for issuing debt,
while the genuinely dilutive small caps file fewer than ten each.

`pipeline/dilution/classify.py` therefore establishes what a filing IS before it
can score: `equity_offering`, `atm_programme`, `variable_convertible`,
`shelf_415`, `debt_or_structured` (zero), or `unknown` (zero).

**Unknown is not risk.** It is absence of evidence and never produces penalty
points.

**Debt is tested first.** A note prospectus routinely mentions common stock — as
a structured note's reference asset, or in Plan of Distribution boilerplate.
Testing equity language first classified Merck's shelf boilerplate as an ATM
programme. Only genuinely floating conversion terms override a debt
classification, because a variable-priced convertible really is dilutive.

The `_ATM` pattern requires the phrase to be tied to a programme AND to common
equity within a bounded window; bare "at-the-market" is boilerplate.

### Frozen formula

D1 capacity 0-4, D2 issuance 0-10 (1 filing 4, 2 filings 7, 3+ filings 10),
D3 structural 0-8 (ATM 4, variable convertible 8, maximum not sum),
D4 realised `12 * clamp((g - 0.05) / 0.35, 0, 1)` on SPLIT-ADJUSTED share growth.
`score = min(30, ΣD)`, `is_disqualified = score >= 22`.

Three invariants are database CHECKs, not conventions: the score equals the
formula, disqualification equals the threshold, and `D2 + D4 > 0` is required to
disqualify — so capacity alone can never disqualify (D1 + D3 max at 12).

Share growth restates the earlier count onto the later split basis, so a 2-for-1
split reads as 0% growth, not 100% dilution. Spin-off factors (filed by
migration 003 as `other` with `requires_manual_review`) never restate a count.

### KNOWN FORMULA LIMITATION — for Phase S calibration review

**GNS scores 16 and is NOT disqualified despite +148.2% split-adjusted share
growth.** D4 caps at 12, and with one qualifying takedown (D2=4) and no shelf or
ATM detected, 16 is its ceiling against a threshold of 22. A company that grew
its share count roughly 2.5x in a year passes.

The formula is frozen and was deliberately left unchanged. **F9's
`rapid_share_growth` flag must surface this growth prominently regardless of
composite score**, so a diluter of this shape is visible even when the dilution
score does not disqualify it.

Related: **PHUN scores 4** despite being the fixture's designated diluter — its
14 424B5 filings all predate the 12-month D2 window (most recent 2024-11-01).
D2 is a trailing-12-month measure by design.

## Composite score (migration 009)

```bash
pipeline/.venv/Scripts/python.exe pipeline/scoring/compute.py --as-of 2026-07-29
```

**Numbering:** the F8 brief called this "migration 008", but 008
(`dilution_signals`) was already applied in F7, so the table is **009**.
Contents unchanged.

```
composite = 0.30*Value + 0.30*Quality + 0.30*Momentum + InsiderBonus
          - DilutionPenalty,  clamped to [0, 100]
```

### Reproducible by hand, or it does not count

`explanation_json` stores, for every submetric: nominal weight, valid yes/no,
effective weight after renormalisation, comparison population name and count,
cohort blend weight, raw value, percentile and final contribution — plus every
insider sub-bonus calculation. The score breakdown on `/security/[id]` renders
that JSON and nothing else, so the page cannot disagree with the database.

A test re-derives every stored score from its own explanation. Five were also
recomputed by hand off the rendered page and matched exactly: AAPL 58.0785,
HON 56.5488, MTDR 54.6092, CAT 39.5753, NVDA 35.5140.

### Percentiles

```
pct(x, P) = 100 * (below + (equal - 1) / 2) / (n - 1)
```

The brief's formula is the no-tie case; the mid-rank correction generalises it
so every member of a tie group gets the same value whatever order the rows
arrived in. **With n < 2 the percentile is UNAVAILABLE, not zero** — a
population of one has no order statistics, and 0 is a real score meaning "worst
in the universe". Lower-is-better metrics invert the percentile, never the raw
value.

Comparison populations are drawn from one **official universe snapshot** at one
knowledge cutoff, both recorded with every ranked metric. F1-F7 never populated
`universe_snapshot_runs`; scoring materialises the snapshot and marks it
official before any percentile is taken, because otherwise a percentile would
depend on which securities happened to have data that day.

### Cohorts are SIC divisions, never GICS

Nothing in this system has ever seen a GICS code. Cohort ids carry a `SIC-`
prefix and the labels say "SIC division" in words. Blending weight is
`w = 0` below 10 valid observations of *that metric* in the cohort, else
`clamp(n_c / 50, 0, 1)`.

### Gates and renormalisation

| Component | Weight | Gate |
|---|---|---|
| Value | 0.30 | at least 3 of 4 submetrics valid |
| Quality | 0.30 | Piotroski fully computable, plus 3 of the remaining 4 |
| Momentum | 0.30 | at least 250 adjusted trading days |

Renormalisation happens strictly **inside** a component. Piotroski's 0.40 share
never moves; a missing non-Piotroski metric is redistributed only within the
remaining 0.60. Momentum never renormalises at all — its seven weights already
sum to 1.00 and a missing input fails the gate rather than inflating the others.
No component weight is ever moved to another component: a NULL component
withholds the whole security and records a `withhold_reason`.

**Withheld is not zero.** A security that cannot be scored stores NULL and a
reason. The web page shows the reason where the score would be.

### The insider bonus

An insider is counted **once**. B1 rewards several different people buying at
the same time, so purchases collapse to one `q_i` per insider by taking the max
and N counts distinct insiders; splitting one decision across three tickets
cannot manufacture a cluster.

Coverage is **proved, not assumed**. F6's ingest caps each security at 60
original Form 4s and 10 amendments, keeping the most recent, and that cap is
invisible in the transactions table. A window is provably covered only if the
cap was never hit, or the oldest ingested filing predates the window. Complete
coverage with no qualifying purchase is an **observed zero** and is still
ranked; incomplete or stale coverage is **unknown** and withholds ranking.

### Winsorisation

Applied only where raw magnitudes enter arithmetic: the F5 interest-coverage cap
(50), the F5 current-ratio cap (5.0), and the Z'' inputs in F9. Nothing is
winsorised before percentile ranking — percentiles are order statistics, so it
would have no effect, and no such code exists.

Altman Z'' is **not** in the composite. It is a risk flag; see below.

## Risk flags (migration 010)

```bash
pipeline/.venv/Scripts/python.exe pipeline/riskflags/compute.py --as-of 2026-07-30
pipeline/.venv/Scripts/python.exe pipeline/selection/compute.py --as-of 2026-08-02
```

**Numbering:** the F9 brief called this "migration 009", but 009 (`scores`) was
already applied in F8, so the table is **010**. Contents unchanged.

Thirteen deterministic detectors. There is no language model anywhere in this
package and nothing here is a written bear case: each flag states what was
measured and links to the filing it was measured from.

### An unknown is a flag

Severity carries six values so the two states that matter stay apart:

| severity | meaning |
|---|---|
| `high` / `medium` / `low` | a risk was **detected**, graded |
| `none` | the check **ran** and detected nothing |
| `context` | neutral information, must not be read as bearish |
| `unknown` | the check **could not run**, with the reason |

Collapsing `none` and `unknown` would make "we looked and it is fine"
indistinguishable from "we never looked". The panel is titled **"Measured risks
and missing evidence"** for the same reason, and renders detected risks and
unknowns in separate labelled sections at the same visual weight as the score.

### Insider selling is context, enforced in SQL

Insiders sell for taxes, diversification and personal reasons. A database CHECK
permits only `context` or `unknown` for `recent_insider_selling`, so no future
code path can promote a sale to a bearish severity.

### The going-concern detector is narrow on purpose

It anchors on two fixed phrases within 240 characters — "substantial doubt" and
"ability to continue as a going concern" — the construction ASC 205-40 and
AS 2415 / AU-C 570 actually require. Nothing looser is matched, because a
general detector fires on ordinary risk-factor prose in every small-cap 10-K.

Three rejection rules cover text that carries both phrases without being a
disclosure: an explicit **denial** ("did not raise substantial doubt"), the
ASC 205-40 policy **definition** ("whether conditions would raise..."), and a
subject that is not the registrant. Doubt that was identified and then
**alleviated** fires at `medium` rather than being dropped: the auditor's
paragraph goes away, the condition having existed does not.

The scan streams the document and matches as it arrives, so there is no size
limit and it stops reading on the first match. Chunks are cut only at
whitespace or a closing tag — an earlier version split words across boundaries,
turning "substantial" into "substan tial", and under-reported silently.

Live result across the fixture: 3 detected (RAD, BIG, SAVE, all Chapter 11
filers, each linked to the exact filing and character offset), 45 clean, 2
unknown (GNS files a 20-F and SPY an N-CSR, so neither has a 10-K or 10-Q).

### Altman Z''

The four-variable Z'' for non-manufacturers, flagged below 1.10. **Not part of
the composite**, and every row carries the applicability caveat: Z'' was fitted
on manufacturers and emerging-market industrials and reads low for financials,
REITs, pre-revenue companies, and companies with negative equity from buybacks.
Its inputs are winsorised and clamping is recorded when it bites — AMC's `x4`
was clamped from -0.1912 to 0.0 on negative equity, stated in the evidence text.

Retained earnings is the one Z'' input F5 does not map. F5's mapping is frozen
at `MAPPING_VERSION` and every `derived_fundamentals` row records that version,
so F9 resolves the concept through the same `FactIndex` machinery rather than
changing the provenance of numbers it does not touch.

### One documented deviation

`recent_reverse_split` cites `ledger:corporate_actions:<security_id>:<ex_date>`
rather than an SEC accession, because the action is observed in the price
vendor's feed and no filing reports it as such. It resolves to exactly one row,
and the panel says so instead of rendering a link that would not work.

The `low` severity tier is defined but currently unused; every detector grades
to high or medium.

## Weekly candidate selection (migrations 011 and 012)

```bash
pipeline/.venv/Scripts/python.exe pipeline/selection/compute.py --as-of 2026-08-02
pipeline/.venv/Scripts/python.exe pipeline/selection/compute.py --verify
```

**Numbering:** the F10 brief called this "migration 010", but 010 (`risk_flags`)
was already applied in F9, so the selection tables are **011**. Migration 012
rebuilds `risk_flags` to add one flag code, `overdue_issuer_filing`; SQLite
cannot alter a CHECK constraint in place, so the table is rebuilt and the rows
copied, the same way 007 rebuilt `insider_transactions`.

**Addition beyond the specified tables:** `positions`. The brief specifies three
tables but states four rules that cannot be expressed without a fourth — one
open position per (security, horizon), the `open_position` suppression, the exit
cooldown, the gap-cancel cooldown — plus `books.open_position_count`, which has
to count something. "At most one open position per (security, horizon)" is a
partial unique index, not a check in code.

### Which week, and is it over

SELECTION-RULE-1.1 runs selection once per US trading week, after that week's
final regular session closes. Neither half of that is as simple as it sounds.

The week's final session is **not Friday**. Good Friday, Christmas Day and a
presidential funeral all move it. Rather than ship a holiday table that will be
wrong the first time the exchange closes unexpectedly, the final session is read
from the sessions we hold bars for.

Whether the week is *over* is the part a naive "maximum date in the week"
misses. A dataset that stops on a Wednesday still has a maximum date in that
week, and it is not the week's close — the data ran out, the week did not end. A
week counts as over only when a session exists in a **later** week, or the
calendar has passed its Saturday **and** the week's last session in the data is
a Friday. When the exchange is shut on a Friday the second clause does not fire
and selection waits for Monday's session: a one-session delay rather than a
wrong answer.

The evidence cutoff is that session's 16:00 ET close converted to UTC, which is
20:00Z in daylight saving and 21:00Z in standard time — hence the conversion
rather than a constant.

### Pipeline freshness is not issuer-report age

Two things that look identical from a distance:

- **Pipeline freshness** is about us. Did the scheduled ingestion run and
  succeed recently enough? A failure blocks every candidate, because selecting
  on numbers we cannot vouch for is worse than selecting nothing.
- **Issuer-report age** is about the company. A company that last filed a 10-K
  eleven months ago is not stale — that is how often companies file. Treating
  an old-but-current filing as stale would block every well-behaved annual filer
  for eleven months of every year.

So the SEC check asks whether *our ingestion* succeeded, never whether the newest
fact is recent. A company that appears to have missed its own deadline gets the
`overdue_issuer_filing` risk flag instead, which never blocks selection.
Filer category is unknown — SEC Company Facts does not return
`dei:EntityFilerCategory`, and there are zero such facts in the fixture — so the
most permissive (non-accelerated) deadlines are assumed throughout. That can
under-report an overdue filer but never invent one, which is the right direction
for a flag that reads as an accusation.

Freshness itself has two axes that must not be conflated:

- **Coverage** is measured against the cutoff. A run that finished before the
  week's close cannot have seen it, however recently it ran.
- **Age** is measured against *now*. Measuring it against the cutoff makes any
  correct backfill look stale, because a backfill always runs after the period
  it ingests.

Point-in-time correctness is not freshness's job. It belongs to the evidence
cutoff, which filters individual accessions by `accepted_at`. Ingest broadly,
then use only what was public at the cutoff.

A run marked `partial` is not a blanket failure. The fact ingest reports partial
because SPY has no companyfacts endpoint at all — it is a unit investment trust
and files N-CSR, so a 404 is the correct answer, not an outage. The failure is
narrowed to the securities the run actually names.

### Determinism

Selection is fully automatic; no human may add, remove or reorder a candidate.
The sort key is total — composite, then Quality, then `inputs_complete`, then
`security_id` — and `security_id` is unique, so two securities can never compare
equal and the order cannot wobble between runs.

`candidate_id` is `sha256(security, cutoff, versions)`, so re-running a week is a
storage-level no-op rather than a second set of rows that append-only would
forbid removing. A re-run that computed a *different* `row_hash` for the same
week aborts loudly instead of appending a second decision.

`research_candidates` is append-only, enforced by triggers. `row_hash` covers
every other field, so a record whose recomputed hash does not match has been
edited and is non-official by definition — a stronger guarantee than a boolean
anyone could also edit. `--verify` recomputes them all.

### Suppression is evidence

Everything considered and not selected is logged with a reason. A candidate list
on its own cannot be audited: there is no way to tell a security that failed a
rule from one the code never looked at. Selection-level reasons are logged once
per horizon, so each book's log independently answers "what qualified and was
not selected".

Eligibility is enforced from two directions that are deliberately kept separate:
the F7 dilution score gate (`dilution_score < 22` and not disqualified) and the
F9 risk-flag view of the same territory (a severity-high `going_concern`, or a
severity-high flag among `rapid_share_growth`, `active_issuance`,
`atm_or_convertible`).

### The two books

One book per horizon, 20-day and 60-day, $100,000 starting virtual NAV each.
One selection produces up to five candidates and **each candidate opens a
position in both books**, so five candidates become ten positions but remain
five independent observations. `research_candidates` deliberately has no horizon
column, so the unique originating candidate count is impossible to lose.

The books are an experimental accounting convention, **not** recommended
position sizing: every position takes the same fixed $1,000 notional whatever
its price or volatility, nothing compounds during Release 1, and cash earns no
interest. They are separate simulated strategy variants, not two independent
observations and not twice the sample. Never pool them.

## Execution under R1-PROTOCOL-1.1 (migration 013)

```bash
pipeline/.venv/Scripts/python.exe pipeline/execution/compute.py --as-of 2026-08-02
```

**Numbering:** the F11 brief called this "migration 011", but 011 (selection and
books) and 012 (the `overdue_issuer_filing` flag code) were both already
applied, so the execution tables are **013**. This migration also **supersedes
and drops** F10's `positions` table: it was the minimum needed to express
"at most one open position per (security, horizon)" before an execution engine
existed to populate a real one. `paper_positions` is that real one, and keeping
both would leave two different answers to "is a position open here". The rule
itself now lives at the layer that decides it — selection's `open_position`
suppression — rather than as a DB constraint on `paper_positions`, which has no
`security_id` column of its own (only `candidate_id`, resolved through
`research_candidates`).

**Addition beyond the specified tables:** `position_events`. `dividends_received`
and `splits_applied` on `paper_positions` are running totals, but the protocol
requires more than totals — a dividend "accrues on the ex-date, records its
payment date, and enters P&L from the ex-date", and the manual checklist asks
for a split and a dividend to be traced end to end. A scalar cannot hold a date
or a sequence, so every corporate action applied to a position is recorded here
as it happens.

### The protocol is frozen; ambiguity is not

Every rule below has exactly one coded answer, because an execution engine that
resolves an edge case however the code happened to be ordered is how a paper
result quietly becomes fiction.

- **Slippage is adverse on every fill, with no exceptions.** Entry pays up,
  every exit — stop, target, gap-through, time-exit — gets less. A database
  CHECK on `slippage_bps > 0` backs up what the arithmetic already guarantees.
- **A split is never mistaken for a gap.** On an entry-session ex-date, the
  prior close is divided by the split ratio *before* the gap test runs, so a
  10-for-1 split's $1,200 → $120 reads as a ~0% gap, not a 90% collapse that
  would cancel the entry outright.
- **Stop and target come from the actual fill, never the signal close.** A
  candidate that gaps up on entry gets its risk levels set from where it
  actually filled; setting them from the signal close would silently move the
  risk on every gapping trade, favourably for gaps down.
- **Both stop and target touched in one daily bar resolves to the stop.** A
  daily bar records that the low reached one level and the high reached the
  other, with no record of which came first. Choosing the target would be
  choosing the profitable interpretation of an unknowable sequence.
- **A gap through a level exits at the open, not the level.** The stop or
  target price was never actually available that session; claiming it would
  manufacture (or erase) money the market never offered.
- **On an ex-date, the action is applied before that day's OHLC is evaluated.**
  A split scales shares up and entry/stop/target down by the same ratio *before*
  the day's high/low are compared to them, so the split alone can never trigger
  a false stop-out. ATR is untouched — it is frozen at entry and already
  expressed on a post-split basis for every later ex-date, since the source
  series F3 exposes is adjusted at read time.
- **A dividend credits only when `entry_date < ex_date`.** A position opened ON
  the ex-date never held the entitled share, by ordinary dividend mechanics.
- **A spin-off, merger or special distribution freezes the position.**
  `requires_manual_review` is set and automatic evaluation stops; nothing here
  attempts to price a corporate action this system was not told how to handle.

### Delisting is never an automatic close

The one rule most likely to flatter a backtest if skipped: a delisted
security's last quoted price may never have been executable — OTC pink sheets
routinely print a "last sale" that nothing could actually trade at. So a
delisting **never** closes a position. Status moves to `pending_resolution`,
`last_evaluated_at` keeps advancing every run, and the position **stays in
reported exposure** — the book's open-position count is not decremented — until
one of two things happens:

1. A **verified** recovery — contractually established merger consideration or
   an executable OTC quote. Neither data source is integrated yet (there is no
   feed for either in this fixture), so this path is implemented and unit
   tested against constructed input, but the live run never takes it, and that
   absence is stated rather than quietly producing a plausible number from
   nothing.
2. **180 days** elapse with no verified recovery. Only then does the recorded
   resolution policy value the common equity at **zero** — the one number that
   needs no judgement call. Rule 4 is enforced literally: no code path in this
   module accepts a manually chosen haircut, at any percentage, from any caller.

Statistics are reported both **including** pending positions at their
provisional value and **excluding** them, so a reader can see the difference a
still-unresolved delisting makes to the headline numbers. "Including" values
every pending position at the recorded policy's eventual zero — the same
number 180 days would produce — never at its notional or at any other invented
figure; `web/lib/performance.ts:pendingAsProvisionalTrades` is the one place
that conversion happens, and it never touches what is actually stored in
`paper_positions`.

### The benchmark is paired, never pooled with book-level results

Every filled position opens a matched SPY position: identical notional,
identical entry timestamp, closing on the same date as its pair — including a
delisted pair's eventual zero-resolution date, so the comparison window always
matches the primary position's true holding period. The `/performance` page
reports the paired return **per closed trade**, in its own column, separate
from the book-level statistics; the two are never blended into one number.

### `/performance`, per horizon, never pooled

Every statistic — sample size, observation window, Wilson 95% win-rate
interval, average win/loss, profit factor, drawdown, sector concentration,
cancellation rate, pending-resolution notional — is computed **twice**,
independently, once for the 20-day book and once for the 60-day. Losses render
in the identical table as wins: same columns, same styling, no separate view
that could make a losing trade easier to look past.

Drawdown is measured against the book's **fixed** starting NAV, never a moving
peak-to-date baseline, and is reconstructed by replaying closed trades in
exit-date order rather than from a stored NAV history table — there isn't one,
and the ledger is sufficient to rebuild the same curve deterministically any
time.

Profit factor is reported as **undefined**, not `Infinity`, when there are no
losing trades yet: `Infinity` reads as a number and invites being averaged or
sorted; "undefined, no losses" cannot be misread that way.

## Phase F exit-criteria verification (migration 015)

```bash
pipeline/.venv/Scripts/python.exe pipeline/verification/compute.py
```

**Numbering:** the F12 brief called this "migration 011", but 011-014 were all
already applied, so the harness tables are **015**.

Ten checks. **PASS, FAIL and PENDING are three different statements**, and
none of the three substitutes for another: PASS means the check ran and found
nothing wrong; FAIL means it ran and found a real problem; PENDING means it
could not run at all yet. "Phase S may not begin until every check passes"
means all ten report PASS specifically — a PENDING check blocks Phase S
exactly as a FAIL does, and the harness never collapses the distinction to
make the report look cleaner than it is.

### Three checks reuse the production code they audit, deliberately

Checks 1, 7 and 8 do not implement a second, parallel version of fundamentals
computation, score reproduction or risk-flag sourcing. A parallel
implementation could be wrong in exactly the same way as the original, or
drift from it silently over time, and neither failure would ever surface.
Instead each check calls the SAME function that produced the stored row
(`fundamentals.compute.compute_row`, the scoring explanation's own arithmetic,
the risk-flag source resolution logic) and diffs the result against what is
stored. A mismatch means the stored row is stale, corrupted, or was hand-edited
relative to the current fact base — which is what "reproduces from stored
facts" actually has to mean to be worth anything.

### Two checks build a fresh synthetic scenario, on purpose

Checks 3 and 6 describe MECHANISM correctness — does a vendor correction leave
an already-generated candidate unchanged, does a split/dividend/delisting trace
cleanly — not "did this happen to already exist in the live data". The fixture
currently holds zero paper_positions (F10 selected no candidates), so checking
only the live database would report PENDING forever on two checks whose truth
has nothing to do with whether trading has actually happened yet. Both build a
small, self-contained scenario with the real `pipeline.execution` modules and
assert on it fresh, every run — a genuine PASS or FAIL, not an unresolvable
PENDING that could never turn green until a live trade occurs.

### Check 5 was PENDING until a human acted, then PASSED for real

Twenty Form 4 filings hand-verified against their live EDGAR source documents,
at least three of them amendments — this is fundamentally a human act, opening
the actual filing and comparing it field by field against what
`insider_transactions` stored. Nothing in this system could fake that.
`filing_verifications` started and stayed empty until it happened for real;
check 5 counts real rows there and reported PENDING with the exact count
against the requirement, never a manufactured PASS.

Completed 2026-08-04: 20 of 20 filings verified, 6 of them amendments (against
a 3-minimum), zero discrepancies against stored data. One informational note
was recorded in `discrepancy_notes` without affecting `matches_source`: FOX's
accession `0001628280-26-017855` line 15 is a code-P "purchase" that footnote
11 identifies as shares moving into the LKM Family Trust, not an open-market
purchase with new capital — the stored code/shares/price are correct as
filed, but F8's insider-purchase scoring cannot currently distinguish an
open-market buy from an intra-family trust transfer, since both are Table I
code P. Flagged for Phase S calibration, not an F6 defect.

**This verification is durable in `filing_verifications`, not in git.**
`data/` is excluded by `.gitignore` (rule 6), so the 20 rows — and the check 5
PASS the harness derives from them on every run — exist only in
`data/stockbot.db` on whatever machine ran the verification. No code, schema,
or migration changed to complete check 5; migration 015 already had the table.
A fresh clone with an empty database will show check 5 PENDING again until
`filing_verifications` is repopulated — that is correct behavior, not a
regression, since the human act the check exists to prove has to happen
against whatever database is actually in front of the checker.

One documented investigation before building this: F6 (migration 006) recorded
a verification of the `aff10b5One` checkbox's presence across 15 filings, one
of them an amendment. That is real work, but it checked one specific field's
presence across 15 filings — not a full field-by-field reconciliation of 20,
with 3+ amendments, that check 5 requires. It never counted toward check 5.

### Every mismatch found while building the harness was in the TEST, not the pipeline

Building the fault-injection tests surfaced three real issues, and it is worth
being precise about where each one lived:

- **A genuine soundness bug in check 4.** Its scan of the fixture stopped early
  once ten securities had reproduced cleanly with zero failures so far. A
  corrupted security appearing LATER in security_id order than the tenth clean
  one would never have been examined at all — a silent false PASS. Found by
  writing the fault-injection test itself, before it ever reached a real
  release. Fixed by removing the early break entirely: check 4 now scans every
  fixture security with a derived_fundamentals row, always.
- **Two test-fixture bugs, not pipeline bugs**, in the synthetic scenarios for
  checks 3 and 6: a placeholder `row_hash` value that the real verification
  function correctly flagged as tampered (the mechanism worked; the test setup
  was sloppy), and a "value preserved through a split" assertion that compared
  the position's slippage-adjusted entry fill against a later raw market
  close — two different price bases that were never going to match to the
  cent. Replaced with the direct invariant a correct split actually preserves:
  shares, entry price, stop and target each scale by the same ratio, checked
  per field.
- **Two DB CHECK constraints were EXPECTED to refuse fault injection outright**
  — corrupting `dilution_signals.dilution_score` or `scores.composite_score`
  directly raises `IntegrityError` before the corruption can land, because
  migrations 008 and 009 already tie those columns to their formulas at the
  storage layer. That is not a gap in the harness; it is a stronger guarantee
  than the harness re-checking one live row would be, and the tests assert
  that guarantee explicitly rather than working around it. The genuinely
  useful fault for check 7 turned out to be corrupting `explanation_json`'s
  internal `value_used` field, which the DB CHECK has no visibility into at
  all — exactly the internal consistency check 7 exists to catch.

### `/debug/[security_id]`

Every stored row for one security, raw, across every table that references it
— directly by `security_id`, or indirectly through `cik` (filings, xbrl_facts)
or through `candidate_id` (paper_positions, cancelled_entries, and
position_events one join further, through paper_positions). This is
deliberately NOT a curated view: every other page in this app formats, labels
and interprets what it shows, and this one exists precisely so state can be
inspected without reading the code that produced it. A `NULL` cell renders as
the literal text `NULL`, never a blank standing in for it. Every table shows
its true row count even when a cap applies — `prices` and `xbrl_facts` are the
only tables that can plausibly exceed the 500-row cap for a security with
years of history, and the cap is stated, never silent.

## S1: rules-based universe (migration 016)

```bash
pipeline/.venv/Scripts/python.exe pipeline/universe/pool_loader.py --target-size 200
pipeline/.venv/Scripts/python.exe pipeline/prices/ingest.py --pool s1-sample-v1
pipeline/.venv/Scripts/python.exe pipeline/sec/ingest_facts.py --pool s1-sample-v1
pipeline/.venv/Scripts/python.exe pipeline/fundamentals/compute.py --pool s1-sample-v1
```

Phase S replaces the 50-security fixture with a full rules-based universe.
**Still non-official** — nothing here feeds an official candidate or
statistic. This migration is a real, bounded first pass: a 200-security
sample drawn from the live NYSE/Nasdaq/NYSE American common-stock directory,
evaluated alongside the Phase F fixture, not yet the full ~3,000-4,000
candidate set the entry rules will eventually run against.

### Why a 200-security sample first, not the full universe

Filtering the full candidate set to the brief's expected 1,000-1,500 needs
price, 60-day ADV, market cap and 8 quarters of XBRL for every NYSE/Nasdaq/NYSE
American common stock — thousands of real SEC and Yahoo calls, likely hours,
before the rules engine has anything to filter. Proving the membership rules
and hysteresis logic correct on a real (not fixture) but bounded slice first
is the same vertical-slice principle Phase F used throughout: an error found
at 200 securities is cheap; the same error found after paying for a
multi-thousand-ticker ingest is not. It found two real bugs — see below — that
a synthetic-only test suite would not have surfaced. The full ingest is
future work; the manual checklist's universe-size plausibility (1,000-1,500)
is deliberately not claimed yet.

### Separate from the fixture, on purpose

`universe_candidate_pool` (migration 016) is the S1 analogue of
`fixture_manifest`, kept as a different table so Phase F's frozen, already-
passed 10/10 exit criteria never silently grow in scope. `pipeline/universe/
pool.py` gives F3/F4/F5's ingestion scripts a `--pool <version>` flag that
selects from the pool instead of the fixture; the default (fixture-only)
behaviour is untouched.

### Entry rules, retention rules, and why oscillation cannot happen

Entry is the higher bar (price ≥ $3.00, market cap ≥ $300M, 60-day ADV ≥ $5M,
8 consecutive quarters of XBRL, listed NYSE/Nasdaq/NYSE American, common
stock, a CIK that files 10-K/10-Q). Retention is a strictly lower bar (price ≥
$2.50, market cap ≥ $250M, ADV ≥ $4M), enforced by both a config validation
and an automated test. The mechanism that prevents flapping: **a security is
only ever judged against entry once**, to get in. Every check after that —
daily or monthly — uses retention instead. A security that dips just below
entry but stays above retention never sees the entry bar again, so it cannot
enter-exit-enter around that line. It can only formally exit by failing
retention for a full hysteresis window (`universe_retention_hysteresis_days`,
20 trading days — no number was specified in the brief; 20 matches the
monthly membership cadence itself, since a formal exit can never happen faster
than the interval between monthly decisions anyway).

Two run types: `daily_safety` can suspend an included member to `watch`
immediately (stale price, no recent bar, severe new dilution evidence) and
advances the hysteresis counter one trading day at a time, but never changes
formal membership. Only `monthly_membership` may enter or exit a security,
and only monthly runs write to `universe_membership_changes`, the append-only
change log.

### Continued monitoring after exit

`membership.securities_requiring_monitoring()` unions current official
members with every security carrying an open or `pending_resolution` paper
position. Leaving the universe never stops monitoring a name that still has
capital in it — a security can be `excluded` in the latest snapshot and still
appear in this set.

### Two real bugs, found only once real data was used

Both were invisible against the 50-fixture and would have stayed invisible
against synthetic test data alone — this is the concrete payoff of running a
real (if bounded) sample before scaling further.

1. **`(cik, share_class)` is not a unique security key.** `share_class` is
   NULL for both a company's common stock and its preferred series —
   `classify.py`'s `extract_share_class` only recognises "Class X" wording,
   not preferred series letters. Discovering ABR (Arbor Realty Trust common)
   alongside the fixture's already-loaded ABR$D (Series D preferred, same
   CIK, same NULL share_class) silently merged the two into one `security_id`
   the first time both existed under the same CIK.
   `universe/load_fixture.find_existing_security` now matches on
   `(cik, share_class, security_type)`; `pool_loader.py` uses the same fixed
   function rather than a second copy.
2. **A `dei:EntityCommonStockSharesOutstanding` instant of exactly 0 is a
   real, filed value SEC preserves verbatim** — confirmed in HVT.A's own
   filing history, accession `0000216085-12-000014` from 2012. Point-in-time
   share resolution treated it as "present" (zero is present, not falsy, is
   the codebase's own rule for every other input), which propagated a $0
   market cap and a $0-numerator P/E for every later knowledge date with no
   closer share count — exactly the "never zero for absent data" F12 check 9
   exists to catch, and it did: check 9 failed against the real database
   after the S1 fundamentals run. `FactIndex.resolve_instant_asof` now skips
   any non-positive instant per-row rather than accepting the first match,
   so it finds the next genuinely positive one instead. Unlike the debt,
   current-ratio and interest-coverage zero rules the brief specifies, a real
   company cannot have zero shares outstanding — the zero is never a
   legitimate value for this one input, not merely a case needing a
   different treatment.

Both fixes ship with regression tests reproducing the exact real-data shape
that exposed them (`pipeline/tests/test_universe.py`,
`pipeline/tests/test_fundamentals.py`).

### Real result on the 200-security sample plus the fixture

250 securities evaluated (200 sample + 50 fixture), 125 included, 125
excluded, all 50 fixture securities accounted for. Exclusion reasons: 52
market cap, 41 no 10-K/10-Q on file, 22 price, 6 ADV, 2 security_type, 1
exchange, 1 XBRL depth (a genuine fiscal-year-transition stub-period gap in
FERG's real SEC data, not a bug — verified against the raw ingested facts).

### `/universe`

Membership, status and the reason for every security in the latest monthly
snapshot; an exclusion-reason breakdown; the monthly membership change log.
`watch` and `excluded` both require a non-null reason — migration 016
rebuilt `universe_snapshots`' CHECK to cover `watch` too, since the
migration-002 version only required one for `excluded`.

## S2: scaled ingestion (migration 017)

```bash
pipeline/.venv/Scripts/python.exe pipeline/orchestrate/run.py --tier all --pool <version> --batch-id <id>
```

No new ingest logic. `pipeline/orchestrate/run.py` calls the SAME per-item
functions F3-F5/insider/S1 already use (`ingest_securities`, `ingest_security`,
`ingest_company`, `compute_for_security`, `compute_snapshot`), adding only what
they lacked for real scale: a per-item transaction (so a kill loses at most
one item, not the whole batch), resumability via `orchestration_progress`
keyed by a caller-supplied `batch_id`, a circuit breaker that aborts a tier
after 5 consecutive failures, and one `pipeline_runs` row per tier per
invocation. Re-invoking with the same `batch_id` skips items already
`success` and retries `failed` ones — proven by fault injection
(`pipeline/tests/test_orchestrate.py`), not a live kill test: this platform's
`kill -9` from Git Bash does not reliably reach a Windows `python.exe`
subprocess, and a deterministic raised-exception-mid-batch is the same
property under test either way.

### Two real runs, not a synthetic benchmark

1. **250 securities** (the S1 pool + fixture), all four tiers, **zero
   failures**, 48.7 minutes. Form 4 dominates (44 of the 48.7 minutes) — each
   company needs a submissions fetch plus every Form 4 document, one at a
   time under the SEC's own rate ceiling. XBRL was mostly cache hits
   ("payload unchanged") since S1 had already ingested most of this pool.
2. **700 new securities** (`s2-slice-v1`, a fresh stride sample), all four
   tiers, **zero failures**, 259.7 minutes (4h20m). Same shape: prices ~5
   min, Form 4 ~3h2m, XBRL ~1h8m (now doing real work, not cache hits),
   universe <1 min.

Both runs are real network activity against live SEC and Yahoo endpoints, at
the scale the manual checklist asks for ("coverage above 80%", "no repeated
consecutive failures") — not a mocked or synthetic stand-in.

### Two more bugs, found only at this scale

1. **`add_listing` opened a second "current" listing window** instead of
   replacing the first. The PK is `(security_id, symbol, valid_from)`;
   re-running pool discovery on a LATER date for an already-current symbol
   (MSFT, PG, CAT, and ten others) inserted a new `valid_to IS NULL` row
   alongside the old one rather than over it, since `valid_from` differed.
   Every query assuming "at most one current listing per security" — which
   is most of them — would have silently cartesian-duplicated these
   securities the moment the pool was re-run a day later. Fixed:
   `add_listing` now closes any other open window first (no-op if the symbol
   is already current, closes-then-opens on a genuine symbol change). Found
   and repaired before the 700-security run, not after.
2. Not a bug: `fundamentals/compute.py` was never one of S2's four named
   tiers (prices, Form 4, XBRL, universe), so per-metric fundamentals
   coverage read 21% right after the 700-security run — correct, since only
   raw XBRL facts had been ingested, not the derived layer on top. Running
   `fundamentals/compute.py --pool s2-slice-v1` (pure local computation, the
   facts were already there — no new network calls) brought it to 74%. The
   remaining gap is the same securities that have no XBRL at all (SPY and
   several closed-end funds, confirmed via S1's investigation), not a new
   issue.

### Real coverage, full population (937 securities: fixture + both pools)

prices 99% (932/937), Form 4 87% (816/937), XBRL 94% (885/937), fundamentals
74% (691/937, explained above). `ADIG` is the one security with zero data
anywhere (0/4 sources) — investigated directly: it has no yfinance price
history at all, a genuine thin/new-listing gap, not an ingestion failure
(`orchestration_progress` correctly recorded it as `success` with zero rows
returned, which is what "the source has nothing" looks like — not something
to keep retrying).

### `/health` coverage reporting

Per source and per metric coverage, null reasons by metric (from
`missing_fields_json`), a price-staleness distribution, and the 20
worst-covered securities with what's missing for each — computed against the
latest monthly snapshot's full population, included and excluded alike.

## S3: calibration report (migration 018)

```bash
pipeline/.venv/Scripts/python.exe pipeline/scoring/compute.py --pool <version> [--pool <version>...]
pipeline/.venv/Scripts/python.exe pipeline/calibration/report.py
```

Signal-frequency only. **No return, price-after-selection, or performance
data appears anywhere in this report** — enforced by
`pipeline/tests/test_calibration.py`, which parses `report.py`'s own AST for
any reference to `exit_price`, `gross_pnl`, `net_pnl`, `pnl_pct`,
`paper_positions`, `benchmark_positions` or execution-outcome tables.

The candidate-rate simulation reuses `selection.rules.select()` unmodified —
the exact function F10's real weekly run calls — applied hypothetically at
each threshold (50 to 90, step 5) against today's already-computed scores. It
is a single point-in-time simulation, not a multi-week historical backtest:
the system has no history of past weekly score snapshots to replay, only
today's. "Estimated weeks to 100 closed" is pure arithmetic from the
candidate rate and each horizon's fixed max-hold length (a frozen protocol
parameter), never anything about what a position actually did.

Two fixes were needed to make F8's scoring reach the S1/S2 pool population at
all, both scoped by extending existing lookups rather than rewriting them:
`universe_rows`/`ensure_snapshot` gained a `pool_versions` parameter (mirroring
every other `--pool` addition since S1, and made the snapshot-reuse lookup
pool-aware so a pool run and a fixture run at the same score_date/config_hash
don't collide); and `insider_inputs`' "was Form 4 ingestion attempted for this
security" check was hardcoded to `fixture_manifest`, so every pool security
silently read "Insider coverage unknown" regardless of real coverage — it now
checks against the actual scored population (`attempted_ids`, built from
`members`), and separately recognises `orchestrate_form4` (S2's per-item
equivalent of a plain `insider` run) as valid completion evidence.

**Known limitation, stated rather than hidden — and larger than "mostly"**:
S1's pool discovery never populated `sic_code` for pool securities (only the
fixture loader does). Checked directly: 887 of 896 scored securities (99%)
share one undifferentiated `SIC-UNKNOWN` bucket; the remaining 9 real-cohort
securities sit below the 10-security blend-weight floor too. Cohort blending
is effectively **inert**, not merely degraded, for this population —
industry-relative percentiles don't meaningfully exist yet. **Any threshold
read from this run is provisional** and must be re-verified once `sic_code`
is populated and the universe is closer to full scale; S4 must not freeze a
`composite_threshold` from this data.

### `/calibration`

Rankable/withheld breakdown, composite and component histograms, per-submetric
percentile distributions from stored `explanation_json` (nothing
recomputed), cohort sizes and per-metric valid-observation counts, and the
threshold sweep table. The "Calibration data — non-official. No return
information is used or displayed." banner is permanent on this page.

## S6: unattended scheduling (`pipeline/scheduler/`)

No new pipeline logic. `daily.py`, `weekly.py`, `monthly.py` sequence the
same CLI scripts documented above -- each one still runnable by hand exactly
as shown in its own section -- and add only what running several of them
unattended needs: per-stage failure isolation, a plain-text log per run, a
`pipeline_runs` row per scheduler invocation (so it shows up in `/health`'s
run history the same way any stage already does), and missed-run detection.

### Cadence

| Job | Runs | What it calls |
|---|---|---|
| `daily.py` | every day, after US close | prices, Form 4, XBRL + fundamentals, scoring, risk flags, execution (position monitoring/exit eval), then a read-only data-health check and a severe-risk-flag scan |
| `weekly.py` | once per US trading week | `selection/compute.py` (official run) |
| `monthly.py` | once a month | `orchestrate/run.py --tier universe --run-type monthly_membership` (hysteresis lives in `universe/membership.py::compute_snapshot`, untouched) |

`daily.py` never calls selection -- it only ever runs from `weekly.py` --
so a blocked or delayed weekly run cannot stop daily monitoring, and a
daily-stage failure (`scheduler/common.py::run_stage`, which never raises)
cannot stop the remaining daily stages either.

"Fires exactly once per trading week" is not enforced by `weekly.py` itself;
it is enforced two layers down and would hold even if the trigger fired
twice: `trading_calendar.latest_complete_week` refuses an incomplete week,
and `research_candidates.candidate_id` is deterministic per week, so a
re-run cannot duplicate it (`test_candidate_id_is_deterministic_so_a_rerun_cannot_duplicate`).

### Logs

One file per run, under `data/logs/`: `daily-YYYY-MM-DD.log`,
`weekly-YYYY-MM-DD.log`, `monthly-YYYY-MM-DD.log` (the date is the
invocation date, not a week/month key). Each daily log's sections: pool
used, each stage's stdout/stderr tail, the read-only data-health report,
every `severity='high'` risk flag logged today, positions opened/closed
today, and open `pending_resolution` positions.

### Missed-run detection

Each job checks the newest existing log for its own type before doing
anything else. A gap bigger than its expected period (1 day / 7 days / ~30
days, `scheduler/common.py::missed_run_dates`) is written into that run's
log under `MISSED RUNS DETECTED` **and** recorded as its own
`pipeline_runs` row (`stage='scheduler_<job>_missed'`, `status='failed'`)
so it is visible on `/health` even if nobody reads the log file. A run that
fires and finds the week/month not yet complete (see above) still writes
its log -- "missed" means the scheduled process never ran at all (machine
off, task disabled), not "ran and correctly declined."

### A source failure never blanks the screener

`selection/compute.py` already suppresses every considered security as
`stale_source` (and writes zero candidates) when a required source fails
its freshness check -- that part needed no change. What did: `/candidates`
used to always render the *newest* selection run, so a blocked week
displayed as an empty page, indistinguishable from "0 candidates was a
fair result." `web/lib/db.ts::getPublishedSelectionRun` now picks the
newest run that was **not** an all-`stale_source` run, and the page renders
a warning naming the failed source when that differs from the newest
attempt -- see `web/tests/candidates-page.test.tsx`'s
`"preserves the last published screener..."` test.

### Windows Task Scheduler

This machine's timezone is Singapore Standard Time (UTC+8, no DST). US
market close (4:00pm ET) lands at 4:00am SGT (EDT) or 5:00am SGT (EST) the
next calendar day depending on the time of year -- the trigger times below
are chosen with margin on both sides of that, not tied to ET at all; each
script is otherwise timezone-agnostic (everything it reads/writes is UTC).

Run from an elevated PowerShell or Command Prompt (the tasks run under
whichever account creates them, and only while that account is logged in --
add `/RU` and `/RP` to a command below, which will prompt for that
account's password, to run whether logged on or not):

```bash
schtasks /create /tn "Stockbot\Daily" /sc daily /st 06:00 /f /tr "\"C:\Users\USER\stockbot\pipeline\.venv\Scripts\python.exe\" \"C:\Users\USER\stockbot\pipeline\scheduler\daily.py\""

schtasks /create /tn "Stockbot\Weekly" /sc weekly /d SUN /st 06:00 /f /tr "\"C:\Users\USER\stockbot\pipeline\.venv\Scripts\python.exe\" \"C:\Users\USER\stockbot\pipeline\scheduler\weekly.py\""

schtasks /create /tn "Stockbot\Monthly" /sc monthly /d 1 /st 07:00 /f /tr "\"C:\Users\USER\stockbot\pipeline\.venv\Scripts\python.exe\" \"C:\Users\USER\stockbot\pipeline\scheduler\monthly.py\""
```

Sunday 06:00 SGT for the weekly job is deliberate, not arbitrary: it is
`2026-08-08T22:00:00Z` in UTC terms -- Saturday, UTC-wise -- which is the
earliest point `trading_calendar`'s weekend-settled rule (`as_of` date is at
or after that week's Saturday) can pass for a week whose last session was
the preceding Friday. Moving this trigger earlier than Sunday 00:00 SGT
risks firing while the UTC date is still Friday, which would make the week
look incomplete and the run a permanent no-op, once a week, forever.

Verify or remove a task:

```bash
schtasks /query /tn "Stockbot\Daily" /v /fo list
schtasks /delete /tn "Stockbot\Daily" /f
```

Each script also runs by hand exactly like every other CLI in this doc,
which is how the manual-verification checklist below exercises them:

```bash
pipeline/.venv/Scripts/python.exe pipeline/scheduler/daily.py --as-of 2026-08-06
pipeline/.venv/Scripts/python.exe pipeline/scheduler/weekly.py --as-of 2026-08-08
pipeline/.venv/Scripts/python.exe pipeline/scheduler/monthly.py --as-of 2026-08-01
```

## O3: disclosures, evidence bands, change control (migration 023)

The final Phase O step: what makes the official experiment's results
honestly interpretable as evidence accumulates, and what happens when a
defect is found in it.

### Evidence bands (`web/lib/performance.ts::evidenceBand`)

Per horizon, from OFFICIAL closed positions only (never pre-launch,
provisional, or the "including pending @ zero" view):

| Closed | Band |
|---|---|
| < 30 | Insufficient evidence |
| 30-99 | Preliminary evidence |
| ≥ 100 **and** ≥ 12 months since `experiments.started_at` | Evaluation possible, not validated |
| ≥ 100, < 12 months | Preliminary evidence (the count alone is never enough) |

Rendered on `/performance` per horizon, even at zero -- an "Insufficient
evidence" band at n=0 is the correct, complete state on a freshly-launched
experiment, not a placeholder waiting for real content. The exact timeline
statement is shown once, under "Official results":

> The earliest status above preliminary evidence is 12 months after the
> official experiment begins, provided at least 100 positions have closed
> in that horizon.

### Scope disclosure (`web/components/scope-disclosure.tsx`)

Permanent, on `/performance` and `/candidates` (this project's screener):

> This system covers primarily profitable, mid-and-large-cap US operating
> companies. It excludes financial companies, REITs, ADRs, unresolvable
> multi-class issuers, and companies without positive earnings. Any result
> generalises only to that population.

### Footer (`web/components/site-footer.tsx`, every page via `app/layout.tsx`)

> Personal research tool. Not financial advice. Not a licensed financial
> advisor. Private, non-commercial use only - the price data source is not
> licensed for redistribution or commercial use.

### Bug-correction policy, `defect_log`, `/changelog`

`defect_log` (migration 023) is append-only in the same shape as
`experiments`/`frozen_config_lock`: core facts (`severity`, `description`,
`affected_strategy_version`, `affected_candidates_json`) are immutable once
set; `resolution`, `new_strategy_version` and `published_at` fill in as the
investigation concludes. `/changelog` shows only rows with `published_at`
set -- a defect mid-investigation is not public yet.

Three severities, matching the policy exactly:
- **cosmetic** -- no experiment restart, logged.
- **data_correction** -- no official candidate affected: audited, logged, continue.
- **material** -- an official candidate was affected: the affected
  `strategy_version` is marked `experiments.status = 'compromised'` (with
  `compromised_reason`, S5's existing column), every record stays exactly as
  it was, the defect is published on `/changelog`, and a new `strategy_version`
  begins a separately-reported experiment. `frozen_config_lock` and
  `defect_log.new_strategy_version` both FK to the new version, so the chain
  from "what broke" to "what replaced it" is queryable, not just narrated.

**"Never silently rewritten" is enforced at the schema level, not by
convention.** `research_candidates` already had this (migration 011);
migration 023 gives `paper_positions` and `benchmark_positions` the same
guarantee they were missing -- once `status = 'closed'`, the row can never
be UPDATEd or DELETEd again, full stop. This was a real, unintentional gap:
every closing UPDATE in `execution/compute.py` already only ever fires once
per position (immediately followed by `return`), so no existing code path
is affected -- the trigger only ever fires on an attempt to rewrite a result
already at rest. Verified in `pipeline/tests/test_defect_log.py` and
`pipeline/tests/test_experiments.py` (the compromised-experiment tests).

### Null-result requirement

`/performance` must be able to show "no detectable edge" as clearly as a
positive one. **Standing rule, not just for this session:** no feature,
weight, threshold, exclusion, or reporting change may be introduced in
response to disappointing results without going through the bug-correction
policy above first. A null result is a finding, not a bug.

### Language audit

Scanned `web/app` and `web/components` for "recommendation", "top pick",
"buy" (word-boundary), "signal to act": **zero instances found.** The
site's existing copy (candidates page, suppression labels, experiment
banner) already used "research candidate" / "candidate" consistently
throughout every prior phase -- nothing needed changing. Guarded going
forward by `web/tests/language-audit.test.ts`, an automated scan of the
same two directories, not a one-time manual check.

## Traps already paid for

These cost real debugging time. They are recorded so they are not rediscovered.

- **yfinance `auto_adjust=False` still returns SPLIT-adjusted OHLC.** The
  provider un-adjusts on the way in. Verified: NVDA traded near 1208.90 on
  2024-06-07; Yahoo reports 120.89.
- **Yahoo puts spin-off factors in the `Stock Splits` column.** Unclean ratios
  are filed as `other` with `requires_manual_review` and never adjust prices.
- **A company's EDGAR feed contains filings where it is the reporting OWNER,**
  not the issuer. Always resolve by the filing's own `issuerCik`.
- **`dei:EntityCommonStockSharesOutstanding` is a cover-page instant**, not
  dated at the fiscal period end.
- **The prior fiscal period must come from annual 10-K durations**, never from
  an arbitrary fact date; cover-page instants silently null every YoY metric.
- **Bank 424B2 filings are debt.** 126,659 of them in the fixture. Classify
  before scoring, and test debt language *before* equity language.
- **`semantic_hash` excludes the CIK**, so 71,910 values are shared by more than
  one company. Always group by `(cik, semantic_hash)`.
- **A file named `calendar.py` inside a package shadows the standard library's
  `calendar` module** when that package is run as a script — the package
  directory lands on `sys.path`, and `datetime.strptime` imports `calendar`
  internally. The collision surfaces far from its cause, as `module 'calendar'
  has no attribute 'day_abbr'`. `pipeline/selection/trading_calendar.py` is
  named to avoid it; do not rename it back.
- **Splitting a position mid-hold must move `entry_price`, not just stop and
  target.** Dividing only stop and target by the split ratio leaves the stored
  cost basis on the pre-split share count while the share count itself has
  already grown — `gross_pnl = shares * (exit - entry)` then compares a
  post-split share count against a pre-split entry price and manufactures a
  return out of the split alone. All three move together, preserving the same
  relative ordering (stop < entry < target) through the split.
- **`(cik, share_class)` is not a unique security identity.** It is NULL for
  both a company's common stock and its preferred series. Matching an
  existing security on that pair alone silently merges the two the first
  time both are loaded under one CIK. `security_type` must be part of the
  match — found via Arbor Realty Trust (ABR common vs. ABR$D preferred).
- **`add_listing` must close a security's OTHER open listing window before
  opening a new one**, even for the SAME symbol re-confirmed on a later date.
  The PK is `(security_id, symbol, valid_from)`, so re-discovering an
  already-current symbol on a new day inserted a second `valid_to IS NULL`
  row instead of replacing the first — found at S2 real-sample scale (MSFT,
  PG, CAT and ten others), before it reached the expensive 700-security run.
- **A `dei:EntityCommonStockSharesOutstanding` instant of exactly 0 is a real
  filed value**, not a sentinel for "unknown" — HVT.A's 2012 filing
  (accession `0000216085-12-000014`) tags it literally. Treating it as
  present propagates a $0 market cap and a $0 P/E. Point-in-time share
  resolution must skip non-positive instants and keep looking, not accept
  the first match.
