# Portfolio simulator — specification (not yet implemented)

Status: **spec only**. No code exists for this yet. Do not build until this
spec is reviewed, and not before the currently-running daily scan, the S6
scheduler, CI, and the isolated test suite have all been confirmed green
(see the status report this spec ships alongside).

## What this is, in one sentence

A research-only calculator: given a hypothetical dollar amount and a risk
tier, show what a diversified hypothetical allocation across *this week's
actual published candidates* would look like, using the bot's own already-
frozen entry/stop/target math — nothing here is a new trading signal, a new
scoring model, or an instruction to act.

## What this is explicitly NOT

- Not personalized financial advice. Nobody's actual financial situation,
  goals, or risk tolerance is assessed — "risk tier" is a diversification
  parameter (below), not a suitability judgment.
- Not a portfolio the bot recommends. It's *this week's already-published,
  already-filtered candidates*, split by a disclosed arithmetic rule.
- Not connected to any brokerage, and never will be. No order can be placed
  from this page, directly or indirectly.
- Not a new strategy. It changes zero pipeline logic, zero scoring, zero
  selection rules, and reads `config.frozen.json`'s existing
  `stop_atr_multiple` / `target_atr_multiple` rather than defining new ones.
- Not able to promise a result: "target scenario" and "stop-loss scenario"
  are the same arithmetic the frozen protocol already applies to real paper
  positions, restated as hypothetical numbers for candidates that may not
  have an open position yet.

## Language constraints (binding)

Every string this feature renders must pass the existing
`web/tests/language-audit.test.ts` (no "recommendation", "top pick",
"signal to act", or the standalone word "buy") — same rule already applied
to every other page. Additional project-specific vocabulary this spec
commits to, so a future reviewer doesn't have to re-derive it:

| Say this | Not this |
|---|---|
| modeled entry reference | entry point / buy price |
| stop-loss scenario | stop / where to sell |
| target scenario | target / take profit |
| hypothetical allocation | your portfolio / your position |
| Wall Street analyst target (external) | our target / price target |

## Inputs

- **Amount**: a positive dollar figure the user types in. No minimum beyond
  `> 0`. No maximum. Never stored, never sent anywhere — request-scoped only
  (see "No persistence" below).
- **Risk tier**: `low` / `medium` / `high`, radio-button choice. Defined
  purely as a diversification/concentration rule (table below) — it does
  NOT change which candidates are eligible, and it does NOT imply higher-
  risk-tier positions are "riskier stocks." All tiers draw from the exact
  same candidate list.

## Candidate universe (what's eligible)

The newest **published** selection run only — `getPublishedSelectionRun()` +
`getCandidatesForRun()`, the same data `/candidates` already reads. Nothing
merely ranked, nothing suppressed, nothing hypothetically scored — only
candidates that survived every real eligibility rule and were actually
selected. If that set is empty (a legitimate, current, real state — see the
status report), the simulator says so and computes nothing, exactly like
`/candidates` already does for a zero-candidate week. It never substitutes
in ranked-but-not-selected stocks to avoid showing an empty result.

## Diversification rule (the risk tier's only job)

| Tier | Max positions used | Max % of amount in one position |
|---|---|---|
| low | up to 10 (or all available candidates, whichever is fewer) | 15% |
| medium | up to 6 (or all available) | 25% |
| high | up to 3 (or all available) | 40% |

Allocation within a tier is **equal-weight** across however many candidates
are actually used (`min(tier max, candidates available)`) — never a
conviction-weighted split. Equal-weight is the only allocation rule that
doesn't require the bot to express an opinion about which candidate it
"likes more"; the composite score already is that opinion, expressed once,
transparently, on `/candidates`.

If the published set has fewer candidates than the tier's max, every
available candidate is used and the leftover cap is simply unused headroom
— never forced to spread over non-existent candidates, never topped up with
lower-ranked stocks.

Cash not allocated (there is none, at equal-weight over all available
candidates, unless the per-position cap binds) is shown explicitly as
"unallocated," never silently dropped from the total.

## Per-position numbers, and the exact formula behind each one

For each candidate included in the hypothetical allocation:

- **Hypothetical shares**: `floor(position_dollar_amount / modeled_entry)`.
  Fractional-share leftover cash is shown, not hidden.
- **Modeled entry reference**: if a real `paper_positions` row already
  exists for this candidate (it may, since selection and execution both run
  against the same published candidates), use its actual `entry_price` and
  label it "actual recorded entry." If none exists yet, use the candidate's
  most recent adjusted close and label it "modeled reference (next-session
  open protocol not yet applied)" — matching R1-PROTOCOL-1.1's own
  documented entry mechanics (README § Execution under R1-PROTOCOL-1.1),
  never inventing a different entry rule for this feature.
- **Stop-loss scenario**: `entry - config.stop_atr_multiple * ATR(entry)`.
  Currently `entry - 2.0 * ATR`. Same ATR value R1-PROTOCOL-1.1 already
  freezes at entry (`atr_at_entry_basis`), read from the real stored
  position when one exists, or computed the same way execution/compute.py
  does when modeling a not-yet-executed candidate.
- **Target scenario**: `entry + config.target_atr_multiple * ATR(entry)`.
  Currently `entry + 4.0 * ATR`, same source as above.
- **Hypothetical dollar risk / reward**: shares × (entry − stop) and
  shares × (target − entry), shown as the two numbers that fall directly
  out of the stop/target above — not a separately invented figure.

Every one of these four numbers renders with the formula inline (not just
the result), e.g. "$186.40 (100 sh × $1.86 = 2.0 × ATR $0.93)" — satisfying
"explain the exact calculation behind every number" literally, not just in
a tooltip a reader has to hunt for.

## Wall Street analyst data (kept visibly separate)

If `getLatestAnalystSnapshot()` has a row for a candidate, its target range
(low/mean/high) renders in its own clearly-headed section per position —
"Wall Street analyst target (external, via Yahoo Finance)" — directly
adjacent to but never merged into the modeled entry/stop/target numbers
above. No arithmetic ever combines the two; a reader must never have to
guess which number came from which source.

## No persistence

This is a pure, stateless calculation on each request — amount and risk
tier are query parameters (`/portfolio-simulator?amount=...&risk=low`,
same GET-form pattern `/search` already uses), computed fresh from
`research_candidates` + `paper_positions` + `analyst_snapshots` + the live
adjusted price series, and rendered. No new table, no new migration, no
new write path anywhere in the pipeline. This keeps the feature trivially
impossible to confuse with a real logged position — there is nothing in the
database that reflects a "portfolio" ever having existed.

## Required disclosure copy (exact placement, not exact wording yet)

A `ScopeDisclosure`-style banner at the top of the page, before any number
renders, stating at minimum: this is a hypothetical calculation over
already-published research candidates, not personalized advice, not an
instruction to trade, and not connected to any brokerage. Exact copy to be
finalized with the same care `web/components/scope-disclosure.tsx`'s
existing text already received — not drafted casually inline in this spec.

## Tests required before/alongside implementation

- Pure-function unit tests (no DB) for the diversification-rule table above:
  every (tier, candidate-count) combination, including 0 candidates and
  candidate-count below the tier's max.
- A test asserting equal-weight allocation sums to ≤ amount, never over.
- A test asserting the stop/target formulas match `config.frozen.json`'s
  `stop_atr_multiple`/`target_atr_multiple` exactly, not a hardcoded 2.0/4.0
  — so a future config change can't silently desync this feature from the
  real protocol.
- A `web/tests/language-audit.test.ts`-style scan (or extend the existing
  one to cover this page) proving none of the constrained vocabulary above
  regresses.
- A test asserting the page renders and computes nothing (no positions, no
  error) when the published candidate set is empty.

## Open questions for review before implementation starts

1. Are the three diversification tiers (10/15%, 6/25%, 3/40%) the right
   numbers, or should they be tuned? They're a first defensible guess, not
   derived from anything in the existing frozen config.
2. Should a not-yet-executed candidate's "modeled entry reference" be
   allowed at all, or should the simulator only ever show numbers for
   candidates that already have a real `paper_positions` row (stricter,
   less useful the day candidates first publish, before execution has run)?
