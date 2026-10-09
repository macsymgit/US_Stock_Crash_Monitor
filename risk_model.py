# -*- coding: utf-8 -*-
"""六因子规则风险模型 v1.4。

保留v1.3的阈值、非线性信用评分、共振和红线规则。
零权重因子退出计算，缺失值不冒充正常值；元数据统一供UI显示权重和贡献。
模型评分不是崩盘概率。价格与情绪因子偏向过热信号，不完整覆盖下跌中的压力。
"""

import math

MODEL_VERSION = "1.4"
WARNING_SCORE = 60
HIGH_RISK_SCORE = 80

# ------------------------------------------------------------------
# 因子元数据: key -> (中文名, 权重key)
# ------------------------------------------------------------------
FACTOR_META = {
    "buffett": {"name": "巴菲特指标", "inputs": ("buffett",)},
    "shiller": {"name": "席勒市盈率", "inputs": ("shiller",)},
    "credit": {"name": "信用利差", "inputs": ("hy_oas",)},
    "yield": {"name": "美债利差", "inputs": ("us_10y", "us_2y")},
    "technical": {"name": "均线乖离", "inputs": ("price", "sma200")},
    "sentiment": {"name": "恐慌贪婪", "inputs": ("fear",)},
}

# 默认权重 (6因子, 合计100%)
DEFAULT_WEIGHTS = {
    "buffett": 0.15,
    "shiller": 0.20,
    "credit": 0.20,  # 信用利差: 新增, 给较高权重(聪明钱信号)
    "yield": 0.20,
    "technical": 0.15,
    "sentiment": 0.10,
}


def _finite(value, name, minimum=None, maximum=None):
    """拒绝 NaN/无穷值和非法区间，避免坏数据变成低风险分。"""
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} 必须为有限数值")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} 不得小于 {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} 不得大于 {maximum}")
    return value


def normalize_weights(weights):
    values = {k: _finite(weights.get(k, 0), f"{k} 权重", 0) for k in FACTOR_META}
    total = sum(values.values())
    if total == 0:
        return dict(DEFAULT_WEIGHTS)
    return {k: v / total for k, v in values.items()}


def risk_band(score):
    """所有图表与文字共用的风险区间。"""
    if score >= HIGH_RISK_SCORE:
        return "高风险", "#BD4C57"
    if score >= WARNING_SCORE:
        return "风险累积", "#A36B22"
    return "较低评分", "#187C68"


def credit_history_values(history):
    if history is None:
        return []
    values = []
    for value in history:
        try:
            values.append(_finite(value, "信用利差历史", 0))
        except (TypeError, ValueError):
            continue
    return values


# ------------------------------------------------------------------
# 非线性放大: 分位数 -> 风险分
# 用幂变换让极端读数贡献非线性增长。
# percentile 为 0~1 (当前值在历史中处于的分位)。
# exponent>1 时, 越极端(分位越高)分数增长越快。
# ------------------------------------------------------------------
def _nonlinear(percentile, exponent=1.6):
    p = max(0.0, min(1.0, percentile))
    return round(100.0 * (p**exponent), 1)


# ------------------------------------------------------------------
# 各因子的风险分计算 (统一输出 0-100, 越高越危险)
# 每个函数返回 (risk_score_0_100, display_value, status_text)
# ------------------------------------------------------------------


def score_buffett(buffett_val):
    """巴菲特指标(总市值/GDP %)。无逐日历史序列, 用校准阈值映射(本质即分位)。"""
    v = _finite(buffett_val, "巴菲特指标", 0)
    if v > 200:
        r = 100
    elif v > 180:
        r = 90
    elif v > 150:
        r = 75
    elif v > 120:
        r = 50
    elif v > 100:
        r = 35
    else:
        r = 20
    return r, f"{v:.1f}%", _level_text(r)


def score_shiller(shiller_val):
    """席勒市盈率 CAPE。无逐日历史序列, 用校准阈值映射。"""
    v = _finite(shiller_val, "席勒市盈率", 0)
    if v > 40:
        r = 100
    elif v > 35:
        r = 90
    elif v > 30:
        r = 70
    elif v > 25:
        r = 50
    elif v > 20:
        r = 35
    else:
        r = 20
    return r, f"{v:.1f}", _level_text(r)


def score_credit(hy_oas, history=None):
    """
    高收益债信用利差 (%)。有历史序列, 用5年分位数 + 非线性放大。
    利差越大风险越高。history 为历史利差 list (用于算分位)。
    """
    v = _finite(hy_oas, "信用利差", 0)
    history = credit_history_values(history)
    if len(history) > 20:
        # 并列观测取中位秩；常数序列应处于50%而不是100%分位。
        pct = (sum(h < v for h in history) + 0.5 * sum(h == v for h in history)) / len(history)
        r = _nonlinear(pct, exponent=1.6)
        status = f"历史{pct * 100:.0f}%分位 ({len(history)}条)"
    else:
        # 无历史时退化为绝对阈值 (HY OAS 历史经验: <3低 3-4正常 4-5紧张 >5危险)
        if v > 6:
            r = 100
        elif v > 5:
            r = 85
        elif v > 4:
            r = 65
        elif v > 3:
            r = 40
        else:
            r = 20
        status = "绝对阈值"
    return r, f"{v:.2f}%", status


def score_yield(us_10y, us_2y):
    """
    美债 10Y-2Y 利差。倒挂/解挂是衰退预警。
    利差因子方向特殊: 深度倒挂和解挂期都危险, 用规则映射(不单纯分位)。
    """
    spread = _finite(us_10y, "10Y收益率") - _finite(us_2y, "2Y收益率")
    if spread < -0.5:
        r, status = 80, "深度倒挂"
    elif spread < 0:
        r, status = 60, "轻度倒挂"
    elif 0 <= spread < 0.5:
        r, status = 70, "平坦曲线"  # 当前快照无法证明此前发生过倒挂。
    else:
        r, status = 30, "正常"
    return r, f"{spread:.2f}%", status


def score_technical(current_price, sma_200):
    """200日均线乖离率 (%)。偏离越大越超买(正乖离高风险)。"""
    price = _finite(current_price, "价格", 0)
    sma = _finite(sma_200, "200日均线", 0)
    if price == 0 or sma == 0:
        raise ValueError("价格和200日均线必须大于0")
    dev_pct = (price - sma) / sma * 100
    d = dev_pct
    if d > 25:
        r = 100
    elif d > 20:
        r = 85
    elif d > 15:
        r = 65
    elif d > 5:
        r = 40
    elif d < -10:
        r = 10
    else:
        r = 20
    return r, f"{d:.1f}%", _level_text(r)


def score_sentiment(fear_val):
    """
    CNN恐慌贪婪指数 (0-100)。反向指标: 极度贪婪高风险。
    注意: 极度恐慌(<20)给低分但不给0 —— 恐慌也可能是崩盘进行中。
    """
    v = _finite(fear_val, "恐慌贪婪指数", 0, 100)
    if v > 80:
        r = 100
    elif v > 60:
        r = 70
    elif v < 20:
        r = 25  # 极度恐慌: 可能见底, 但也可能是崩盘中段, 给低中分而非0
    else:
        r = 40
    return r, f"{int(v)}", _level_text(r)


def _level_text(r):
    if r >= 80:
        return "极高"
    if r >= 60:
        return "偏高"
    if r >= 40:
        return "中性"
    return "低"


# ------------------------------------------------------------------
# 共振检测: 多个因子同时进入极端区(>=80分)时, 风险非线性放大
# ------------------------------------------------------------------
def detect_resonance(factor_scores, threshold=80):
    """
    factor_scores: dict key->risk_score(0-100)
    返回 (extreme_count, resonance_active, boost)
      extreme_count: 进入极端区的因子数
      resonance_active: 是否触发共振(>=3个因子极端)
      boost: 共振加分 (非线性, 最多+15)
    """
    extreme = sum(1 for s in factor_scores.values() if s >= threshold)
    active = extreme >= 3
    # 共振加分: 3个极端+8, 4个+12, 5个及以上+15 (非线性但封顶)
    boost = {3: 8, 4: 12}.get(extreme, 15 if extreme >= 5 else 0)
    return extreme, active, boost


# ------------------------------------------------------------------
# 主评分函数
# ------------------------------------------------------------------
def calculate_risk(factor_inputs, weights, hy_history=None):
    """
    综合风险评分。

    参数:
      factor_inputs: dict, 含各因子原始值:
          buffett(%)  shiller  hy_oas(%)  us_10y  us_2y
          price  sma200  fear(0-100)
      weights: dict, key->0~1 权重 (会自动归一化)
      hy_history: 高收益债利差历史list (用于分位数, 可None)

    返回:
      (final_score, detail_dict)
      detail_dict 含各因子(risk,value,status)、覆盖率、生效权重、贡献与警报。
      缺失因子重新归一化并标注覆盖率；无可用因子时final_score为None。
    """
    wn = normalize_weights(weights)
    scorers = {
        "buffett": score_buffett,
        "shiller": score_shiller,
        "credit": lambda value: score_credit(value, hy_history),
        "yield": score_yield,
        "technical": score_technical,
        "sentiment": score_sentiment,
    }
    details, factor_scores = {}, {}
    missing = []
    for key, meta in FACTOR_META.items():
        if wn[key] == 0:
            details[key] = (None, "—", "已禁用")
            continue
        values = [factor_inputs.get(name) for name in meta["inputs"]]
        if any(value is None for value in values):
            details[key] = (None, "—", "数据缺失")
            missing.append(key)
            continue
        details[key] = scorers[key](*values)
        factor_scores[key] = details[key][0]

    coverage = sum(wn[k] for k in factor_scores)
    effective = (
        {k: wn[k] / coverage if k in factor_scores else 0.0 for k in FACTOR_META}
        if coverage
        else dict.fromkeys(FACTOR_META, 0.0)
    )
    if not factor_scores:
        details["_meta"] = {
            "coverage": 0.0,
            "missing_factors": missing,
            "normalized_weights": wn,
            "effective_weights": effective,
            "resonance": False,
            "extreme_count": 0,
            "boost": 0,
            "weighted": 0.0,
            "redline_floor": 0.0,
            "contributions": {},
        }
        return None, details

    # 加权总分
    contributions = {k: factor_scores[k] * effective[k] for k in factor_scores}
    weighted = sum(contributions.values())

    # 共振检测
    extreme_cnt, resonance, boost = detect_resonance(factor_scores)

    # 最大单项红线: 防止极端信号被温和因子稀释。
    # 若某因子 >=90 (极端), 总分下限托底到该因子分的 70%;
    # 这捕捉"单点突破即危机"的非线性 (如2008雷曼: 信用利差爆表但其他因子温和)。
    max_factor = max(factor_scores, key=factor_scores.get)
    max_score = factor_scores[max_factor]
    redline_floor = 0.0
    if max_score >= 90:
        redline_floor = max_score * 0.7  # 单因子爆表 -> 总分至少 63
    elif max_score >= 80:
        redline_floor = max_score * 0.6  # 单因子高危 -> 总分至少 48

    final = max(weighted, redline_floor)  # 红线托底
    final = min(100.0, final + boost)  # 共振加分, 封顶100

    details["_meta"] = {
        "coverage": round(coverage, 6),
        "missing_factors": missing,
        "normalized_weights": wn,
        "effective_weights": effective,
        "contributions": contributions,
        "redline_adjustment": max(0.0, redline_floor - weighted),
        "cap_adjustment": max(0.0, max(weighted, redline_floor) + boost - 100.0),
        "weighted": round(weighted, 1),
        "resonance": resonance,
        "extreme_count": extreme_cnt,
        "boost": boost,
        "max_factor": FACTOR_META[max_factor]["name"],
        "max_score": max_score,
        "max_factor_key": max_factor,
        "redline_floor": round(redline_floor, 1),
    }
    return round(final, 1), details


# 近似情景参数，含构造价格和情绪值；用于压力对比，不是历史回测数据。
HISTORICAL_SCENARIOS = {
    "2000 互联网泡沫 (情景)": {
        "buffett": 145.0,
        "shiller": 44.2,
        "hy_oas": 4.5,
        "us_10y": 6.2,
        "us_2y": 6.6,
        "fear": 90,
        "price": 115,
        "sma200": 100,
    },
    "2008 次贷危机 (情景)": {
        "buffett": 110.0,
        "shiller": 27.5,
        "hy_oas": 3.0,
        "us_10y": 4.6,
        "us_2y": 4.2,
        "fear": 75,
        "price": 108,
        "sma200": 100,
    },
    "2022 加息熊市 (情景)": {
        "buffett": 195.0,
        "shiller": 38.3,
        "hy_oas": 3.1,
        "us_10y": 1.6,
        "us_2y": 0.8,
        "fear": 75,
        "price": 112,
        "sma200": 100,
    },
}
