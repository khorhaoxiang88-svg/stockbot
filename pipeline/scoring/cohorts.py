"""SIC-derived cohorts. These are NOT GICS sectors and must never be labelled so.

GICS is a licensed classification maintained by S&P and MSCI. Nothing in this
system has ever seen a GICS code; every industry grouping here is derived from
the SEC's own SIC code on the securities table. Calling the result "sector" or
"GICS" would misrepresent where the number came from, so the cohort id carries
the "SIC-" prefix and the label says "SIC division" in words.

The groupings are the standard SIC divisions, unmodified. Splitting them further
would be an invention with no source behind it, and the blend weight already
protects a thin cohort: below ten valid observations of a metric the cohort
percentile carries zero weight.

Division H (60-67, Finance / Insurance / Real Estate) is present for
completeness only. Those securities carry model_applicable = 0 from F5 and are
never ranked, so no cohort population is ever built from them.
"""

from __future__ import annotations

# (low, high) inclusive on the two-digit SIC major group, division letter, name.
_DIVISIONS: tuple[tuple[int, int, str, str], ...] = (
    (1, 9, "A", "Agriculture, Forestry and Fishing"),
    (10, 14, "B", "Mining"),
    (15, 17, "C", "Construction"),
    (20, 39, "D", "Manufacturing"),
    (40, 49, "E", "Transportation, Communications, Electric, Gas and Sanitary Services"),
    (50, 51, "F", "Wholesale Trade"),
    (52, 59, "G", "Retail Trade"),
    (60, 67, "H", "Finance, Insurance and Real Estate"),
    (70, 89, "I", "Services"),
    (91, 99, "J", "Public Administration"),
)

UNCLASSIFIED = "SIC-UNKNOWN"


def cohort_for_sic(sic_code: str | None) -> tuple[str, str]:
    """(cohort_id, human label) for a SIC code. Never raises, never guesses."""
    if not sic_code:
        return UNCLASSIFIED, "No SIC code on file"
    digits = "".join(ch for ch in str(sic_code).strip() if ch.isdigit())
    if not digits:
        return UNCLASSIFIED, "No SIC code on file"
    major = int(digits[:2].zfill(2))
    for low, high, letter, name in _DIVISIONS:
        if low <= major <= high:
            return f"SIC-{letter}", f"SIC division {letter} - {name}"
    return UNCLASSIFIED, f"SIC {digits} falls outside the published divisions"


def cohort_label(cohort_id: str) -> str:
    for _, _, letter, name in _DIVISIONS:
        if cohort_id == f"SIC-{letter}":
            return f"SIC division {letter} - {name}"
    return "Unclassified (no SIC division)"
