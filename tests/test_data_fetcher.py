import json
import ssl
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import numpy as np
import pandas as pd
import pytest

import data_fetcher as fetcher


def fail(*args, **kwargs):
    raise ValueError("offline")


def test_10y_fallback_runs_when_2y_succeeds(monkeypatch):
    monkeypatch.setattr(fetcher, "get_yf_us10y", fail)
    monkeypatch.setattr(fetcher, "get_cnbc_us2y", lambda: (4, "CNBC"))
    monkeypatch.setattr(fetcher, "get_cnbc_yield", fail)
    backup = Mock(return_value=(3.9, 4.3, 0.4, "backup"))
    monkeypatch.setattr(fetcher, "get_eastmoney_treasury", backup)
    result = fetcher._fetch_treasury(None)
    assert result["us10y"]["value"] == 4.3
    assert result["us2y"]["value"] == 4
    backup.assert_called_once()


def test_treasury_failure_returns_missing_not_demo_yield(monkeypatch):
    for name in (
        "get_yf_us10y",
        "get_cnbc_us2y",
        "get_cnbc_yield",
        "get_eastmoney_treasury",
        "get_fred_dgs",
    ):
        monkeypatch.setattr(fetcher, name, fail)
    result = fetcher._fetch_treasury("key")
    assert all(item["value"] is None and not item["ok"] for item in result.values())


def test_disabled_macro_fetch_does_not_request_sources(monkeypatch):
    macro = Mock(side_effect=AssertionError("must not fetch macro"))
    monkeypatch.setattr(fetcher, "fetch_macro_data", macro)
    monkeypatch.setattr(fetcher, "fetch_market_data", lambda *args: {"ok": True})
    assert fetcher.fetch_all(include_macro=False) == {"market": {"ok": True}}
    macro.assert_not_called()


def test_proxy_context_is_isolated_and_can_be_cleared():
    barrier = Barrier(2)

    def task(proxy):
        fetcher.set_proxy(proxy)
        barrier.wait(timeout=5)
        current = fetcher._PROXY.get()
        fetcher.set_proxy(None)
        return current, fetcher._PROXY.get()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(task, ["http://127.0.0.1:7890", "http://127.0.0.1:7891"]))
    assert results == [("http://127.0.0.1:7890", None), ("http://127.0.0.1:7891", None)]
    assert fetcher._SSL_CTX.verify_mode == ssl.CERT_REQUIRED
    assert fetcher._SSL_CTX.check_hostname


def test_macro_jobs_receive_their_proxy_context(monkeypatch):
    barrier = Barrier(5)

    def job(*args):
        barrier.wait(timeout=5)
        return {str(__import__("threading").get_ident()): {"proxy": fetcher._PROXY.get()}}

    for name in (
        "_fetch_treasury",
        "_fetch_buffett",
        "_fetch_credit",
        "_fetch_shiller",
        "_fetch_sentiment",
    ):
        monkeypatch.setattr(fetcher, name, job)
    results = fetcher.fetch_macro_data(proxy="http://127.0.0.1:7890")
    values = [item["proxy"] for item in results.values() if isinstance(item, dict)]
    assert values == ["http://127.0.0.1:7890"] * 5


def test_mock_candles_are_consistent_and_reproducible():
    frame, price, sma, _ = fetcher.generate_mock_market()
    pd.testing.assert_frame_equal(frame, fetcher.generate_mock_market()[0])
    assert (frame["High"] >= frame[["Open", "Close"]].max(axis=1)).all()
    assert (frame["Low"] <= frame[["Open", "Close"]].min(axis=1)).all()
    assert (frame["Volume"] > 0).all()
    assert price > 0 and sma > 0


def test_short_yahoo_history_falls_back(monkeypatch):
    import yfinance as yf

    frame = fetcher.generate_mock_market()[0].tail(100)
    monkeypatch.setattr(yf, "Ticker", lambda *args: Mock(history=Mock(return_value=frame)))
    monkeypatch.setattr(yf, "set_config", Mock())
    with pytest.raises(ValueError, match="200"):
        fetcher.get_market_data_yf()
    assert fetcher.fetch_market_data()["is_mock"]


def test_yahoo_proxy_is_reset_and_nonfinite_rows_removed(monkeypatch):
    import yfinance as yf

    frame = fetcher.generate_mock_market()[0]
    frame.loc[frame.index[-1], "Close"] = np.inf
    ticker = Mock(history=Mock(return_value=frame))
    monkeypatch.setattr(yf, "Ticker", Mock(return_value=ticker))
    config = Mock()
    monkeypatch.setattr(yf, "set_config", config)
    item = fetcher.fetch_market_data(proxy=None)
    assert item["ok"] and len(item["df"]) == 499
    config.assert_called_with(proxy=None)


def test_fred_credit_uses_latest_valid_observation_date(monkeypatch):
    data = {
        "observations": [
            {"date": "2026-10-09", "value": "."},
            {"date": "2026-10-08", "value": "3.2"},
        ]
    }
    monkeypatch.setattr(fetcher, "_http_get", lambda *a, **kw: json.dumps(data).encode())
    current, history, source = fetcher.get_fred_hy_spread("key")
    assert current == 3.2 and history == [3.2]
    assert source == "2026-10-08 (FRED)"


def test_fred_query_encodes_key_and_errors_hide_secrets(monkeypatch):
    request = Mock(return_value=b'{"observations": [{"date": "2026-07-01", "value": "30"}]}')
    monkeypatch.setattr(fetcher, "_http_get", request)
    fetcher._fred_latest("GDP", "a&b")
    assert parse_qs(urlsplit(request.call_args.args[0]).query)["api_key"] == ["a&b"]
    message = fetcher._safe_error(
        "https://fred.test?api_key=secret&other=1 http://name:pwd@localhost:7890"
    )
    assert "secret" not in message and "pwd" not in message


def test_unrelated_percentages_do_not_become_buffett_indicator(monkeypatch):
    monkeypatch.setattr(
        fetcher, "_http_get", lambda *a, **kw: b"<h1>Stock returns</h1><td>125.5%</td>"
    )
    with pytest.raises(ValueError):
        fetcher.get_buffett_indicator()


def test_html_values_with_tags_are_parsed(monkeypatch):
    monkeypatch.setattr(
        fetcher,
        "_http_get",
        lambda *a, **kw: b"Current<span>\nShiller PE Ratio</span>: <b>40.2</b>",
    )
    assert fetcher.get_shiller_pe()[0] == 40.2
    monkeypatch.setattr(
        fetcher, "_http_get", lambda *a, **kw: b"Total Market Cap is <b>232.90%</b> of GDP"
    )
    assert fetcher.get_buffett_indicator()[0] == 232.9


def test_buffett_fetch_uses_browser_fingerprint(monkeypatch):
    request = Mock(return_value=b"Total Market Cap is 232.9% of GDP")
    monkeypatch.setattr(fetcher, "_http_get", request)
    fetcher.get_buffett_indicator()
    assert request.call_args.kwargs["impersonate"] == "chrome"


def test_fred_proxy_is_not_used_as_buffett_score(monkeypatch):
    monkeypatch.setattr(fetcher, "get_buffett_indicator", fail)
    monkeypatch.setattr(fetcher, "get_fred_gdp", lambda *args: (30, "2026-04-01 (FRED)"))
    monkeypatch.setattr(fetcher, "get_fred_market_cap", lambda *args: (75, "2026-04-01 (FRED)"))
    result = fetcher._fetch_buffett("key")
    assert not result["buffett"]["ok"] and result["buffett"]["value"] is None
    assert result["buffett_proxy"]["value"] == 250


def test_fred_proxy_rejects_mismatched_quarters(monkeypatch):
    monkeypatch.setattr(fetcher, "get_buffett_indicator", fail)
    monkeypatch.setattr(fetcher, "get_fred_gdp", lambda *args: (30, "2026-04-01 (FRED)"))
    monkeypatch.setattr(fetcher, "get_fred_market_cap", lambda *args: (75, "2026-01-01 (FRED)"))
    assert "buffett_proxy" not in fetcher._fetch_buffett("key")


def test_eastmoney_fetches_only_recent_valid_data(monkeypatch):
    rows = [
        {"SOLAR_DATE": "2026-10-09", "EMG00001306": None, "EMG00001310": None},
        {"SOLAR_DATE": "2026-10-08", "EMG00001306": "4.1", "EMG00001310": "4.5"},
    ]
    request = Mock(return_value=json.dumps({"result": {"data": rows}}).encode())
    monkeypatch.setattr(fetcher, "_http_get", request)
    v2, v10, _, source = fetcher.get_eastmoney_treasury()
    assert (v2, v10) == (4.1, 4.5) and "2026-10-08" in source
    assert parse_qs(urlsplit(request.call_args.args[0]).query)["ps"] == ["20"]
