"""定时扫描：美东工作日 NOTIFY_TIME（默认10:00）按默认权重计算VOO风险评分，并推送逐因子说明到Telegram。

python notifier.py        常驻，按计划扫描（docker compose 的 notifier 服务）
python notifier.py --now  立即扫描并发送一次，用于测试
"""

import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

import data_fetcher
import risk_history
import risk_model

ROOT = Path(__file__).resolve().parent
TICKER = "VOO"
NY = ZoneInfo("America/New_York")
MAX_LATE_SECONDS = 1800  # 主机休眠导致错过计划超过30分钟则跳过当天，不补发


def parse_time(text):
    """'HH:MM'（美东时间）-> (hour, minute)；格式或范围错误抛ValueError。"""
    parsed = datetime.strptime(text.strip(), "%H:%M")
    return parsed.hour, parsed.minute


def next_run(now, hour=10, minute=0):
    """now之后的下一个工作日hour:minute（美东时间，自动处理夏令时）。"""
    now = now.astimezone(NY)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    while target.weekday() >= 5:
        target += timedelta(days=1)
    return target


def format_message(score, details, market, notes=()):
    meta = details["_meta"]
    contributions = meta["contributions"]
    lines = []
    if score is None:
        lines.append(f"{TICKER} 风险评分：暂无可用因子")
    else:
        if score >= risk_model.HIGH_RISK_SCORE:
            lines.append(f"⚠️ 高风险警告：评分已达 {risk_model.HIGH_RISK_SCORE} 以上")
        lines.append(f"{TICKER} 风险评分 {score:.1f} / 100 · {risk_model.risk_band(score)[0]}")
    lines.append(
        f"数据日期 {market.get('as_of') or '无（演示行情）'} · 模型 v{risk_model.MODEL_VERSION}"
        f" · 覆盖率 {meta['coverage']:.0%}"
    )

    lines += ["", "因子明细"]
    order = sorted(risk_model.FACTOR_META, key=lambda k: contributions.get(k, -1), reverse=True)
    for index, key in enumerate(order, start=1):
        name = risk_model.FACTOR_META[key]["name"]
        risk, value, status = details[key]
        if risk is None:
            lines.append(f"{index}. {name}  {status}")
        else:
            lines.append(
                f"{index}. {name}  {value} → 风险 {risk:.0f} · {status}"
                f" · 贡献 {contributions[key]:.1f}（权重 {meta['effective_weights'][key]:.0%}）"
            )

    adjustments = []
    if meta.get("redline_adjustment"):
        adjustments.append(
            f"红线托底 +{meta['redline_adjustment']:.1f}"
            f"（{meta['max_factor']} {meta['max_score']:.0f}分）"
        )
    if meta["boost"]:
        adjustments.append(f"共振加分 +{meta['boost']}（{meta['extreme_count']}个因子≥80）")
    if meta.get("cap_adjustment"):
        adjustments.append(f"封顶 -{meta['cap_adjustment']:.1f}")
    if adjustments:
        lines += ["", "调整：" + " / ".join(adjustments)]

    lines.append("")
    if contributions:
        lines.append(f"主要驱动：{risk_model.FACTOR_META[order[0]]['name']}")
    if market.get("is_mock"):
        lines.append("VOO行情获取失败，均线乖离未计分")
    if meta["missing_factors"]:
        names = "、".join(risk_model.FACTOR_META[k]["name"] for k in meta["missing_factors"])
        lines.append(f"缺失：{names}（暂定评分，未写入历史）")
    lines += list(notes)
    lines.append("评分为模型信号，不代表崩盘概率。")
    return "\n".join(lines)


def scan():
    """获取数据、按默认权重评分、完整读数写入历史，返回待发送文本。"""
    data = data_fetcher.fetch_all(TICKER, fred_api_key=os.environ.get("FRED_API_KEY"))
    market = data["market"]
    readings = {
        key: item.get("value") if item.get("ok") else None
        for key, item in data.items()
        if key != "market" and isinstance(item, dict)
    }
    hy_history = data.get("credit", {}).get("history")
    weights = risk_model.DEFAULT_WEIGHTS
    score, details = risk_model.calculate_risk(
        risk_model.build_factor_inputs(readings, market), weights, hy_history=hy_history
    )
    notes = []
    complete = not market.get("is_mock", True) and details["_meta"]["coverage"] >= 0.999999
    if complete and score is not None:
        profile = risk_history.scoring_profile(weights, risk_model.credit_method(hy_history))
        try:
            risk_history.record_risk_score(
                TICKER, score, observation_date=market["as_of"], profile=profile
            )
        except (OSError, ValueError) as error:
            notes.append(f"风险历史保存失败：{error}")
    return format_message(score, details, market, notes)


def send_telegram(text):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    body = json.dumps({"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": text}).encode()
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            if not json.load(response).get("ok"):
                raise RuntimeError("Telegram拒绝了消息")
    except Exception as error:
        # 异常文本可能带URL，去掉bot token再抛出。
        raise RuntimeError(str(error).replace(token, "[已隐藏]")) from None


def log(message):
    print(f"[{datetime.now(NY):%Y-%m-%d %H:%M:%S %Z}] {message}", flush=True)


def run_once():
    try:
        text = scan()
    except Exception as error:
        text = f"{TICKER} 风险扫描失败：{data_fetcher._safe_error(error)}"
    send_telegram(text)
    log("已发送：" + text.splitlines()[0])


def main():
    load_dotenv(ROOT / ".env")
    missing = [k for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID") if not os.environ.get(k)]
    if missing:
        sys.exit("缺少环境变量：" + ", ".join(missing))
    if "--now" in sys.argv[1:]:
        run_once()
        return
    try:
        run_at = parse_time(os.environ.get("NOTIFY_TIME") or "10:00")
    except ValueError:
        sys.exit("NOTIFY_TIME 格式应为 HH:MM（美东时间），例如 10:00")
    while True:
        target = next_run(datetime.now(NY), *run_at)
        log(f"下次扫描 {target:%Y-%m-%d %H:%M %Z}")
        # 用UTC时间戳比较并分段睡眠：跨夏令时准确，主机从休眠恢复后也能及时察觉。
        while (wait := target.timestamp() - time.time()) > 0:
            time.sleep(min(wait, 60))
        if time.time() - target.timestamp() > MAX_LATE_SECONDS:
            log("错过计划时间（主机可能休眠），跳过本次")
            continue
        try:
            run_once()
        except Exception as error:
            log(f"发送失败：{error}")


if __name__ == "__main__":
    main()
