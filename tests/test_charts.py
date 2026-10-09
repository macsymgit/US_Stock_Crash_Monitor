import numpy as np
import pytest

from charts import price_chart
from data_fetcher import generate_mock_market


def test_short_display_window_retains_full_history_average():
    frame = generate_mock_market()[0]
    figure = price_chart(frame, "VOO", periods=63)
    expected = frame["Close"].rolling(200).mean().tail(63)
    assert len(figure.data[0].x) == 63
    assert np.isfinite(figure.data[1].y).all()
    assert list(figure.data[1].y) == pytest.approx(expected.tolist())


def test_candle_view_keeps_volume_and_both_averages():
    frame = generate_mock_market()[0]
    figure = price_chart(frame, "VOO", kind="candles", periods=126)
    assert [trace.name for trace in figure.data] == ["VOO", "SMA 50", "SMA 200", "成交量"]
    assert len(figure.data[0].close) == 126
    assert list(figure.data[-1].y) == frame["Volume"].tail(126).tolist()
