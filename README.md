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

## Data sources

Both were verified against live responses on 2026-07-29.

| Source | Endpoint | Notes |
|---|---|---|
| SEC ticker map | `www.sec.gov/files/company_tickers.json` | Requires `SEC_USER_AGENT`. Max 10 requests/second; the client caps itself at 8. |
| SEC submissions | `data.sec.gov/submissions/CIK##########.json` | Company name, SIC code, exchanges, filing history. |
| SEC insider data | `www.sec.gov/files/structureddata/data/insider-transactions-data-sets/<q>_form345.zip` | Quarterly Form 3/4/5 tables. Used to compute insider clusters from `TRANS_CODE = 'P'`. |
| Nasdaq Trader | `www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt`, `otherlisted.txt` | Pipe-delimited. Drop the `File Creation Time:` footer row. |

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
