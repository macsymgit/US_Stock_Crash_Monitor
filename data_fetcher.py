# -*- coding: utf-8 -*-
"""外部数据获取：带超时、TLS验证、代理隔离和独立的收益率降级链。

ETF与宏观数据分开缓存（由app.py管理），独立宏观数据源并发请求。
失败项value=None，演示行情明确标记。历史持久化由risk_history.py负责。
"""

import re
import json
import ssl
import urllib.request
from urllib.parse import urlencode, urlsplit
from html import unescape
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar, copy_context
from datetime import datetime, timezone
from threading import RLock

import numpy as np
import pandas as pd


def generate_mock_market(ticker="VOO", seed=42):
    """
    生成逼真的模拟行情, 用于网络完全断开时的演示模式。
    返回 (df, price, sma200, us10y)。
    """
    rng = np.random.default_rng(seed)
    dates = pd.date_range(end=datetime.now(timezone.utc).date(), periods=500, freq="B")
    base_price = 450
    trend = np.linspace(0, 50, 500)
    noise = rng.normal(0, 2, 500).cumsum()
    prices = base_price + trend + noise

    df = pd.DataFrame(index=dates)
    df["Open"] = prices + rng.uniform(-2, 2, 500)
    df["High"] = np.maximum(df["Open"], prices) + rng.uniform(0, 5, 500)
    df["Low"] = np.minimum(df["Open"], prices) - rng.uniform(0, 5, 500)
    df["Close"] = prices
    df["Volume"] = rng.integers(1_000_000, 8_000_000, 500)
    df["SMA_200"] = df["Close"].rolling(window=200).mean()

    return (df, float(df["Close"].iloc[-1]), float(df["SMA_200"].iloc[-1]), 4.15)


# ----------------------------------------------------------------------------
# 全局网络配置
# ----------------------------------------------------------------------------
_SSL_CTX = ssl.create_default_context()

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# 网络代理 (由 app.py 注入, 例如 http://127.0.0.1:7890)
_PROXY = ContextVar("market_data_proxy", default=None)
# yfinance内部共享YfData单例，串行设置代理并请求，避免不同会话相互覆盖。
_YF_LOCK = RLock()


def set_proxy(proxy_url):
    """仅设置当前调用上下文的代理，空值明确恢复直连。"""
    proxy_url = (proxy_url or "").strip()
    if proxy_url:
        parsed = urlsplit(proxy_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("代理地址需为 http:// 或 https:// 开头的完整地址")
        parsed.port  # 校验端口格式。
    _PROXY.set(proxy_url or None)


def _safe_error(error):
    text = re.sub(r"(?i)(api_key=)[^&\s'\"]+", r"\1[已隐藏]", str(error))
    return re.sub(r"(https?://)[^/\s@]+@", r"\1[已隐藏]@", text)


def _value(value, minimum=None, maximum=None):
    value = float(value)
    if not np.isfinite(value):
        raise ValueError("数据源返回了非有限数值")
    if minimum is not None and value < minimum:
        raise ValueError("数据源返回值低于有效范围")
    if maximum is not None and value > maximum:
        raise ValueError("数据源返回值超出有效范围")
    return value


def _page_text(html):
    html = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", html, flags=re.I | re.S)
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]*>", " ", html)))


def _http_get(url, headers=None, timeout=15):
    """
    统一的 HTTP GET, 自动应用代理与 UA, 返回原始 bytes。失败抛异常。
    """
    h = {
        "User-Agent": _UA,
        "Accept": "application/json,text/html,application/xhtml+xml,*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)

    handlers = [urllib.request.HTTPSHandler(context=_SSL_CTX)]
    proxy = _PROXY.get()
    handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy} if proxy else {}))
    opener = urllib.request.build_opener(*handlers)

    with opener.open(req, timeout=timeout) as resp:
        raw = resp.read(5_000_001)
        if len(raw) > 5_000_000:
            raise ValueError("数据源响应过大")
        return raw


# ============================================================================
# 1. FRED (美联储圣路易斯分行) —— GDP / 美股总市值 / 备选国债收益率
#    注意: fred.stlouisfed.org (网页/CSV) 国内被墙,
#          但 api.stlouisfed.org (官方API) 可达, 需免费 API key。
#    申请: https://fred.stlouisfed.org/docs/api/api_key.html (免费, 秒批)
# ============================================================================
_FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"


def _fred_latest(series_id, api_key, limit=8):
    """
    取 FRED 某序列最近若干条观测, 返回最新非空 float 值。
    GDP 等低频序列最新点可能是 '.', 故取最近 limit 条找最新有效值。
    """
    url = (
        _FRED_BASE
        + "?"
        + urlencode(
            {
                "series_id": series_id,
                "api_key": api_key,
                "file_type": "json",
                "sort_order": "desc",
                "limit": limit,
            }
        )
    )
    raw = _http_get(url)
    data = json.loads(raw)
    for obs in data.get("observations", []):
        v = obs.get("value", ".")
        if v not in (".", "", None):
            return _value(v), obs.get("date")
    raise ValueError(f"FRED {series_id} 无有效观测值")


def get_fred_gdp(api_key):
    """
    美国名义 GDP (FRED 序列 GDP, 单位: 十亿美元, 季度, 已年化)。
    返回 (gdp_trillion, '2025-Q2 (FRED)')
    """
    val, date = _fred_latest("GDP", api_key)
    return val / 1000.0, f"{date} (FRED)"  # 十亿 -> 万亿


def get_fred_hy_spread(api_key, hist_years=5):
    """
    高收益债信用利差 (FRED 序列 BAMLH0A0HYM2, 单位: %, 日频)。
    返回 (current_value, history_list, source)。
    history_list 为最近 hist_years 年的历史利差, 用于分位数计算。
    这是与股市自身指标正交的"聪明钱"信用信号, 崩盘前常领先1-2个月扩大。
    """
    limit = hist_years * 260 + 20  # 每年约260个交易日
    url = (
        _FRED_BASE
        + "?"
        + urlencode(
            {
                "series_id": "BAMLH0A0HYM2",
                "api_key": api_key,
                "file_type": "json",
                "sort_order": "desc",
                "limit": limit,
                "observation_start": (pd.Timestamp.now(tz="UTC") - pd.DateOffset(years=hist_years))
                .date()
                .isoformat(),
            }
        )
    )
    raw = _http_get(url)
    data = json.loads(raw)
    vals = []
    for obs in data.get("observations", []):
        v = obs.get("value", ".")
        if v not in (".", "", None):
            vals.append((_value(v, 0), obs.get("date", "")))
    if not vals:
        raise ValueError("FRED 高收益债利差无数据")
    current, date = vals[0]
    return current, [value for value, _ in vals], f"{date} (FRED)"


def get_fred_market_cap(api_key):
    """
    FRED 美国公司权益市值 (序列 NCBEILQ027S, 单位: 百万美元, 季度)。
    此序列仅覆盖非金融公司权益，不能视为全部上市公司总市值。
    返回 (marketcap_trillion, date)，仅用于显式标注的代理指标。
    """
    val, date = _fred_latest("NCBEILQ027S", api_key)
    return val / 1e6, f"{date} (FRED)"  # 百万 -> 万亿


def get_fred_dgs(api_key):
    """
    FRED 国债收益率 DGS2 / DGS10 (日度, %)。
    返回 (dgs2, dgs10, date)。用作 CNBC/yfinance 的备用。
    """
    v2, d2 = _fred_latest("DGS2", api_key)
    v10, d10 = _fred_latest("DGS10", api_key)
    return v2, v10, f"2Y {d2} / 10Y {d10} (FRED)"


# ============================================================================
# 2. CNBC —— 2年期美债收益率 (国内直连, 无需key, 实时)
# ============================================================================
def get_cnbc_yield(symbol="US2Y"):
    """
    CNBC 实时 2年期美债收益率 (%)。国内直连稳定。
    返回 (yield_2y_float, 'CNBC 实时')
    """
    url = (
        "https://quote.cnbc.com/quote-html-webservice/restQuote/"
        f"symbolType/symbol?symbols={symbol}&requestMethod=itv&noform=1"
        "&partnerId=2&fund=1&exthrs=1&output=json"
    )
    raw = _http_get(url)
    data = json.loads(raw)
    q = data["FormattedQuoteResult"]["FormattedQuote"][0]
    last = str(q.get("last", "")).replace("%", "").strip()
    return _value(last, -5, 30), f"CNBC {symbol}"


def get_cnbc_us2y():
    return get_cnbc_yield("US2Y")


# ============================================================================
# 3. 东方财富 —— 美债收益率备用，仅下载最近20条。
# ============================================================================
def get_eastmoney_treasury():
    """直接取东方财富最新20条美债数据，避免AkShare下载多年完整历史。"""
    params = {
        "type": "RPTA_WEB_TREASURYYIELD",
        "sty": "ALL",
        "st": "SOLAR_DATE",
        "sr": "-1",
        "p": "1",
        "ps": "20",
        "token": "894050c76af8597a853f5b408b759f5d",
    }
    raw = _http_get("https://datacenter.eastmoney.com/api/data/get?" + urlencode(params))
    observations = json.loads(raw).get("result") or {}
    for row in sorted(
        observations.get("data", []), key=lambda item: item.get("SOLAR_DATE", ""), reverse=True
    ):
        try:
            us2y = _value(row.get("EMG00001306"), -5, 30)
            us10y = _value(row.get("EMG00001310"), -5, 30)
            return us2y, us10y, us10y - us2y, f"{row['SOLAR_DATE'][:10]} (东方财富)"
        except (TypeError, ValueError):
            continue
    raise ValueError("东方财富最近20条中无有效美债数据")


# ============================================================================
# 4. Multpl —— 席勒市盈率 Shiller PE (CAPE)
# ============================================================================
def get_shiller_pe():
    """
    从 multpl.com 抓取最新 Shiller PE。
    页面顶部有 "Current Shiller PE Ratio: XX.XX" 字样。
    返回 (pe_float, 'multpl.com')
    """
    raw = _http_get("https://www.multpl.com/shiller-pe")
    html = raw.decode("utf-8", errors="ignore")
    text = _page_text(html)

    # 主方案: 匹配 "Current Shiller PE Ratio is <b>XX.XX</b>" 之类的结构
    m = re.search(r"Current Shiller PE Ratio[^0-9]*?(\d+\.?\d*)", text, re.I)
    if m:
        return _value(m.group(1), 1, 150), "multpl.com"

    raise ValueError("multpl Shiller PE 解析失败")


# ============================================================================
# 4.5 GuruFocus —— 巴菲特指标 (Buffett Indicator, 总市值/GDP)
#     业界标准口径, 免key直连, 已实机验证可解析。
#     口径: 上市公司总市值 / GDP (基于Wilshire5000全市场)。
# ============================================================================
def get_buffett_indicator():
    """
    从 gurufocus.com 抓取最新巴菲特指标 (%)。
    页面含 "Total Market Cap ... XX.X% ... GDP" 结构。
    返回 (buffett_pct, total_market_cap_trillion_or_None, source)。

    注: gurufocus 给出两个口径:
        - 232.9% 类: 基于当前GDP (TMC/GDP)
        - 192.9% 类: 基于预测GDP (修正版)
    本函数取第一个 (TMC/GDP 当前值), 与历史 2000/2008/2021 阈值口径一致。
    """
    url = "https://www.gurufocus.com/stock-market-valuations.php"
    raw = _http_get(url, timeout=20)
    html = _page_text(raw.decode("utf-8", errors="ignore"))

    # 匹配 "Total Market ... XX.X% ..." 结构
    m = re.search(
        r"(?:total\s+market\s+cap(?:italization)?|ratio\s+of\s+(?:the\s+)?total\s+market)[^%]{0,200}?(\d+(?:\.\d+)?)\s*%",
        html,
        re.I,
    )
    if not m:
        raise ValueError("gurufocus 巴菲特指标解析失败")

    buffett_pct = _value(m.group(1), 1, 1000)
    return buffett_pct, None, "gurufocus.com"


# ============================================================================
# 5. CNN —— 恐慌与贪婪指数 Fear & Greed Index
# ============================================================================
def get_fear_greed():
    """
    CNN Fear & Greed Index (0-100)。
    非官方 dataviz 端点, 需完整浏览器 UA 否则返回 418。
    返回 (score_int, rating_str, timestamp_str)
    """
    url = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
    raw = _http_get(url, headers={"Referer": "https://www.cnn.com/"})
    data = json.loads(raw)
    fg = data["fear_and_greed"]
    score = int(round(_value(fg["score"], 0, 100)))
    rating = fg.get("rating", "")
    ts = fg.get("timestamp", "")
    return score, rating, str(ts)


# ============================================================================
# 6. yfinance —— 股价 / 200日均线 / 10Y美债收益率
# ============================================================================
def get_market_data_yf(ticker="VOO", include_treasury=True):
    """
    获取 ETF 历史行情 + 200日均线 + 10Y美债收益率(^TNX)。
    返回 (df, current_price, sma_200, us_10y_yield)。失败抛异常由上层降级。
    """
    import yfinance as yf

    with _YF_LOCK:
        yf.set_config(proxy=_PROXY.get())
        df = yf.Ticker(ticker).history(period="2y", auto_adjust=True, timeout=12)
    if df.empty:
        raise ValueError(f"{ticker} 行情为空")

    required = ["Open", "High", "Low", "Close"]
    df = df.replace([np.inf, -np.inf], np.nan).dropna(subset=required).sort_index().copy()
    df = df.loc[~df.index.duplicated(keep="last")]
    if len(df) < 200:
        raise ValueError(f"{ticker} 有效行情少于200个交易日，无法计算均线")
    current_price = _value(df["Close"].iloc[-1], 0.01)
    df["SMA_200"] = df["Close"].rolling(window=200).mean()
    sma_200 = _value(df["SMA_200"].iloc[-1], 0.01)

    # 10Y 美债收益率
    us_10y = None
    if include_treasury:
        try:
            us_10y, _ = get_yf_us10y()
        except Exception:
            pass

    return df, current_price, sma_200, us_10y


# ============================================================================
# 聚合: 一键获取全部自动数据 (带降级)
# ============================================================================
def get_yf_us10y():
    import yfinance as yf

    with _YF_LOCK:
        yf.set_config(proxy=_PROXY.get())
        frame = yf.Ticker("^TNX").history(period="5d", timeout=12)
    values = frame["Close"].dropna()
    if values.empty:
        raise ValueError("yfinance 10Y为空")
    return _value(values.iloc[-1], -5, 30), f"{values.index[-1].date()} (yfinance ^TNX)"


def _failed(error):
    return {"ok": False, "value": None, "error": _safe_error(error)}


def fetch_market_data(ticker="VOO", proxy=None):
    """只请求所选ETF；断网时返回明确标记的、可复现的演示行情。"""
    token = _PROXY.set(None)
    try:
        set_proxy(proxy)
        try:
            frame, price, sma, _ = get_market_data_yf(ticker, include_treasury=False)
            return {
                "ok": True,
                "df": frame,
                "price": price,
                "sma200": sma,
                "is_mock": False,
                "source": "yfinance (复权日线)",
                "as_of": frame.index[-1].date().isoformat(),
            }
        except Exception as error:
            frame, price, sma, _ = generate_mock_market(ticker)
            return {
                "ok": False,
                "df": frame,
                "price": price,
                "sma200": sma,
                "is_mock": True,
                "source": "模拟数据",
                "as_of": None,
                "error": _safe_error(error),
            }
    finally:
        _PROXY.reset(token)


def _fetch_treasury(fred_api_key):
    """两个期限独立检查；任一期限缺失都执行备用链。"""
    results = {}
    for key, getter in (("us10y", get_yf_us10y), ("us2y", get_cnbc_us2y)):
        try:
            value, source = getter()
            results[key] = {"ok": True, "value": _value(value, -5, 30), "source": source}
        except Exception as error:
            results[key] = _failed(error)
    if not results["us10y"]["ok"]:
        try:
            value, source = get_cnbc_yield("US10Y")
            results["us10y"] = {"ok": True, "value": value, "source": source}
        except Exception as error:
            results["us10y"] = _failed(error)
    if all(item["ok"] for item in results.values()):
        return results
    backups = [get_eastmoney_treasury]
    if fred_api_key:
        backups.append(lambda: get_fred_dgs(fred_api_key))
    for getter in backups:
        try:
            values = getter()
            v2, v10, source = values[0], values[1], values[-1]
            for key, value in (("us2y", v2), ("us10y", v10)):
                if not results[key]["ok"]:
                    results[key] = {"ok": True, "value": _value(value, -5, 30), "source": source}
            if all(item["ok"] for item in results.values()):
                break
        except Exception as error:
            for item in results.values():
                if not item["ok"]:
                    item["error"] += "; " + _safe_error(error)
    return results


def _fetch_buffett(fred_api_key):
    try:
        value, _, source = get_buffett_indicator()
        return {"buffett": {"ok": True, "value": _value(value, 1), "source": source}}
    except Exception as error:
        result = {"buffett": _failed(error)}
    if fred_api_key:
        try:
            gdp, gdp_source = get_fred_gdp(fred_api_key)
            cap, cap_source = get_fred_market_cap(fred_api_key)
            if gdp_source.split()[0] != cap_source.split()[0]:
                raise ValueError("FRED权益和GDP季度不一致，不能计算代理比值")
            # NCBEILQ027S不是上市总市值。代理值仅展示，不送入巴菲特评分。
            result["buffett_proxy"] = {
                "ok": True,
                "value": cap / _value(gdp, 0.01) * 100,
                "source": f"非金融公司权益/GDP ({cap_source})",
            }
        except Exception as error:
            result["buffett"]["error"] += "; " + _safe_error(error)
    return result


def _fetch_credit(fred_api_key):
    if not fred_api_key:
        return {"credit": _failed("未提供FRED key")}
    try:
        value, history, source = get_fred_hy_spread(fred_api_key)
        return {
            "credit": {"ok": True, "value": _value(value, 0), "history": history, "source": source}
        }
    except Exception as error:
        return {"credit": _failed(error)}


def _fetch_shiller():
    try:
        value, source = get_shiller_pe()
        return {"shiller": {"ok": True, "value": _value(value, 1, 150), "source": source}}
    except Exception as error:
        return {"shiller": _failed(error)}


def _fetch_sentiment():
    try:
        score, rating, timestamp = get_fear_greed()
        return {
            "fear_greed": {
                "ok": True,
                "value": score,
                "rating": rating,
                "source": f"CNN ({rating})",
                "as_of": timestamp,
            }
        }
    except Exception as error:
        return {"fear_greed": _failed(error)}


def fetch_macro_data(fred_api_key=None, proxy=None):
    """独立数据源并发获取，供VOO风险面板缓存复用。"""
    token = _PROXY.set(None)
    try:
        set_proxy(proxy)
        key = (fred_api_key or "").strip() or None
        jobs = [
            lambda: _fetch_treasury(key),
            lambda: _fetch_buffett(key),
            lambda: _fetch_credit(key),
            _fetch_shiller,
            _fetch_sentiment,
        ]
        with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
            futures = [pool.submit(copy_context().run, job) for job in jobs]
            result = {}
            for future in futures:
                result.update(future.result())
        result["_fetched_at"] = datetime.now(timezone.utc).isoformat()
        return result
    finally:
        _PROXY.reset(token)


def fetch_all(ticker="VOO", fred_api_key=None, proxy=None, include_macro=True):
    """命令行/外部调用的统一入口，关闭宏观获取时不发出宏观请求。"""
    with ThreadPoolExecutor(max_workers=2) as pool:
        market = pool.submit(fetch_market_data, ticker, proxy)
        macro = pool.submit(fetch_macro_data, fred_api_key, proxy) if include_macro else None
        result = macro.result() if macro else {}
        result["market"] = market.result()
    return result


if __name__ == "__main__":
    import os
    from pathlib import Path
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).with_name(".env"))
    print("数据源连通性自测")
    for name, item in fetch_all(fred_api_key=os.environ.get("FRED_API_KEY")).items():
        if not isinstance(item, dict):
            continue
        if item.get("ok"):
            value = item.get("price", item.get("value"))
            print(f"[OK] {name}: {value} ({item.get('source', '')})")
        else:
            print(f"[--] {name}: {item.get('error', '')}")
