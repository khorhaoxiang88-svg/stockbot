"""The frozen config must load, and a missing key must fail loudly."""

import json

import pytest

from config_loader import (
    PLACEHOLDER_KEYS,
    REQUIRED_KEYS,
    ConfigError,
    canonical_value,
    governed_digest,
    load_config,
    pending_placeholders,
    validate_config,
)


def test_frozen_config_loads_with_every_required_key():
    cfg = load_config()
    for key in REQUIRED_KEYS:
        assert key in cfg, f"config.frozen.json is missing {key}"


def test_frozen_values_match_release_1_spec():
    cfg = load_config()
    assert cfg["max_candidates_per_selection"] == 5
    assert cfg["max_per_cohort"] == 2
    assert cfg["book_starting_nav"] == 100000
    assert cfg["position_notional"] == 1000
    assert cfg["max_open_positions_per_horizon"] == 100
    assert cfg["horizons"] == [20, 60]
    assert cfg["atr_window"] == 14
    assert cfg["stop_atr_multiple"] == 2.0
    assert cfg["target_atr_multiple"] == 4.0
    assert cfg["gap_cancel_atr"] == 1.0
    assert cfg["slippage_bps_high_liquidity"] == 5
    assert cfg["slippage_bps_mid_liquidity"] == 15
    assert cfg["cohort_blend_target"] == 50
    assert cfg["cohort_blend_floor"] == 10
    assert cfg["dilution_disqualify"] == 22
    assert cfg["current_ratio_cap"] == 5.0
    assert cfg["interest_coverage_cap"] == 50
    assert cfg["exit_cooldown_days"] == 10
    assert cfg["gap_cancel_cooldown_days"] == 3


def test_composite_threshold_is_a_declared_placeholder():
    cfg = load_config()
    assert "composite_threshold" in PLACEHOLDER_KEYS
    assert pending_placeholders(cfg) == ["composite_threshold"]


def test_config_is_read_only():
    cfg = load_config()
    with pytest.raises(TypeError):
        cfg["book_starting_nav"] = 1  # type: ignore[index]


@pytest.mark.parametrize("dropped", list(REQUIRED_KEYS))
def test_missing_key_raises_clear_error(dropped, tmp_path):
    cfg = dict(load_config())
    cfg.pop(dropped)
    path = tmp_path / "broken.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")

    with pytest.raises(ConfigError) as exc:
        load_config(path)
    message = str(exc.value)
    assert "missing required key" in message
    assert dropped in message


def test_null_non_placeholder_key_is_rejected():
    cfg = dict(load_config())
    cfg["position_notional"] = None
    with pytest.raises(ConfigError, match="may not be null"):
        validate_config(cfg)


def test_bad_horizons_rejected():
    cfg = dict(load_config())
    cfg["horizons"] = [20, -5]
    with pytest.raises(ConfigError, match="positive integers"):
        validate_config(cfg)


def test_bad_freshness_sla_rejected():
    cfg = dict(load_config())
    cfg["freshness_sla"] = {"prices_daily": 0}
    with pytest.raises(ConfigError, match="positive number of hours"):
        validate_config(cfg)


def test_missing_file_raises_clear_error(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.json")


def test_invalid_json_raises_clear_error(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid JSON"):
        load_config(path)


# ---------------------------------------------------- version-governed values


def _mutable_config() -> dict:
    """A plain dict copy, including the "_"-prefixed governance blocks."""
    import copy

    return copy.deepcopy(dict(load_config()))


def test_high_leverage_threshold_is_governed_by_strategy_version():
    cfg = load_config()
    governed = cfg["_governed_by"]["strategy_version"]
    assert "high_leverage_debt_ebitda" in governed
    # Everything governed must be a real required key, and the list must stay
    # sorted so a diff shows an addition rather than a reshuffle.
    assert governed == sorted(governed)
    for key in governed:
        assert key in REQUIRED_KEYS


def test_declared_placeholders_are_never_governed():
    cfg = load_config()
    for version_key, keys in cfg["_governed_by"].items():
        if version_key.startswith("_"):
            continue
        # Filling composite_threshold in Phase S is an expected event tracked in
        # _placeholders. Governing it would force a strategy_version bump for
        # something the config already declares is coming.
        assert not set(keys) & set(PLACEHOLDER_KEYS)


def test_recorded_digest_matches_the_current_governed_values():
    cfg = load_config()
    current = str(cfg["strategy_version"])
    recorded = cfg["_version_digests"]["strategy_version"][current]
    assert governed_digest(cfg, "strategy_version") == recorded


def test_changing_a_governed_value_without_a_bump_is_refused():
    cfg = _mutable_config()
    cfg["high_leverage_debt_ebitda"] = 5.0
    with pytest.raises(ConfigError, match="strategy_version is still 1"):
        validate_config(cfg)


def test_bumping_without_recording_a_digest_is_refused():
    cfg = _mutable_config()
    cfg["strategy_version"] = 2
    with pytest.raises(ConfigError, match="no digest recorded for strategy_version=2"):
        validate_config(cfg)


def test_a_bump_with_its_digest_recorded_is_accepted():
    cfg = _mutable_config()
    cfg["high_leverage_debt_ebitda"] = 5.0
    cfg["strategy_version"] = 2
    cfg["_version_digests"]["strategy_version"]["2"] = governed_digest(
        cfg, "strategy_version"
    )
    validate_config(cfg)  # must not raise


def test_earlier_version_digests_are_kept_as_history():
    cfg = load_config()
    # The map is a history, so a past version's meaning cannot be rewritten
    # without the change showing up in the diff.
    assert isinstance(cfg["_version_digests"]["strategy_version"], dict)
    assert "1" in cfg["_version_digests"]["strategy_version"]


def test_canonical_value_normalises_numbers_the_way_javascript_does():
    # 4.0 must render as "4", not "4.0", or the two loaders disagree.
    assert canonical_value(4.0) == "4"
    assert canonical_value(50) == "50"
    assert canonical_value(0.35) == "0.35"
    assert canonical_value(True) == "true"
    assert canonical_value(None) == "null"


def test_governing_an_unknown_key_is_refused():
    cfg = _mutable_config()
    cfg["_governed_by"]["strategy_version"] = ["not_a_real_key"]
    with pytest.raises(ConfigError, match="not required"):
        validate_config(cfg)
