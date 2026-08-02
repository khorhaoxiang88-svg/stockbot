"""Loader and validator for config.frozen.json.

The config file is frozen for Release 1. This module refuses to hand back a
config that is missing any required key, so a typo fails loudly at startup
instead of silently changing trading behaviour later.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.frozen.json"

# Every key that must exist. Keys beginning with "_" are documentation only.
REQUIRED_KEYS: tuple[str, ...] = (
    "strategy_version",
    "selection_rule_version",
    "protocol_version",
    "resolution_policy_version",
    "accrual_policy_version",
    "mapping_version",
    "composite_threshold",
    "max_candidates_per_selection",
    "max_per_cohort",
    "book_starting_nav",
    "position_notional",
    "max_open_positions_per_horizon",
    "horizons",
    "atr_window",
    "stop_atr_multiple",
    "target_atr_multiple",
    "gap_cancel_atr",
    "slippage_bps_high_liquidity",
    "slippage_bps_mid_liquidity",
    "cohort_blend_target",
    "cohort_blend_floor",
    "dilution_disqualify",
    "current_ratio_cap",
    "interest_coverage_cap",
    "high_leverage_debt_ebitda",
    "exit_cooldown_days",
    "gap_cancel_cooldown_days",
    "freshness_sla",
)

# Keys allowed to be null right now. Each must be filled before Release 1 ships.
PLACEHOLDER_KEYS: frozenset[str] = frozenset({"composite_threshold"})

VERSION_KEYS: tuple[str, ...] = (
    "strategy_version",
    "selection_rule_version",
    "protocol_version",
    "resolution_policy_version",
    "accrual_policy_version",
    "mapping_version",
)


class ConfigError(ValueError):
    """Raised when the frozen config is missing, malformed, or incomplete."""


def load_config(path: Path | str = DEFAULT_CONFIG_PATH) -> Mapping[str, Any]:
    """Load config.frozen.json and validate it. Returns a read-only mapping."""
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"Config file not found: {path}")

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Config file {path} is not valid JSON: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"Config file {path} must contain a JSON object at the top level")

    validate_config(raw, source=str(path))
    return MappingProxyType(raw)


def validate_config(config: Mapping[str, Any], source: str = "config") -> None:
    """Raise ConfigError listing every problem found. Does not mutate config."""
    problems: list[str] = []

    missing = [key for key in REQUIRED_KEYS if key not in config]
    if missing:
        problems.append("missing required key(s): " + ", ".join(sorted(missing)))

    null_keys = [
        key
        for key in REQUIRED_KEYS
        if key in config and config[key] is None and key not in PLACEHOLDER_KEYS
    ]
    if null_keys:
        problems.append("key(s) set to null that may not be null: " + ", ".join(sorted(null_keys)))

    for key in VERSION_KEYS:
        value = config.get(key)
        if value is not None and not isinstance(value, (int, str)):
            problems.append(f"{key} must be an integer or string, got {type(value).__name__}")

    horizons = config.get("horizons")
    if horizons is not None:
        if not isinstance(horizons, list) or not horizons:
            problems.append("horizons must be a non-empty list of day counts")
        elif not all(isinstance(h, int) and h > 0 for h in horizons):
            problems.append("horizons must contain positive integers only")

    sla = config.get("freshness_sla")
    if sla is not None:
        if not isinstance(sla, dict):
            problems.append("freshness_sla must be an object mapping source name to max hours")
        else:
            sources = {k: v for k, v in sla.items() if not k.startswith("_")}
            if not sources:
                problems.append("freshness_sla must list at least one source")
            for name, hours in sources.items():
                if not isinstance(hours, (int, float)) or hours <= 0:
                    problems.append(f"freshness_sla.{name} must be a positive number of hours")

    positive_numbers = (
        "max_candidates_per_selection",
        "max_per_cohort",
        "book_starting_nav",
        "position_notional",
        "max_open_positions_per_horizon",
        "atr_window",
        "stop_atr_multiple",
        "target_atr_multiple",
        "gap_cancel_atr",
        "current_ratio_cap",
        "interest_coverage_cap",
    )
    for key in positive_numbers:
        value = config.get(key)
        if value is not None and (not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0):
            problems.append(f"{key} must be a positive number, got {value!r}")

    if problems:
        raise ConfigError(
            f"{source} failed validation:\n  - " + "\n  - ".join(problems)
        )


def pending_placeholders(config: Mapping[str, Any]) -> list[str]:
    """Placeholder keys still unset. Must be empty before Release 1 ships."""
    return sorted(key for key in PLACEHOLDER_KEYS if config.get(key) is None)


if __name__ == "__main__":
    cfg = load_config()
    print(f"Loaded {len(REQUIRED_KEYS)} required keys from {DEFAULT_CONFIG_PATH}")
    still_open = pending_placeholders(cfg)
    if still_open:
        print("Placeholders still unset: " + ", ".join(still_open))
    else:
        print("No placeholders outstanding.")
