"""Going-concern detector. Narrow, phrase-anchored, source-linked.

This is NOT a language classifier. It looks for one specific construction, the
one that US GAAP and the auditing standards put in writing:

  ASC 205-40 requires management to disclose when "conditions or events raise
  substantial doubt about the entity's ability to continue as a going concern".
  AS 2415 / AU-C 570 require the auditor to say the same thing in an explanatory
  paragraph when the doubt is not alleviated.

So the anchor is the co-occurrence of two fixed phrases within a short window:

    "substantial doubt"  ...  "ability to continue as a going concern"

Anything looser -- "risk", "liquidity concerns", "may be unable to fund" -- is
deliberately NOT matched. A general detector would fire on ordinary risk-factor
prose in every small-cap 10-K and the flag would mean nothing.

Three ways a match is REJECTED, because the same two phrases appear in text
that is not a going-concern disclosure:

1. DENIAL. "did not raise substantial doubt", "no substantial doubt exists".
   These are the opposite statement and appear in clean filings.
2. DEFINITION. "if substantial doubt", "whether substantial doubt exists",
   "when conditions would raise substantial doubt". This is the accounting
   policy note describing the standard, not applying it. Almost every filer
   with an ASC 205-40 policy paragraph carries one.
3. THIRD PARTY. "the auditors of [an acquiree] included a going concern
   paragraph" -- guarded by requiring the subject to be the registrant itself
   ("its", "our", "the Company's", "the Company's ability").

One match is ACCEPTED but downgraded rather than rejected: ALLEVIATED doubt.
Under ASC 205-40 substantial doubt can be identified and then alleviated by
management's plans, which removes the auditor's paragraph but does not remove
the fact that the condition was identified. That is real, disclosed information,
so it fires at 'medium' with the alleviation quoted, rather than being silently
dropped.

Every match records the exact filing, the character offset in the extracted
text, and the passage itself, so a reader can open the document and find it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape

# Both anchors, as fixed phrases. \s+ between words because HTML routinely
# splits them across tags and non-breaking spaces.
_DOUBT = re.compile(r"substantial\s+doubt", re.IGNORECASE)
_GOING_CONCERN = re.compile(
    r"ability\s+to\s+continue\s+as\s+a\s+going\s+concern", re.IGNORECASE
)

# How far apart the two anchors may sit and still be one statement. The standard
# sentence is about 90 characters; 240 allows for an intervening clause without
# letting two unrelated sentences pair up.
MAX_ANCHOR_GAP = 240
# How much text to keep either side of the match as the quoted passage.
QUOTE_BEFORE = 320
QUOTE_AFTER = 320

_DENIAL = re.compile(
    r"(?:do(?:es)?|did|have|has|had|was|were|is|are)\s+not\s+(?:\w+\s+){0,3}"
    r"(?:raise|indicate|create|exist)"
    r"|no\s+(?:longer\s+)?substantial\s+doubt"
    r"|not\s+(?:been\s+)?(?:raise|raised|identified)",
    re.IGNORECASE,
)
_DEFINITION = re.compile(
    r"\b(?:if|whether|when|where|should|would|could|may|might)\b"
    r"(?:\W+\w+){0,6}\W+substantial\s+doubt"
    r"|substantial\s+doubt\s+(?:is|were)\s+to\s+(?:exist|arise)"
    r"|in\s+accordance\s+with\s+asc\s+205-40[^.]{0,120}substantial\s+doubt",
    re.IGNORECASE,
)
_ALLEVIATED = re.compile(
    r"alleviat\w*"
    r"|mitigat\w+\s+(?:the\s+)?substantial\s+doubt"
    r"|substantial\s+doubt\s+(?:has|had|was)\s+been\s+(?:resolved|removed)",
    re.IGNORECASE,
)
# The registrant must be the subject. A going-concern paragraph about an
# acquiree or an equity-method investee is not this company's flag.
_SELF_REFERENCE = re.compile(
    r"\b(?:its|our|the\s+Compan(?:y|y's|ies')|the\s+Registrant(?:'s)?"
    r"|we\s+(?:will|may|would|could)|the\s+Group's)\b",
    re.IGNORECASE,
)

_TAG = re.compile(r"<[^>]*>")
_ENTITY = re.compile(r"&(?:nbsp|#160|#xa0|#xA0);", re.IGNORECASE)
_WHITESPACE = re.compile(r"\s+")


def extract_text(markup: str) -> str:
    """HTML to plain text, preserving word boundaries where tags were."""
    # Non-breaking spaces become real spaces BEFORE tags are stripped, so a
    # word split by "&nbsp;" still reads as two words.
    text = _ENTITY.sub(" ", markup)
    # A tag becomes a space, not nothing: "going<br/>concern" must not become
    # "goingconcern" and stop matching.
    text = _TAG.sub(" ", text)
    # Entities are decoded AFTER tag stripping. Doing it first would turn a
    # literal "&lt;p&gt;" in the filing's own text into a tag and delete it.
    # Filings carry zero-width spaces, curly quotes and numeric references
    # throughout, and an undecoded "&#8203;" would appear verbatim in the
    # passage this flag quotes back to the reader.
    text = unescape(text)
    text = text.replace("​", " ").replace(" ", " ").replace("﻿", " ")
    return _WHITESPACE.sub(" ", text).strip()


@dataclass(frozen=True)
class GoingConcernMatch:
    detected: bool
    alleviated: bool
    offset: int | None
    passage: str | None
    rejected_reason: str | None
    candidates_examined: int


def scan_text(text: str) -> GoingConcernMatch:
    """Find the going-concern construction in already-extracted plain text."""
    candidates = 0
    rejections: list[str] = []

    for doubt in _DOUBT.finditer(text):
        window = text[doubt.end() : doubt.end() + MAX_ANCHOR_GAP]
        concern = _GOING_CONCERN.search(window)
        if concern is None:
            # The phrases also occur in the other order, e.g. "the ability to
            # continue as a going concern is subject to substantial doubt".
            lookback = text[max(0, doubt.start() - MAX_ANCHOR_GAP) : doubt.start()]
            if _GOING_CONCERN.search(lookback) is None:
                continue
            start = max(0, doubt.start() - MAX_ANCHOR_GAP)
            end = doubt.end()
        else:
            start = doubt.start()
            end = doubt.end() + concern.end()

        candidates += 1
        # A little context either side, because the denial or the conditional
        # usually sits just before the anchor.
        context = text[max(0, start - 160) : min(len(text), end + 160)]

        if _DENIAL.search(context):
            rejections.append("denial ('...did not raise substantial doubt')")
            continue
        if _DEFINITION.search(context):
            rejections.append("accounting-policy definition, not an assertion")
            continue
        if not _SELF_REFERENCE.search(context):
            rejections.append("subject is not the registrant")
            continue

        passage = text[max(0, start - QUOTE_BEFORE) : min(len(text), end + QUOTE_AFTER)]
        return GoingConcernMatch(
            detected=True,
            alleviated=bool(_ALLEVIATED.search(context)),
            offset=start,
            passage=passage.strip(),
            rejected_reason=None,
            candidates_examined=candidates,
        )

    reason = None
    if rejections:
        reason = "; ".join(sorted(set(rejections)))
    return GoingConcernMatch(False, False, None, None, reason, candidates)


# ------------------------------------------------------------------ fetching

_CHUNK = 262_144
# Overlap carried between chunks. The construction spans at most MAX_ANCHOR_GAP
# plus the quoted context either side, so this is comfortably larger than any
# passage that could straddle a chunk boundary.
_CARRY = MAX_ANCHOR_GAP + QUOTE_BEFORE + QUOTE_AFTER + 1024


@dataclass(frozen=True)
class ScanResult:
    match: GoingConcernMatch | None
    error: str | None
    bytes_read: int


def scan_stream(sec, url: str) -> ScanResult:
    """Stream the document and scan as it arrives. There is NO size limit.

    An earlier version buffered the whole document behind a 12 MB cap and
    reported truncation as an unknown. That turned JPMorgan's 10-K -- which is
    genuinely larger than the cap -- into "could not determine" when the answer
    was in fact knowable. Scanning incrementally removes the cap entirely: only
    a small overlap window is ever held in memory, so document size stops
    mattering and no honest answer is lost to a buffer limit.

    Chunk boundaries are handled twice over. Raw bytes are split at the last
    complete tag so a `<font` opened in one chunk is not treated as text, and
    the last _CARRY characters of extracted text are prepended to the next
    window so a passage straddling a boundary is still matched exactly once.
    """
    pending_raw = ""
    carry_text = ""
    consumed_chars = 0   # extracted characters already scanned and dropped
    total_bytes = 0
    candidates = 0

    def flush(raw: str, final: bool) -> GoingConcernMatch | None:
        nonlocal carry_text, consumed_chars, candidates
        extracted = extract_text(raw)
        if not extracted:
            return None
        window = f"{carry_text} {extracted}" if carry_text else extracted
        result = scan_text(window)
        candidates += result.candidates_examined
        if result.detected:
            # Offsets are reported in the extracted text of the whole document.
            offset = consumed_chars + (result.offset or 0)
            return GoingConcernMatch(
                True, result.alleviated, offset, result.passage, None, candidates
            )
        if final:
            return None
        keep = window[-_CARRY:]
        consumed_chars += len(window) - len(keep)
        carry_text = keep
        return None

    try:
        sec.limiter.acquire()
        with sec.session.get(url, timeout=180, stream=True) as response:
            response.raise_for_status()
            for chunk in response.iter_content(_CHUNK):
                total_bytes += len(chunk)
                buffer = pending_raw + chunk.decode("utf-8", errors="ignore")
                tail = ""
                # Never split inside a tag: hold back from the last unmatched '<'.
                cut = buffer.rfind("<")
                if cut > buffer.rfind(">"):
                    tail, buffer = buffer[cut:], buffer[:cut]
                # Never split inside a WORD either. Windows are joined with a
                # space, so a chunk ending mid-word would turn "substantial"
                # into "substan tial" and the anchor would stop matching. Cut
                # back to the last whitespace or closing tag instead.
                safe = max(
                    buffer.rfind(" "), buffer.rfind("\n"),
                    buffer.rfind("\t"), buffer.rfind("\r"), buffer.rfind(">"),
                )
                if safe >= 0:
                    tail = buffer[safe + 1:] + tail
                    buffer = buffer[: safe + 1]
                else:
                    tail, buffer = buffer + tail, ""
                pending_raw = tail
                found = flush(buffer, final=False)
                if found is not None:
                    return ScanResult(found, None, total_bytes)
            found = flush(pending_raw, final=True)
            if found is not None:
                return ScanResult(found, None, total_bytes)
    except Exception as exc:  # noqa: BLE001
        return ScanResult(None, f"{type(exc).__name__}: {exc}", total_bytes)

    return ScanResult(
        GoingConcernMatch(False, False, None, None, None, candidates), None, total_bytes
    )
