import numpy as np
import pandas as pd
import pytest

import risk_model as model


@pytest.fixture
def inputs():
    return {
        "buffett": 220,
        "shiller": 44,
        "hy_oas": 7,
        "us_10y": 4.5,
        "us_2y": 4.0,
        "price": 90,
        "sma200": 110,
        "fear": 90,
    }


def test_disabled_extreme_factors_do_not_trigger_alert_or_floor(inputs):
    score, details = model.calculate_risk(inputs, {"technical": 1})
    assert score == 10
    assert details["_meta"]["extreme_count"] == 0
    assert details["_meta"]["redline_floor"] == 0
    assert not details["_meta"]["resonance"]


def test_all_zero_weights_and_contributions_use_the_same_defaults(inputs):
    score, details = model.calculate_risk(inputs, dict.fromkeys(model.FACTOR_META, 0))
    reference, _ = model.calculate_risk(inputs, model.DEFAULT_WEIGHTS)
    assert score == reference
    meta = details["_meta"]
    assert meta["effective_weights"] == pytest.approx(model.DEFAULT_WEIGHTS)
    assert sum(meta["contributions"].values()) == pytest.approx(meta["weighted"], abs=0.05)
    assert score == pytest.approx(
        sum(meta["contributions"].values())
        + meta["redline_adjustment"]
        + meta["boost"]
        - meta["cap_adjustment"],
        abs=0.05,
    )


def test_missing_factor_is_excluded_and_coverage_is_reported(inputs):
    inputs["hy_oas"] = None
    score, details = model.calculate_risk(inputs, model.DEFAULT_WEIGHTS)
    assert score is not None
    assert details["credit"][0] is None
    assert details["_meta"]["coverage"] == 0.8
    assert details["_meta"]["effective_weights"]["credit"] == 0
    assert details["_meta"]["missing_factors"] == ["credit"]
    assert sum(details["_meta"]["effective_weights"].values()) == pytest.approx(1)


def test_no_data_has_no_score():
    score, details = model.calculate_risk({}, model.DEFAULT_WEIGHTS)
    assert score is None
    assert details["_meta"]["coverage"] == 0


@pytest.mark.parametrize("invalid", [-1, float("nan"), float("inf")])
def test_invalid_weights_are_rejected(inputs, invalid):
    with pytest.raises(ValueError):
        model.calculate_risk(inputs, {"credit": invalid})


@pytest.mark.parametrize("sma", [0, -10, float("nan"), float("inf")])
def test_invalid_average_does_not_become_low_risk(sma):
    with pytest.raises(ValueError):
        model.score_technical(100, sma)


@pytest.mark.parametrize("fear", [-1, 101, float("nan")])
def test_sentiment_range(fear):
    with pytest.raises(ValueError):
        model.score_sentiment(fear)


@pytest.mark.parametrize("container", [list, np.array, pd.Series])
def test_credit_ties_and_array_history(container):
    score, _, status = model.score_credit(3, container([3] * 25))
    assert score == pytest.approx(100 * 0.5**1.6, abs=0.05)
    assert "50%" in status


def test_credit_history_filters_invalid_samples():
    history = [float("nan"), None, "bad", -1, float("inf")] + list(range(25))
    assert model.score_credit(10, history) == model.score_credit(10, list(range(25)))


@pytest.mark.parametrize("count,boost", [(2, 0), (3, 8), (4, 12), (5, 15), (6, 15)])
def test_resonance(count, boost):
    assert model.detect_resonance(dict.fromkeys(range(count), 80)) == (count, count >= 3, boost)


def test_scenario_snapshots_remain_stable():
    scores = [
        model.calculate_risk(values, model.DEFAULT_WEIGHTS)[0]
        for values in model.HISTORICAL_SCENARIOS.values()
    ]
    assert scores == [70.0, 46.2, 63.0]


@pytest.mark.parametrize("score,color", [(59.9, "#187C68"), (60, "#A36B22"), (80, "#BD4C57")])
def test_shared_risk_band_boundaries(score, color):
    assert model.risk_band(score)[1] == color
