"""The restricted event taxonomy and field vocabulary, spec section 4.

Single source of truth for pipeline/news/extract.py's tool schema and for
pipeline/tests/test_news_ledger.py -- both import from here rather than
duplicating the list, so the DB CHECK constraint (migrations/024) and the
values the extractor is allowed to emit can never silently drift apart.
"""

from __future__ import annotations

# Every candidate label the extractor may assign. Scoring eligibility (which
# of these become NewsBonus/NewsPenalty inputs) is a Stage B decision, spec
# section 4 -- this list only says what Stage A is allowed to CLASSIFY.
EVENT_TYPES = (
    "binding_commercial_contract",
    "contract_termination",
    "lawsuit",
    "merger_acquisition",
    "financing_or_securities_issuance",
    "partnership_no_disclosed_commitment",
    "non_binding_loi_or_mou",
    "rumor_unnamed_source",
)

CONFIRMATION_TIERS = ("binding", "non_binding_loi", "rumor")

AMOUNT_TYPES = ("total", "annual", "minimum", "maximum", "estimated")

EXTRACTION_PROMPT_VERSION = "news-extract-v1"
