import copy
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

import data_fetcher
import risk_history

APP = Path(__file__).resolve().parents[1] / "app.py"


@pytest.fixture
def sources(monkeypatch, tmp_path):
    st.cache_data.clear()
    frame, price, sma, _ = data_fetcher.generate_mock_market()
    market = {
        "ok": True,
        "df": frame,
        "price": price,
        "sma200": sma,
        "is_mock": False,
        "source": "fixture",
        "as_of": "2026-10-09",
    }
    macro = {
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
    macro["credit"]["history"] = list(range(1, 30))
    fetch_market = Mock(side_effect=lambda *args: copy.deepcopy(market))
    fetch_macro = Mock(side_effect=lambda *args: copy.deepcopy(macro))
    monkeypatch.setattr(data_fetcher, "fetch_market_data", fetch_market)
    monkeypatch.setattr(data_fetcher, "fetch_macro_data", fetch_macro)
    monkeypatch.setattr(risk_history, "HISTORY_FILE", tmp_path / "history.csv")
    record = Mock(wraps=risk_history.record_risk_score)
    monkeypatch.setattr(risk_history, "record_risk_score", record)
    yield market, macro, fetch_market, fetch_macro, record
    st.cache_data.clear()


def run_app():
    app = AppTest.from_file(str(APP), default_timeout=15).run()
    assert not app.exception
    return app


def test_voo_dashboard_rerun_reuses_both_caches(sources):
    _, _, market, macro, record = sources
    app = run_app()
    assert len(app.get("plotly_chart")) == 2
    assert record.call_args.kwargs["observation_date"] == "2026-10-09"
    assert not app.sidebar.selectbox
    assert not any("QQQ" in item.value for item in app.title)
    app.run()
    assert not app.exception
    assert macro.call_count == 1
    assert market.call_count == 1
    assert market.call_args.args[0] == "VOO"


def test_missing_credit_has_no_default_and_is_not_saved(sources):
    _, macro, _, _, record = sources
    macro["credit"] = {"ok": False, "value": None, "error": "offline"}
    app = run_app()
    record.assert_not_called()
    assert any("指标不完整" in item.value for item in app.warning)
    table = app.dataframe[0].value
    assert table.loc[table["因子"] == "信用利差", "读数"].item() == "—"
    app.checkbox(key="ov_credit").check().run()
    assert not app.exception
    record.assert_not_called()
    assert any("手动情景评分" in item.value for item in app.info)


def test_manual_override_does_not_overwrite_automatic_history(sources):
    *_, record = sources
    app = run_app()
    record.reset_mock()
    app.checkbox(key="ov_shiller").check().run()
    assert not app.exception
    record.assert_not_called()


def test_macro_toggle_off_sends_no_macro_requests(sources):
    _, _, _, macro, record = sources
    app = run_app()
    st.cache_data.clear()
    macro.reset_mock()
    record.reset_mock()
    app.toggle[0].set_value(False).run()
    assert not app.exception
    macro.assert_not_called()
    record.assert_not_called()
    assert any("15%" in item.value for item in app.warning)


def test_all_zero_weights_show_default_contributions(sources):
    app = run_app()
    for slider in app.slider:
        slider.set_value(0)
    app.run()
    assert not app.exception
    assert any("默认权重" in item.value for item in app.info)
    table = app.dataframe[0].value
    assert table.loc[table["因子"] == "巴菲特指标", "生效权重"].item() == "15%"
    charts = [json.loads(chart.proto.spec) for chart in app.get("plotly_chart")]
    assert sum(charts[1]["data"][0]["x"]) > 0


def test_total_network_failure_still_displays_demo_chart(sources):
    market, macro, _, _, record = sources
    market["ok"] = False
    market["is_mock"] = True
    for key in list(macro):
        macro[key] = {"ok": False, "value": None, "error": "offline"}
    app = run_app()
    record.assert_not_called()
    assert len(app.get("plotly_chart")) == 1
    assert any("暂无可用" in item.value for item in app.info)


def test_automatic_recovery_uses_new_value_without_stuck_override(sources):
    _, macro, _, _, record = sources
    macro["credit"] = {"ok": False, "value": None, "error": "offline"}
    app = run_app()
    assert not app.checkbox(key="ov_credit").value
    macro["credit"] = {"ok": True, "value": 3, "source": "recovered"}
    st.cache_data.clear()
    app.run()
    assert not app.exception
    assert not app.checkbox(key="ov_credit").value
    record.assert_called_once()


def test_history_failure_is_reported_without_breaking_dashboard(sources):
    *_, record = sources
    record.side_effect = OSError("disk unavailable")
    app = run_app()
    assert any("保存失败" in item.value for item in app.warning)
    assert len(app.get("plotly_chart")) == 2


def test_light_charts_and_source_navigation(sources):
    app = run_app()
    charts = [json.loads(chart.proto.spec) for chart in app.get("plotly_chart")]
    assert all(chart["layout"]["font"]["color"] == "#192D3E" for chart in charts)
    assert all(chart["layout"]["paper_bgcolor"] == "rgba(0,0,0,0)" for chart in charts)
    app.sidebar.radio[0].set_value("数据来源").run()
    assert not app.exception
    assert len(app.dataframe[0].value) == 7
    assert app.dataframe[0].value.iloc[0]["指标"] == "VOO行情"


def test_factor_navigation_preserves_missing_readings(sources):
    _, macro, _, _, _ = sources
    macro["credit"] = {"ok": False, "value": None, "error": "offline"}
    app = run_app()
    app.sidebar.radio[0].set_value("因子拆解").run()
    assert not app.exception
    table = app.dataframe[0].value
    assert len(table) == 6
    assert table.loc[table["因子"] == "信用利差", "读数"].item() == "—"
