"""
tests/unit/test_anomaly_detection.py — Unit tests for observability/anomaly_detection.py

Coverage targets:
  - simple_threshold(): high / low / within-range, direction, pct_deviation,
    zero-baseline guard, pct-range guard, check_low=False suppression
  - check_multiple(): mixed-breach batch, all-breached, all-clear, empty dict

Design:
  - Pure unit tests; no database, no network, no file I/O
  - structlog noise suppressed via configure() to a NullLogger in conftest
    (or simply not asserted; output captured by capfd if needed)
"""
from __future__ import annotations

import pytest

from observability.anomaly_detection import (
    ThresholdResult,
    check_multiple,
    simple_threshold,
)


# ---------------------------------------------------------------------------
# simple_threshold — breach detection
# ---------------------------------------------------------------------------


class TestSimpleThresholdHighBreach:
    def test_returns_threshold_result_type(self) -> None:
        result = simple_threshold(current=1.5, baseline=1.0, pct=0.20)
        assert isinstance(result, ThresholdResult)

    def test_high_breach_detected(self) -> None:
        result = simple_threshold(current=1.5, baseline=1.0, pct=0.20)
        assert result.breached is True
        assert result.direction == "high"

    def test_high_breach_pct_deviation_positive(self) -> None:
        result = simple_threshold(current=1.5, baseline=1.0, pct=0.20)
        assert result.pct_deviation == pytest.approx(0.5, abs=1e-4)

    def test_high_breach_message_contains_label(self) -> None:
        result = simple_threshold(current=2.0, baseline=1.0, pct=0.20, label="error_rate")
        assert "error_rate" in result.message

    def test_exact_threshold_boundary_no_breach(self) -> None:
        # current == baseline * (1 + pct) is NOT a breach (strict >)
        result = simple_threshold(current=1.2, baseline=1.0, pct=0.20)
        assert result.breached is False
        assert result.direction is None

    def test_just_above_threshold_is_breach(self) -> None:
        result = simple_threshold(current=1.201, baseline=1.0, pct=0.20)
        assert result.breached is True
        assert result.direction == "high"


class TestSimpleThresholdLowBreach:
    def test_low_breach_detected(self) -> None:
        result = simple_threshold(current=0.5, baseline=1.0, pct=0.20)
        assert result.breached is True
        assert result.direction == "low"

    def test_low_breach_pct_deviation_negative(self) -> None:
        result = simple_threshold(current=0.5, baseline=1.0, pct=0.20)
        assert result.pct_deviation == pytest.approx(-0.5, abs=1e-4)

    def test_check_low_false_suppresses_low_breach(self) -> None:
        result = simple_threshold(current=0.5, baseline=1.0, pct=0.20, check_low=False)
        assert result.breached is False
        assert result.direction is None

    def test_low_breach_message_contains_label(self) -> None:
        result = simple_threshold(current=0.1, baseline=1.0, pct=0.20, label="prediction_volume")
        assert "prediction_volume" in result.message


class TestSimpleThresholdWithinRange:
    def test_within_range_not_breached(self) -> None:
        result = simple_threshold(current=1.05, baseline=1.0, pct=0.20)
        assert result.breached is False
        assert result.direction is None

    def test_within_range_fields_populated(self) -> None:
        result = simple_threshold(current=1.05, baseline=1.0, pct=0.20, label="latency")
        assert result.current == pytest.approx(1.05)
        assert result.baseline == pytest.approx(1.0)
        assert "latency" in result.message


class TestSimpleThresholdGuards:
    def test_zero_baseline_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="baseline must be non-zero"):
            simple_threshold(current=1.0, baseline=0.0)

    def test_pct_zero_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="pct must be in"):
            simple_threshold(current=1.0, baseline=1.0, pct=0.0)

    def test_pct_above_one_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="pct must be in"):
            simple_threshold(current=1.0, baseline=1.0, pct=1.1)

    def test_pct_exactly_one_is_valid(self) -> None:
        result = simple_threshold(current=1.0, baseline=1.0, pct=1.0)
        assert isinstance(result, ThresholdResult)


# ---------------------------------------------------------------------------
# check_multiple — batch threshold evaluation
# ---------------------------------------------------------------------------


class TestCheckMultiple:
    def test_returns_dict_of_threshold_results(self) -> None:
        metrics = {"error_rate": (0.1, 0.05), "latency": (200.0, 150.0)}
        results = check_multiple(metrics)
        assert set(results.keys()) == {"error_rate", "latency"}
        assert all(isinstance(v, ThresholdResult) for v in results.values())

    def test_mixed_breach_and_no_breach(self) -> None:
        metrics = {
            "error_rate": (0.5, 0.05),   # +900% → breach
            "latency": (105.0, 100.0),   # +5%   → within range
        }
        results = check_multiple(metrics)
        assert results["error_rate"].breached is True
        assert results["latency"].breached is False

    def test_all_breached_returns_all_breached(self) -> None:
        metrics = {
            "a": (2.0, 1.0),    # +100% → breach
            "b": (0.1, 1.0),    # -90%  → breach
        }
        results = check_multiple(metrics)
        assert all(v.breached for v in results.values())

    def test_all_clear_returns_no_breaches(self) -> None:
        metrics = {
            "a": (1.01, 1.0),
            "b": (0.99, 1.0),
        }
        results = check_multiple(metrics)
        assert not any(v.breached for v in results.values())

    def test_empty_dict_returns_empty_dict(self) -> None:
        results = check_multiple({})
        assert results == {}

    def test_label_propagated_into_threshold_result(self) -> None:
        metrics = {"prediction_volume": (10.0, 1000.0)}
        results = check_multiple(metrics)
        assert "prediction_volume" in results["prediction_volume"].message

    def test_custom_pct_respected(self) -> None:
        # With pct=0.50, a 30% deviation should NOT breach
        results = check_multiple({"m": (1.3, 1.0)}, pct=0.50)
        assert results["m"].breached is False

    def test_check_low_false_propagated(self) -> None:
        # Drop by 50% — should NOT breach if check_low=False
        results = check_multiple({"m": (0.4, 1.0)}, check_low=False)
        assert results["m"].breached is False
