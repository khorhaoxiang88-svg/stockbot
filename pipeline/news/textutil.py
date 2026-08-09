"""HTML to plain text, own copy.

Deliberately not imported from pipeline.riskflags.going_concern: the point of
this package's isolation is that it never imports FROM a scoring-adjacent
module, so the "does News import anything from riskflags/scoring/selection"
question has one, structurally obvious answer. The logic itself is the same
small, well-understood transform every SEC HTML document needs.
"""

from __future__ import annotations

import re
from html import unescape

_TAG = re.compile(r"<[^>]*>")
_ENTITY = re.compile(r"&(?:nbsp|#160|#xa0|#xA0);", re.IGNORECASE)
_WHITESPACE = re.compile(r"\s+")


def extract_text(markup: str) -> str:
    """HTML to plain text, preserving word boundaries where tags were."""
    text = _ENTITY.sub(" ", markup)
    text = _TAG.sub(" ", text)
    text = unescape(text)
    text = text.replace("​", " ").replace("\xa0", " ").replace("﻿", " ")
    return _WHITESPACE.sub(" ", text).strip()
