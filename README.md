# stockbot

US stock research system. **Phase 1: skeleton only — no market data yet.**

What exists right now:

- a Python pipeline folder with its own virtual environment,
- a numbered migration system for the SQLite database,
- a frozen configuration file for Release 1,
- a Next.js web app with a `/health` page.

---

## Folder map

```
stockbot/
  config.frozen.json      Frozen Release 1 parameters. Version-controlled.
  .env.example            Template for .env. Copy it, never commit .env.
  .gitignore              Excludes .env and /data.
  migrations/             Numbered SQL, one .up.sql and one .down.sql each.
  pipeline/               Python side.
    .venv/                Virtual environment (not committed).
    requirements.txt
    migrate.py            Migration runner.
    config_loader.py      Loads and validates config.frozen.json.
    tests/                pytest suite.
  data/                   SQLite database and raw payloads. Never committed.
    stockbot.db
    raw/                  Raw source payloads land here, one folder per source.
  web/                    Next.js 15 App Router, TypeScript, Tailwind, shadcn/ui.
    app/health/page.tsx   The health page.
    lib/                  db, config, paths, time helpers.
    tests/                vitest suite.
```

## Rules this project follows

1. **Timestamps are stored in UTC.** Conversion to US Eastern happens only for
   display and market-calendar logic (`web/lib/time.ts`).
2. **Schema changes only ever go in a new numbered migration.** Never edit a
   migration that has already been applied.
3. **`config.frozen.json` is frozen for Release 1.** Changing a value means
   bumping the matching `*_version` key.
4. **`.env` and `/data/` are never committed.**

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

Run the Python tests:

```bash
pipeline/.venv/Scripts/python.exe -m pytest pipeline/tests -q
```

Run the web tests:

```bash
npm test --prefix web
```

Start the web app, then open <http://localhost:3000/health>:

```bash
npm run dev --prefix web
```

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

Migration 001 creates operations tables only: `pipeline_runs`, `source_health`,
`schema_migrations`. No market data tables exist yet.

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
- `freshness_sla` lists a per-source maximum staleness in hours. The F10
  freshness table was not supplied with the Phase 1 brief, so the source names
  and hour budgets there are a first pass and are flagged `_provisional` inside
  the JSON. Confirm them against F10 before any real source is wired up.
- The required-key list exists twice on purpose: `pipeline/config_loader.py` for
  Python and `web/lib/config.ts` for the web app. Adding a key means editing both.

## Dependency notes

- `better-sqlite3` is pinned to **12.x**. Version 13 dropped its prebuilt
  binaries and compiles from source, which needs Visual Studio build tools on
  Windows. 12.x ships a prebuilt binary and installs cleanly.
- `next.config.ts` lists `better-sqlite3` under `serverExternalPackages` so the
  native module is loaded by Node instead of being bundled.
