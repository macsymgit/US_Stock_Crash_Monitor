"""VOO风险研究面板：浅色界面、明确的数据质量、可复核的六因子评分。"""

import os
from html import escape
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv

import data_fetcher as dfetch
import risk_history
import risk_model
from charts import price_chart
from ui_theme import AMBER, MINT, RED, style_chart

ROOT = Path(__file__).resolve().parent
TICKER = "VOO"
GITHUB_URL = "https://github.com/middletoo/US_Stock_Crash_Monitor"
LOGO_PATH = ROOT / "assets" / "logo.png"
load_dotenv(ROOT / ".env")

st.set_page_config(
    page_title="VOO · 美股崩盘风险监测仪",
    page_icon=str(LOGO_PATH),
    layout="wide",
    initial_sidebar_state="expanded",
)
st.logo(str(LOGO_PATH), size="large", link=GITHUB_URL, icon_image=str(LOGO_PATH))
st.markdown(
    "<style>" + (ROOT / "assets" / "app.css").read_text(encoding="utf-8") + "</style>",
    unsafe_allow_html=True,
)


@st.cache_data(ttl=1800, max_entries=16, show_spinner=False)
def get_market_data(proxy):
    return dfetch.fetch_market_data(TICKER, proxy)


@st.cache_data(ttl=1800, max_entries=16, show_spinner=False)
def get_macro_data(fred_key, proxy):
    return dfetch.fetch_macro_data(fred_key, proxy)


st.sidebar.markdown(
    '<div class="sidebar-brand">US Stock<br>Crash Monitor</div>'
    '<div class="sidebar-subtitle">WALL STREET QUANT</div>',
    unsafe_allow_html=True,
)
views = ["市场总览", "因子拆解", "数据来源"]
page = st.sidebar.radio(
    "页面",
    views,
    label_visibility="collapsed",
    format_func=lambda value: f"{views.index(value) + 1:02d}  {value}",
)
st.sidebar.markdown(
    '<div class="sidebar-model">MODEL / V1.4<br>六因子规则模型<br>只监测VOO · 标普500</div>',
    unsafe_allow_html=True,
)
st.sidebar.link_button("GitHub 项目 ↗", GITHUB_URL, width="stretch")

header, actions = st.columns([1.65, 1])
with header:
    st.markdown('<div class="product-kicker">MARKET RISK OBSERVATORY</div>', unsafe_allow_html=True)
    st.title(
        {"市场总览": "市场风险总览", "因子拆解": "六因子 · 风险拆解", "数据来源": "来源与数据质量"}[
            page
        ]
    )
    st.markdown(
        '<p class="product-subtitle">聚焦风险结构，追踪主要驱动。</p>', unsafe_allow_html=True
    )
with actions:
    badge, refresh, settings = st.columns([1, 1, 1.3])
    with badge:
        st.markdown('<div class="voo-badge">VOO · 标普500</div>', unsafe_allow_html=True)
    with refresh:
        if st.button("刷新数据", width="stretch"):
            get_market_data.clear()
            get_macro_data.clear()
            st.rerun()
    with settings:
        settings_popover = st.popover("模型设置", width="stretch")
with settings_popover:
    with st.expander("模型权重", expanded=False):
        st.caption("权重自动归一化；设为0禁用该因子的评分和警报。")
        user_weights = {
            key: st.slider(
                f"{index}. {factor['name']}权重",
                0,
                50,
                round(risk_model.DEFAULT_WEIGHTS[key] * 100),
                5,
                format="%d%%",
                key=f"weight_{key}",
            )
            / 100
            for index, (key, factor) in enumerate(risk_model.FACTOR_META.items(), start=1)
        }
    with st.expander("数据连接", expanded=False):
        auto_fetch = st.toggle(
            "启用数据自动获取", value=True, help="关闭后仅下载VOO行情，宏观与情绪指标使用手动输入"
        )
        fred_key = st.text_input(
            "FRED API Key（可选）",
            value=os.environ.get("FRED_API_KEY", ""),
            type="password",
            help="用于信用利差历史和美债备用数据。",
        )
        st.markdown("[申请FRED key](https://fred.stlouisfed.org/docs/api/api_key.html)")
        proxy = st.text_input("HTTP代理地址", placeholder="http://127.0.0.1:7890").strip()
        st.caption("空值为直连；无法获取行情时明确标记演示数据。")

try:
    dfetch.set_proxy(proxy)
except ValueError as error:
    st.error(str(error))
    st.stop()
with st.spinner("正在获取VOO行情与风险指标…"):
    market = get_market_data(proxy)
    auto = {"market": market}
    if auto_fetch:
        auto.update(get_macro_data(fred_key.strip(), proxy))

# 所有输入控件位于一个折叠区；自动失败不自动开启手动覆盖。
INPUTS = {
    "buffett": ("巴菲特指标", 180.0, "%", ".1f", 1.0),
    "shiller": ("席勒市盈率", 40.0, "", ".1f", 0.1),
    "credit": ("信用利差", 3.0, "%", ".2f", 0.05),
    "us2y": ("2年期收益率", 4.2, "%", ".2f", 0.01),
    "us10y": ("10年期收益率", 4.0, "%", ".2f", 0.01),
    "fear_greed": ("恐慌贪婪指数", 45.0, "", ".0f", 1.0),
}
manual_sources, selected = set(), {}
with settings_popover, st.expander("手动校准", expanded=False):
    st.caption("预设值只用于输入起点。勾选后作为手动情景，不覆盖自动历史。")
    for key, (title, default, unit, fmt, step) in INPUTS.items():
        item = auto.get(key, {})
        available = item.get("ok") and item.get("value") is not None
        value = item["value"] if available else default
        override = st.checkbox(f"校准{title}", key=f"ov_{key}", value=False)
        if override:
            value = st.number_input(
                f"{title} 手动值",
                value=float(value),
                step=step,
                key=f"manual_{key}",
                min_value=-5.0 if key in ("us2y", "us10y") else 0.0,
                max_value=100.0 if key == "fear_greed" else None,
            )
            manual_sources.add(key)
            selected[key] = value
            st.caption(f"手动：{value:{fmt}}{unit}")
        elif available:
            selected[key] = value
            st.caption(f"自动：{value:{fmt}}{unit} · {item.get('source', '')}")
        else:
            selected[key] = None
            st.caption(f"{title}缺失；需要校准时再勾选。")

is_mock = market.get("is_mock", True)
frame = market.get("df")
factor_inputs = {
    "buffett": selected["buffett"],
    "shiller": selected["shiller"],
    "hy_oas": selected["credit"],
    "us_10y": selected["us10y"],
    "us_2y": selected["us2y"],
    "price": None if is_mock else market.get("price"),
    "sma200": None if is_mock else market.get("sma200"),
    "fear": selected["fear_greed"],
}
hy_history = auto.get("credit", {}).get("history")
score, details = risk_model.calculate_risk(factor_inputs, user_weights, hy_history=hy_history)
meta = details["_meta"]
provisional = is_mock or bool(manual_sources) or meta["coverage"] < 0.999999
credit_method = (
    "percentile" if len(risk_model.credit_history_values(hy_history)) > 20 else "absolute"
)
profile = risk_history.scoring_profile(user_weights, credit_method)

if sum(user_weights.values()) == 0:
    st.info("所有权重均为0，已使用默认权重。下方显示实际生效的权重。")
quality_notes = []
if is_mock:
    quality_notes.append("演示行情：VOO数据暂不可用，图表为模拟数据，不参与技术评分")
if meta["missing_factors"]:
    names = "、".join(risk_model.FACTOR_META[k]["name"] for k in meta["missing_factors"])
    quality_notes.append(f"指标不完整：缺少 {names}，覆盖原始权重的 {meta['coverage']:.0%}")
if quality_notes:
    st.warning("；".join(quality_notes) + "。当前参考评分不写入正式历史。")
if manual_sources:
    st.info("包含手动校准值：当前为手动情景评分，不写入正式历史。")
if score is None:
    st.info("暂无可用的已启用风险因子。请恢复数据连接或输入已核实的指标。")
if not provisional and score is not None:
    try:
        risk_history.record_risk_score(
            TICKER, score, observation_date=market["as_of"], profile=profile
        )
    except (OSError, ValueError) as error:
        st.warning(f"风险历史保存失败：{error}")

label_map = [
    ("market", "VOO行情"),
    ("us10y", "10Y收益率"),
    ("us2y", "2Y收益率"),
    ("buffett", "巴菲特指标"),
    ("shiller", "席勒市盈率"),
    ("credit", "信用利差"),
    ("fear_greed", "恐慌贪婪"),
]
ok_count = sum(bool(auto.get(k, {}).get("ok")) for k, _ in label_map)


def factor_table():
    rows = []
    for key, factor in risk_model.FACTOR_META.items():
        risk, value, status = details[key]
        rows.append(
            {
                "因子": factor["name"],
                "读数": value,
                "风险分": "—" if risk is None else f"{risk:.1f}",
                "生效权重": f"{meta['effective_weights'][key]:.0%}",
                "贡献分": f"{meta['contributions'].get(key, 0):.1f}",
                "状态": status,
            }
        )
    return pd.DataFrame(rows)


def show_sources():
    rows = []
    for key, name in label_map:
        item = auto.get(key, {})
        state = "手动覆盖" if key in manual_sources else "自动可用" if item.get("ok") else "缺失"
        if key == "market" and is_mock:
            state = "模拟行情"
        rows.append(
            {
                "指标": name,
                "状态": state,
                "来源": item.get("source", "—"),
                "观测时间": str(item.get("as_of", "见来源说明")),
                "说明": item.get("error", ""),
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    if auto.get("buffett_proxy", {}).get("ok"):
        item = auto["buffett_proxy"]
        st.caption(
            f"FRED代理比值 {item['value']:.1f}% · {item['source']}。口径不同，未纳入巴菲特评分。"
        )
    st.caption(
        "来源状态与生效权重覆盖率不同：手动覆盖可补齐因子，但不会变成自动数据。"
        "VOO价格为复权日线；收益率和宏观观测的时间可能不同。"
    )


if page == "数据来源":
    with st.container(border=True, key="panel-sources"):
        st.subheader("数据源状态")
        show_sources()
elif page == "因子拆解":
    with st.container(border=True, key="panel-factors"):
        st.subheader("读数、权重与贡献")
        st.dataframe(factor_table(), hide_index=True, width="stretch")
        st.caption("贡献分采用生效权重；缺失的启用因子会退出当前计算，已禁用因子不触发警报。")
    with st.expander("评分口径与边界"):
        st.markdown(
            "估值与情绪因子偏向识别过热。负乖离和恐慌可能发生在下跌中，低分不能证明安全。"
            "信用利差在历史足够时采用分位数，其余因子仍使用经验阈值。"
            "当期平坦利差不能证明此前曾发生倒挂。"
        )
else:
    score_col, quality_col, driver_col = st.columns([1.15, 1, 1])
    with score_col, st.container(border=True, key="panel-score"):
        label = "参考风险评分" if provisional else "综合风险评分"
        band, color = risk_model.risk_band(score) if score is not None else ("等待数据", "#607686")
        value = f"{score:.1f}" if score is not None else "—"
        marker = (
            f'<div class="risk-track"><i style="left:{score}%"></i></div>'
            if score is not None
            else ""
        )
        st.markdown(
            f'<div class="risk-label">{label}<span class="status-pill" style="color:{color}">{band}</span></div>'
            f'<div class="risk-number">{value}<small>/ 100</small></div>{marker}'
            '<div class="scale-labels"><span>较低评分</span><span>60</span><span>80</span><span>高风险</span></div>'
            '<p class="summary-note">规则风险信号，不是崩盘概率。</p>',
            unsafe_allow_html=True,
        )
    with quality_col, st.container(border=True, key="panel-quality"):
        st.metric("数据完整性 / 权重覆盖", f"{meta['coverage']:.0%}")
        st.caption(
            f"自动来源 {ok_count}/{len(label_map)} · "
            + ("手动情景" if manual_sources else "参考评分" if provisional else "完整自动评分")
        )
        st.progress(meta["coverage"])
        st.caption("观测日期：" + str(market.get("as_of") or "行情暂不可用"))
    with driver_col, st.container(border=True, key="panel-driver"):
        st.caption("当前主要驱动")
        st.markdown(
            f'<div class="driver-title">{escape(meta.get("max_factor", "等待数据"))}</div>',
            unsafe_allow_html=True,
        )
        st.caption(
            f"最高因子风险分 {meta.get('max_score', 0):.0f}"
            if score is not None
            else "尚无有效因子"
        )
        st.caption(
            f"共振：{meta['extreme_count']}个极端因子"
            if meta.get("resonance")
            else "未触发多因子共振"
        )

    if meta.get("resonance"):
        st.error(
            f"共振警报：{meta['extreme_count']}个已启用因子进入模型极端区，额外加分{meta['boost']}。"
        )
    if score is not None and meta.get("max_score", 0) >= 80:
        st.caption(
            f"单因子红线：{meta['max_factor']}风险分{meta['max_score']:.0f}，"
            f"模型总分下限{meta['redline_floor']:.1f}。"
        )

    price_section, contribution_section = st.columns([1.9, 1])
    with price_section, st.container(border=True, key="panel-price"):
        chart_title, ranges = st.columns([1.3, 1])
        with chart_title:
            st.subheader("VOO价格与长期趋势")
            st.caption("标普500 · " + ("演示行情" if is_mock else "复权日线"))
        with ranges:
            period = st.radio(
                "时间范围",
                ["3M", "6M", "1Y"],
                index=1,
                horizontal=True,
                label_visibility="collapsed",
                key="chart_period",
            )
        st.metric(
            "模拟收盘价" if is_mock else "复权收盘价",
            f"${market['price']:.2f}" if market.get("price") is not None else "—",
        )
        kind = st.radio(
            "图表类型",
            ["收盘线", "K线"],
            index=0,
            horizontal=True,
            label_visibility="collapsed",
            key="chart_kind",
        )
        if frame is not None:
            figure = price_chart(
                frame,
                TICKER,
                kind="candles" if kind == "K线" else "line",
                periods={"3M": 63, "6M": 126, "1Y": 252}.get(period, 126),
            )
            st.plotly_chart(figure, width="stretch", key="price_chart")
        else:
            st.caption("暂无行情数据。")
    with contribution_section, st.container(border=True, key="panel-contributions"):
        st.subheader("风险贡献")
        st.caption("因子风险分 × 生效权重")
        if score is None:
            st.caption("指标恢复后显示贡献分。")
        else:
            rows = []
            for key, factor in risk_model.FACTOR_META.items():
                value = meta["contributions"].get(key, 0)
                risk = details[key][0]
                color = AMBER if risk is not None and risk >= 80 else MINT
                rows.append(
                    f'<div class="contribution-row"><div><span>{factor["name"]}</span>'
                    f'<span>{value:.1f}</span></div><div class="contribution-track">'
                    f'<i style="width:{min(100, value / 35 * 100):.2f}%;background:{color}"></i></div></div>'
                )
            st.markdown("".join(rows), unsafe_allow_html=True)
            st.caption(
                f"加权 {meta['weighted']:.1f} + 托底 {meta['redline_adjustment']:.1f} "
                f"+ 共振 {meta['boost']} - 封顶 {meta['cap_adjustment']:.1f} = {score:.1f}（四舍五入）"
            )

    trend_section, scenario_section = st.columns([1.5, 1])
    with trend_section, st.container(border=True, key="panel-trend"):
        st.subheader("风险如何演变")
        try:
            history = risk_history.load_risk_history(TICKER, profile=profile)
        except (OSError, ValueError) as error:
            st.warning(f"风险历史读取失败：{error}")
            history = pd.DataFrame()
        if len(history) >= 2:
            figure = go.Figure(
                go.Scatter(
                    x=history["date"],
                    y=history["score"],
                    mode="lines+markers",
                    name="风险评分",
                    line=dict(color=MINT, width=2),
                )
            )
            style_chart(figure, 260)
            figure.update_yaxes(range=[0, 100])
            figure.add_hline(y=60, line_color=AMBER, line_dash="dot")
            figure.add_hline(y=80, line_color=RED, line_dash="dot")
            st.plotly_chart(figure, width="stretch", key="trend_chart")
        else:
            st.caption(
                f"当前配置已积累{len(history)}个交易日。完整自动评分会按行情日期保存，"
                "达到两个观测日后显示趋势；不同模型和配置分别记录。"
            )
    with scenario_section, st.container(border=True, key="panel-insights"):
        st.subheader("读懂这一次评分")
        st.markdown(
            "- 先确认观测日期与覆盖率，再理解风险读数。\n"
            "- 信用压力与估值过热是不同的信号维度。\n"
            "- 恐慌和负乖离可能发生在下跌中，低分不等于安全。"
        )
        scenario_expander = st.expander("近似历史情景对比", expanded=False)
    with scenario_expander:
        st.caption("相同权重与可用因子；信用统一采用绝对阈值。含构造参数，不是回测。")
        if score is not None:
            current, _ = risk_model.calculate_risk(factor_inputs, user_weights)
            values, names = [current], ["当前 · 绝对阈值"]
            for name, original in risk_model.HISTORICAL_SCENARIOS.items():
                inputs = dict(original)
                for key in meta["missing_factors"]:
                    for input_key in risk_model.FACTOR_META[key]["inputs"]:
                        inputs[input_key] = None
                value, _ = risk_model.calculate_risk(inputs, user_weights)
                values.append(value)
                names.append(name.split(" ")[0] + " 情景")
            figure = go.Figure(
                go.Bar(
                    y=names,
                    x=values,
                    orientation="h",
                    text=[f"{v:.1f}" for v in values],
                    textposition="auto",
                    marker_color=[risk_model.risk_band(v)[1] for v in values],
                )
            )
            style_chart(figure, 260)
            figure.update_xaxes(range=[0, 100])
            figure.update_yaxes(autorange="reversed")
            st.plotly_chart(figure, width="stretch", key="scenario_chart")
        else:
            st.caption("当前无有效因子，暂不计算对比。")
    with st.expander("查看因子读数与数据来源"):
        st.dataframe(factor_table(), hide_index=True, width="stretch")
        show_sources()

st.markdown(
    '<div class="footer-note">US STOCK CRASH MONITOR · V1.4 · VOO ONLY<br>'
    "模型衡量规则风险信号。历史情景不是回测，较低评分不代表市场安全。</div>",
    unsafe_allow_html=True,
)
