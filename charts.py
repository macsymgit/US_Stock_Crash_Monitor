"""行情图构造：先计算完整均线，再裁剪显示窗口，支持收盘线与K线。"""

import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from ui_theme import AMBER, MINT, RED, style_chart


def price_chart(frame, ticker, *, kind="line", periods=126):
    prepared = frame.copy()
    for window in (50, 200):
        prepared[f"SMA_{window}"] = prepared["Close"].rolling(window).mean()
    display = prepared.tail(periods) if periods else prepared
    if kind == "line":
        figure = go.Figure()
        figure.add_trace(
            go.Scatter(
                x=display.index,
                y=display["Close"],
                name="复权价格",
                mode="lines",
                line=dict(color=MINT, width=2.2),
            )
        )
        figure.add_trace(
            go.Scatter(
                x=display.index,
                y=display["SMA_200"],
                name="SMA 200",
                mode="lines",
                line=dict(color=AMBER, width=1.4, dash="dash"),
            )
        )
        style_chart(figure, height=300)
        figure.update_yaxes(title_text="复权价格")
        return figure
    figure = make_subplots(
        rows=2, cols=1, shared_xaxes=True, row_heights=[0.76, 0.24], vertical_spacing=0.03
    )
    figure.add_trace(
        go.Candlestick(
            x=display.index,
            open=display["Open"],
            high=display["High"],
            low=display["Low"],
            close=display["Close"],
            name=ticker,
            increasing_line_color=MINT,
            decreasing_line_color=RED,
        ),
        row=1,
        col=1,
    )
    for window, color, width in ((50, MINT, 1.2), (200, AMBER, 2)):
        figure.add_trace(
            go.Scatter(
                x=display.index,
                y=display[f"SMA_{window}"],
                mode="lines",
                name=f"SMA {window}",
                line=dict(color=color, width=width),
            ),
            row=1,
            col=1,
        )
    if "Volume" in display:
        figure.add_trace(
            go.Bar(
                x=display.index,
                y=display["Volume"],
                name="成交量",
                opacity=0.5,
                marker_color=np.where(display["Close"] >= display["Open"], MINT, RED),
                showlegend=False,
            ),
            row=2,
            col=1,
        )
    style_chart(figure, height=340)
    figure.update_layout(xaxis_rangeslider_visible=False)
    figure.update_yaxes(title_text="复权价格", row=1, col=1)
    figure.update_yaxes(title_text="成交量", row=2, col=1)
    return figure
