"""The frozen config must load, and a missing key must fail loudly."""

import json

import pytest

from config_loader import (
    PLACEHOLDER_KEYS,
    REQUIRED_KEYS,
    ConfigError,
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
