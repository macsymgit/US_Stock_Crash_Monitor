from datetime import datetime

import pytest

import data_fetcher
import notifier
import risk_history
import risk_model

NY = notifier.NY


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (datetime(2026, 11, 11, 9, 0, tzinfo=NY), datetime(2026, 11, 11, 10, 0, tzinfo=NY)),
        (datetime(2026, 11, 11, 10, 0, tzinfo=NY), datetime(2026, 11, 12, 10, 0, tzinfo=NY)),
        (datetime(2026, 11, 13, 11, 0, tzinfo=NY), datetime(2026, 11, 16, 10, 0, tzinfo=NY)),
        (datetime(2026, 11, 14, 8, 0, tzinfo=NY), datetime(2026, 11, 16, 10, 0, tzinfo=NY)),
        # 跨夏令时结束的周末：周五EDT -> 周一EST，仍是当地10:00。
        (datetime(2026, 10, 30, 11, 0, tzinfo=NY), datetime(2026, 11, 2, 10, 0, tzinfo=NY)),
    ],
)
def test_next_run_is_next_weekday_10am_new_york(now, expected):
    target = notifier.next_run(now)
    assert target == expected
    assert target.utcoffset() == expected.utcoffset()


def test_notify_time_parses_and_schedules():
    assert notifier.parse_time(" 9:45 ") == (9, 45)
    now = datetime(2026, 11, 13, 9, 0, tzinfo=NY)
    assert notifier.next_run(now, 9, 45) == datetime(2026, 11, 13, 9, 45, tzinfo=NY)


@pytest.mark.parametrize("bad", ["25:00", "10:60", "10", "ten", ""])
def test_notify_time_rejects_bad_values(bad):
    with pytest.raises(ValueError):
        notifier.parse_time(bad)


MARKET = {"is_mock": False, "as_of": "2026-11-13"}
HIGH = {
    "buffett": 220,
    "shiller": 44,
    "hy_oas": 7,
    "us_10y": 4.5,
    "us_2y": 4.0,
    "price": 90,
    "sma200": 110,
    "fear": 90,
}
LOW = {
    "buffett": 90,
    "shiller": 15,
    "hy_oas": 2,
    "us_10y": 4.5,
    "us_2y": 4.0,
    "price": 100,
    "sma200": 100,
    "fear": 20,
}


def message(inputs, market=MARKET):
    score, details = risk_model.calculate_risk(inputs, risk_model.DEFAULT_WEIGHTS)
    return score, notifier.format_message(score, details, market)


def test_high_score_has_warning_and_full_breakdown():
    score, text = message(HIGH)
    assert score >= 80
    assert text.startswith("⚠️ 高风险警告")
    for meta in risk_model.FACTOR_META.values():
        assert meta["name"] in text
    assert "贡献" in text and "主要驱动" in text and "缺失" not in text


def test_low_score_has_no_warning():
    score, text = message(LOW)
    assert score < 80
    assert "⚠️" not in text
    assert text.startswith(f"VOO 风险评分 {score:.1f}")


def test_missing_and_mock_are_flagged_provisional():
    _, text = message(
        {**LOW, "hy_oas": None, "price": None, "sma200": None},
        market={"is_mock": True, "as_of": None},
    )
    assert "缺失：信用利差、均线乖离（暂定评分，未写入历史）" in text
    assert "VOO行情获取失败" in text
    assert "演示行情" in text


def test_no_factors_still_formats():
    _, text = message(dict.fromkeys(LOW))
    assert text.startswith("VOO 风险评分：暂无可用因子")


@pytest.fixture
def fetched(monkeypatch, tmp_path):
    _, price, sma, _ = data_fetcher.generate_mock_market()
    data = {
        key: {"ok": True, "value": value, "source": "fixture"}
        for key, value in {
            "buffett": 180,
            "shiller": 35,
            "credit": 3,
            "us2y": 4,
            "us10y": 4.5,
            "fear_greed": 45,
        }.items()
    }
    data["market"] = {"is_mock": False, "price": price, "sma200": sma, "as_of": "2026-11-13"}
    data["_fetched_at"] = "2026-11-13T15:00:00+00:00"
    monkeypatch.setattr(data_fetcher, "fetch_all", lambda *args, **kwargs: data)
    monkeypatch.setattr(risk_history, "HISTORY_FILE", tmp_path / "history.csv")
    return data


def test_scan_records_complete_reading(fetched):
    text = notifier.scan()
    # 与仪表盘默认权重的趋势共用同一profile。
    profile = risk_history.scoring_profile(risk_model.DEFAULT_WEIGHTS, "absolute")
    rows = risk_history.load_risk_history("VOO", profile=profile)
    assert list(rows["date"].astype(str)) == ["2026-11-13"]
    assert "缺失" not in text and "均线乖离" in text


def test_scan_with_missing_reading_is_not_recorded(fetched):
    fetched["credit"] = {"ok": False, "value": None, "error": "offline"}
    text = notifier.scan()
    assert risk_history.load_risk_history("VOO").empty
    assert "缺失：信用利差" in text
