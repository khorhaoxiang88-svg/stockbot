"""AI-assisted structured extraction over ingested 8-K/8-K-A text.

This is judgment-laden extraction feeding deterministic scoring, not neutral
fact retrieval (spec section 3) -- treated with the same rigor Form 4 parsing
received, scaled up. Nothing here writes a score, a candidate or a
suppression; Stage B (unbuilt) is the only consumer of anything written here,
and it does not exist yet.

One extraction attempt per accession (not yet ingested-into-events), covering
the primary document plus up to MAX_EXHIBITS exhibits. The model must always
return at least one event object: a real classification, or a single
is_abstain=true row explaining why nothing qualified. That guarantees
"processed, found nothing" and "never processed" stay distinguishable
(SELECT accession_no FROM news_filings WHERE accession_no NOT IN (SELECT
accession_no FROM news_events)) without adding a second status column --
abstain already is that state, spec section 3.

Never infer an unstated amount: amount_explicit and amount_stated are
validated here AND enforced by a DB CHECK (migrations/024) as a second,
independent backstop.

PROVIDER. Two interchangeable backends behind one EVENTS_SCHEMA and one
validate_event() -- swapping is one line, --provider anthropic or
NEWS_MODEL_PROVIDER=anthropic env var, nothing else in this file changes:

  * ollama (default, current): local, no API key, no cost. Calls Ollama's
    OpenAI-compatible endpoint (localhost:11434/v1/chat/completions,
    confirmed against Ollama's own docs 2026-08-09) using response_format
    JSON-schema structured output, NOT forced tool_choice -- Ollama's own
    docs list forced tool_choice as still not fully supported on smaller/
    local models, while structured JSON-schema output is the mechanism
    Ollama itself recommends as more reliable for exactly this need.
  * anthropic: forced tool_choice against the Anthropic API, exactly as
    originally built. Needs ANTHROPIC_API_KEY; untouched otherwise.

HONEST CAVEAT, not hidden: qwen2.5:7b is a materially smaller/weaker model
than Sonnet for the nuanced binding/non-binding/rumor judgment this task
needs. It is fine for proving the Stage A pipeline mechanism end to end; its
classification quality has NOT been validated against section 7's precision/
recall thresholds and must not be treated as a substitute for that once
Stage B is in scope. Every row records which model produced it
(extraction_model_version) specifically so this distinction is never lost.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

import requests

PIPELINE_DIR = Path(__file__).resolve().parent.parent
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

import migrate  # noqa: E402
from news.taxonomy import AMOUNT_TYPES, CONFIRMATION_TIERS, EVENT_TYPES, EXTRACTION_PROMPT_VERSION  # noqa: E402
from news.textutil import extract_text  # noqa: E402
from sec.payload_store import read_payload, utc_now  # noqa: E402
from universe.sec_client import load_dotenv_into_environ  # noqa: E402

# One line to swap back to Claude once API budget exists: change this
# default, or pass --provider anthropic / set NEWS_MODEL_PROVIDER=anthropic.
DEFAULT_PROVIDER = os.environ.get("NEWS_MODEL_PROVIDER", "ollama")
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_REQUEST_TIMEOUT = 300  # local CPU/GPU inference is slow; no retry, fail loud
DEFAULT_MODEL_BY_PROVIDER = {
    "ollama": "qwen2.5:7b",
    "anthropic": "claude-sonnet-5",
}
MAX_EXHIBITS = 2
MAX_CHARS_PER_DOCUMENT = 15000

# One JSON Schema, shared by both providers -- Anthropic's tool input_schema
# and Ollama's response_format.json_schema.schema take the identical shape.
# "strict"-style (every property in required, nullable via a type union,
# additionalProperties false on every object) because Ollama's structured
# output follows OpenAI's strict convention; a stricter ask costs Claude
# nothing since it can still emit null for a genuinely absent field.
EVENT_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "is_abstain": {"type": "boolean"},
        "abstain_reason": {"type": ["string", "null"]},
        "event_type_candidate": {
            "type": ["string", "null"],
            "enum": list(EVENT_TYPES) + [None],
        },
        "confirmation_tier": {
            "type": ["string", "null"],
            "enum": list(CONFIRMATION_TIERS) + [None],
        },
        "amount_explicit": {"type": "boolean"},
        "amount_stated": {"type": ["number", "null"]},
        "amount_type": {
            "type": ["string", "null"],
            "enum": list(AMOUNT_TYPES) + [None],
        },
        "currency": {"type": ["string", "null"]},
        "contract_duration_months": {"type": ["integer", "null"]},
        "annualization_method": {"type": ["string", "null"]},
        "includes_optional_extensions": {"type": ["boolean", "null"]},
        "supporting_passage": {
            "type": "string",
            "description": (
                "The literal excerpt the classification was drawn from. For an "
                "abstain, a short representative excerpt or the filing's own "
                "Item list -- never fabricated."
            ),
        },
        "source_document": {
            "type": "string",
            "description": "Which of the labelled documents below this came from.",
        },
    },
    "required": [
        "is_abstain", "abstain_reason", "event_type_candidate", "confirmation_tier",
        "amount_explicit", "amount_stated", "amount_type", "currency",
        "contract_duration_months", "annualization_method", "includes_optional_extensions",
        "supporting_passage", "source_document",
    ],
    "additionalProperties": False,
}

EVENTS_SCHEMA = {
    "type": "object",
    "properties": {
        "events": {"type": "array", "minItems": 1, "items": EVENT_ITEM_SCHEMA},
    },
    "required": ["events"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = f"""You extract structured candidate economic events from SEC 8-K filings for a
research ledger. This feeds deterministic downstream scoring, so precision matters more than
recall on any single filing.

Rules, all mandatory:
1. Never infer an amount that is not explicitly stated. If a dollar figure is not written in
   the text, amount_explicit must be false and amount_stated must be null.
2. Classify confirmation_tier as one of: binding (a signed, enforceable agreement),
   non_binding_loi (a letter of intent / MOU / non-binding term sheet), or rumor
   (unnamed-source or unconfirmed reporting). If you cannot confidently determine which,
   set is_abstain=true, leave event_type_candidate and confirmation_tier null, and explain
   why in abstain_reason.
3. event_type_candidate is one of: {', '.join(EVENT_TYPES)}.
4. supporting_passage must be the literal text you drew the classification from -- copy it,
   never paraphrase or invent it.
5. If the filing contains no candidate economic event at all (e.g. a director resignation,
   an auditor change with no dollar figure, a routine item with no commercial content),
   return exactly one event with is_abstain=true and abstain_reason explaining there was
   nothing to classify.
6. A filing may contain more than one candidate event; return one object per event.
7. Every field listed in the schema must be present in every event object. Use null for any
   field that does not apply -- never omit a field.
"""


def pending_filings(conn, limit: int | None) -> list[dict]:
    query = """
        SELECT nf.accession_no, nf.security_id, nf.form_type
          FROM news_filings nf
         WHERE nf.accession_no NOT IN (SELECT DISTINCT accession_no FROM news_events)
         ORDER BY nf.filed_date
    """
    rows = conn.execute(query).fetchall()
    rows = [dict(r) for r in rows]
    return rows[:limit] if limit else rows


def build_document_text(conn, accession_no: str) -> list[tuple[str, str]]:
    """[(document_name, plain_text)], primary first, then up to MAX_EXHIBITS exhibits."""
    docs = conn.execute(
        """
        SELECT document_name, role, payload_id FROM news_filing_documents
         WHERE accession_no = ?
         ORDER BY CASE role WHEN 'primary' THEN 0 ELSE 1 END, document_name
        """,
        (accession_no,),
    ).fetchall()
    selected = []
    exhibit_count = 0
    for doc in docs:
        if doc["role"] == "exhibit":
            if exhibit_count >= MAX_EXHIBITS:
                continue
            exhibit_count += 1
        raw = read_payload(conn, doc["payload_id"])
        text = extract_text(raw.decode("utf-8", errors="replace"))
        selected.append((doc["document_name"], text[:MAX_CHARS_PER_DOCUMENT]))
    return selected


def validate_event(event: dict) -> str | None:
    """Mirrors the migration's CHECK constraints. Returns an error message, or None."""
    is_abstain = bool(event.get("is_abstain"))
    event_type = event.get("event_type_candidate")
    tier = event.get("confirmation_tier")
    reason = event.get("abstain_reason")
    if is_abstain:
        if event_type is not None or tier is not None:
            return "abstain=true must carry no event_type_candidate or confirmation_tier"
        if not reason:
            return "abstain=true must carry a non-empty abstain_reason"
    else:
        if reason:
            return "abstain=false must not carry an abstain_reason"
        if tier not in CONFIRMATION_TIERS:
            return f"abstain=false requires confirmation_tier in {CONFIRMATION_TIERS}"

    explicit = bool(event.get("amount_explicit"))
    amount = event.get("amount_stated")
    if explicit and amount is None:
        return "amount_explicit=true requires a non-null amount_stated"
    if not explicit and amount is not None:
        return "amount_explicit=false requires a null amount_stated -- never infer an amount"

    if not event.get("supporting_passage"):
        return "supporting_passage is required"
    if not event.get("source_document"):
        return "source_document is required"
    return None


def _no_document_abstain() -> list[dict]:
    return [{
        "is_abstain": True,
        "abstain_reason": "no readable document text was ingested for this filing",
        "event_type_candidate": None, "confirmation_tier": None,
        "amount_explicit": False, "amount_stated": None, "amount_type": None,
        "currency": None, "contract_duration_months": None, "annualization_method": None,
        "includes_optional_extensions": None,
        "supporting_passage": "(no document text available)",
        "source_document": "(none)",
    }]


def _user_message(filing: dict, documents: list[tuple[str, str]]) -> str:
    body = "\n\n".join(f"=== DOCUMENT: {name} ===\n{text}" for name, text in documents)
    return f"Form {filing['form_type']}, accession {filing['accession_no']}. Documents:\n\n{body}"


def _extract_via_anthropic(model: str, filing: dict, documents: list[tuple[str, str]]) -> list[dict]:
    import anthropic

    client = anthropic.Anthropic()
    tool = {
        "name": "record_news_extraction",
        "description": (
            "Record every candidate economic event found in this 8-K filing's text. If "
            "nothing qualifies, return exactly one event with is_abstain=true."
        ),
        "input_schema": EVENTS_SCHEMA,
    }
    message = client.messages.create(
        model=model,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        tools=[tool],
        tool_choice={"type": "tool", "name": "record_news_extraction"},
        messages=[{"role": "user", "content": _user_message(filing, documents)}],
    )
    for block in message.content:
        if block.type == "tool_use" and block.name == "record_news_extraction":
            return block.input.get("events", [])
    raise RuntimeError(f"model returned no tool_use block for {filing['accession_no']}")


def _extract_via_ollama(model: str, filing: dict, documents: list[tuple[str, str]]) -> list[dict]:
    """response_format JSON-schema structured output over Ollama's OpenAI-compatible
    /v1/chat/completions endpoint. NOT tool_choice -- see module docstring."""
    response = requests.post(
        f"{OLLAMA_BASE_URL}/v1/chat/completions",
        json={
            "model": model,
            "temperature": 0,  # Ollama's own structured-output guidance: deterministic output
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _user_message(filing, documents)},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "record_news_extraction",
                    "schema": EVENTS_SCHEMA,
                    "strict": True,
                },
            },
        },
        timeout=OLLAMA_REQUEST_TIMEOUT,
    )
    if not response.ok:
        try:
            detail = response.json().get("error", {}).get("message", response.text)
        except (ValueError, AttributeError):
            detail = response.text
        raise RuntimeError(f"Ollama {response.status_code} for {filing['accession_no']}: {detail}")
    payload = response.json()
    content = payload["choices"][0]["message"]["content"]
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Ollama returned non-JSON content for {filing['accession_no']}: {content[:500]!r}"
        ) from exc
    return parsed.get("events", [])


def extract_filing(provider: str, model: str, filing: dict, documents: list[tuple[str, str]]) -> list[dict]:
    if not documents:
        return _no_document_abstain()
    if provider == "ollama":
        return _extract_via_ollama(model, filing, documents)
    if provider == "anthropic":
        return _extract_via_anthropic(model, filing, documents)
    raise ValueError(f"unknown provider {provider!r}: expected 'ollama' or 'anthropic'")


def write_events(conn, filing: dict, events: list[dict], model: str, stats: dict) -> int:
    accepted_at = conn.execute(
        "SELECT accepted_at FROM news_filings WHERE accession_no = ?",
        (filing["accession_no"],),
    ).fetchone()[0]

    written = 0
    for event in events:
        error = validate_event(event)
        if error:
            stats["invalid_events"] += 1
            stats.setdefault("invalid_examples", []).append(
                f"{filing['accession_no']}: {error}"
            )
            print(f"  REJECTED ({filing['accession_no']}): {error}", file=sys.stderr)
            continue
        conn.execute(
            """
            INSERT INTO news_events
                (event_id, security_id, accession_no, accepted_at, source_document,
                 extracted_at, is_abstain, abstain_reason, event_type_candidate,
                 confirmation_tier, amount_explicit, amount_stated, amount_type,
                 currency, contract_duration_months, annualization_method,
                 includes_optional_extensions, supporting_passage, passage_source_offset,
                 extraction_model_version, extraction_prompt_version, supersedes_event_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, NULL)
            """,
            (
                uuid.uuid4().hex, filing["security_id"], filing["accession_no"], accepted_at,
                event["source_document"], utc_now(),
                1 if event["is_abstain"] else 0, event.get("abstain_reason"),
                event.get("event_type_candidate"), event.get("confirmation_tier"),
                1 if event["amount_explicit"] else 0, event.get("amount_stated"),
                event.get("amount_type"), event.get("currency"),
                event.get("contract_duration_months"), event.get("annualization_method"),
                event.get("includes_optional_extensions"), event["supporting_passage"],
                model, EXTRACTION_PROMPT_VERSION,
            ),
        )
        written += 1
        stats["abstains" if event["is_abstain"] else "classified"] += 1
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Extract structured events from ingested 8-Ks")
    parser.add_argument("--db", default=str(migrate.DEFAULT_DB_PATH))
    parser.add_argument("--limit", type=int, default=None, help="max filings to process")
    parser.add_argument("--provider", default=DEFAULT_PROVIDER, choices=["ollama", "anthropic"])
    parser.add_argument("--model", default=None, help="defaults per provider if omitted")
    args = parser.parse_args(argv)

    model = args.model or DEFAULT_MODEL_BY_PROVIDER[args.provider]
    load_dotenv_into_environ()

    conn = migrate.connect(Path(args.db))
    stats = {"classified": 0, "abstains": 0, "invalid_events": 0, "filings_processed": 0}
    run_id = f"news-extract-{uuid.uuid4().hex[:12]}"

    try:
        filings = pending_filings(conn, args.limit)
        conn.execute("BEGIN")
        conn.execute(
            "INSERT INTO pipeline_runs (run_id, stage, started_at, status, code_version) "
            "VALUES (?, 'news_extract', ?, 'running', ?)",
            (run_id, utc_now(), f"{args.provider}:{model}"),
        )
        total_written = 0
        for filing in filings:
            documents = build_document_text(conn, filing["accession_no"])
            events = extract_filing(args.provider, model, filing, documents)
            written = write_events(conn, filing, events, model, stats)
            total_written += written
            stats["filings_processed"] += 1
            print(f"{filing['accession_no']} ({filing['form_type']}) -> {written} event row(s)")

        conn.execute(
            "UPDATE pipeline_runs SET status='success', finished_at=?, records_written=? "
            "WHERE run_id=?",
            (utc_now(), total_written, run_id),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()

    print(f"\n=== news extraction summary (provider={args.provider}, model={model}) ===")
    print(f"filings processed  : {stats['filings_processed']}")
    print(f"classified events  : {stats['classified']}")
    print(f"abstains           : {stats['abstains']}")
    print(f"invalid (rejected) : {stats['invalid_events']}")
    for example in stats.get("invalid_examples", [])[:10]:
        print(f"  rejected: {example}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
