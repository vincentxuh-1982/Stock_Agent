from __future__ import annotations

import math
from typing import List, Sequence

from .models import AnalysisResult, Bar, DirectionalForecast, Quote


def predict_next_3d(result: AnalysisResult, bars: Sequence[Bar]) -> DirectionalForecast:
    metrics = result.metrics
    support = nearest_below(result.close, result.supports)
    resistance = nearest_above(result.close, result.resistances)
    score = 0.0
    signals: List[str] = []

    score += result.score_short * 0.55
    score += result.score_mid * 0.30

    if metrics["close"] > metrics["ma5"] > metrics["ma10"]:
        score += 0.55
        signals.append("短线站上 MA5/MA10")
    elif metrics["close"] < metrics["ma5"] < metrics["ma10"]:
        score -= 0.55
        signals.append("短线跌破 MA5/MA10")

    if metrics["macd_hist"] > 0 and metrics["macd_hist"] > metrics["macd_hist_prev"]:
        score += 0.50
        signals.append("MACD 红柱扩张")
    elif metrics["macd_hist"] < 0 and metrics["macd_hist"] < metrics["macd_hist_prev"]:
        score -= 0.50
        signals.append("MACD 绿柱走弱")

    if metrics["volume_ratio_5_20"] > 1.30 and metrics["change_pct"] > 0:
        score += 0.35
        signals.append("放量上涨")
    elif metrics["volume_ratio_5_20"] > 1.30 and metrics["change_pct"] < 0:
        score -= 0.45
        signals.append("放量下跌")

    if metrics["rsi14"] > 76:
        score -= 0.45
        signals.append("RSI 高位，追高胜率下降")
    elif metrics["rsi14"] < 32:
        score += 0.35
        signals.append("RSI 低位，存在修复弹性")

    if metrics["position60"] > 0.82:
        score += 0.20
        signals.append("60 日区间高位，趋势资金仍占优")
    elif metrics["position60"] < 0.22:
        score -= 0.25
        signals.append("60 日区间低位，修复前仍偏弱")

    if metrics["drawdown_from_60_high"] < -0.16:
        score -= 0.35
        signals.append("距离 60 日高点回撤较深")

    edge = squash(score) * volatility_damper(metrics["atr_pct"])
    up_probability = clamp_probability(0.50 + edge)
    down_probability = 1 - up_probability
    expected_move_pct = expected_horizon_move(metrics["atr_pct"], days=3)
    confidence = forecast_confidence(edge, len(signals), metrics["atr_pct"])
    bias = bias_label(up_probability, down_probability)
    condition = next_3d_condition(result, support, resistance)

    return DirectionalForecast(
        horizon="未来3个交易日",
        up_probability=up_probability,
        down_probability=down_probability,
        expected_move_pct=expected_move_pct,
        confidence=confidence,
        bias=bias,
        condition=condition,
        signals=signals[:5],
    )


def predict_intraday(
    result: AnalysisResult,
    quote: Quote,
    support: float,
    resistance: float,
    range_position: float,
    amount_ratio: float,
    status: str,
) -> DirectionalForecast:
    score = 0.0
    signals: List[str] = []

    score += result.score_short * 0.34
    score += result.score_mid * 0.14
    score += clamp(quote.change_pct / 0.03, -1.8, 1.8) * 0.45

    if range_position >= 0.75:
        score += 0.50
        signals.append("价格位于当日区间上沿")
    elif range_position <= 0.28:
        score -= 0.50
        signals.append("价格位于当日区间下沿")

    if amount_ratio >= 0.80 and quote.change_pct > 0:
        score += 0.55
        signals.append("放量上涨，资金参与度较高")
    elif amount_ratio >= 0.80 and quote.change_pct < 0:
        score -= 0.65
        signals.append("放量回落，抛压偏强")

    if resistance and quote.price > resistance:
        score += 0.75
        signals.append("现价突破压力位")
    elif support and quote.price < support:
        score -= 0.85
        signals.append("现价跌破支撑位")

    if quote.open and quote.price >= quote.open and quote.change_pct > 0:
        score += 0.20
        signals.append("现价守在开盘价上方")
    elif quote.open and quote.price < quote.open and quote.change_pct < 0:
        score -= 0.20
        signals.append("现价低于开盘价")

    score += status_adjustment(status)

    edge = squash(score) * volatility_damper(result.metrics["atr_pct"])
    up_probability = clamp_probability(0.50 + edge)
    down_probability = 1 - up_probability
    expected_move_pct = max(result.metrics["atr_pct"] * 0.65, abs(quote.change_pct) * 0.35)
    confidence = forecast_confidence(edge, len(signals), result.metrics["atr_pct"])
    bias = bias_label(up_probability, down_probability)
    condition = intraday_condition(quote, support, resistance, amount_ratio)

    return DirectionalForecast(
        horizon="当天剩余交易时段",
        up_probability=up_probability,
        down_probability=down_probability,
        expected_move_pct=expected_move_pct,
        confidence=confidence,
        bias=bias,
        condition=condition,
        signals=signals[:5],
    )


def next_3d_condition(result: AnalysisResult, support: float, resistance: float) -> str:
    metrics = result.metrics
    bullish = f"上涨需站稳 MA5/MA10，放量突破 {resistance:.2f} 则概率上修"
    bearish = f"跌破 {support:.2f} 或 MA20({metrics['ma20']:.2f})，概率转向下修"
    if metrics["rsi14"] > 76:
        bullish = f"若高位继续放量并站稳 {resistance:.2f}，上涨概率才维持"
    elif metrics["rsi14"] < 32:
        bullish = f"若缩量止跌并收回 MA5({metrics['ma5']:.2f})，修复概率上升"
    return f"{bullish}；{bearish}"


def intraday_condition(
    quote: Quote,
    support: float,
    resistance: float,
    amount_ratio: float,
) -> str:
    if quote.change_pct >= 0:
        bullish = f"维持在开盘价 {quote.open:.2f} 上方并突破 {resistance:.2f}，当日上行概率上修"
        bearish = f"跌回 {support:.2f} 下方且量能继续放大，转为回落优先"
    else:
        if quote.price >= support:
            bullish = f"守住 {support:.2f} 并收回开盘价 {quote.open:.2f}，修复概率上修"
        else:
            bullish = f"快速收回 {support:.2f} 并站上开盘价 {quote.open:.2f}，修复概率上修"
        bearish = f"不能收回 {support:.2f} 且成交额达到均额 {max(amount_ratio, 0):.0%}，下行概率上修"
    return f"{bullish}；{bearish}"


def status_adjustment(status: str) -> float:
    mapping = {
        "突破压力": 0.85,
        "压力试探": 0.35,
        "放量上涨": 0.60,
        "盘中强势": 0.50,
        "趋势延续": 0.35,
        "跌破支撑": -0.95,
        "支撑告警": -0.35,
        "放量回落": -0.70,
        "盘中走弱": -0.50,
        "弱势延续": -0.35,
    }
    return mapping.get(status, 0.0)


def forecast_probability_text(forecast: DirectionalForecast) -> str:
    return (
        f"涨 {forecast.up_probability:.0%} / 跌 {forecast.down_probability:.0%}"
        f"（{forecast.bias}，置信 {forecast.confidence:.0%}）"
    )


def forecast_condition_text(forecast: DirectionalForecast) -> str:
    move = forecast.expected_move_pct
    signals = "、".join(forecast.signals[:3]) or "信号中性"
    return f"{forecast.condition}；预估波动 ±{move:.1%}；依据：{signals}"


def bias_label(up_probability: float, down_probability: float) -> str:
    spread = up_probability - down_probability
    if spread >= 0.18:
        return "上行占优"
    if spread >= 0.08:
        return "略偏上行"
    if spread <= -0.18:
        return "下行占优"
    if spread <= -0.08:
        return "略偏下行"
    return "震荡均衡"


def expected_horizon_move(atr_pct: float, days: int) -> float:
    return max(0.015, min(0.18, atr_pct * math.sqrt(days)))


def forecast_confidence(edge: float, signal_count: int, atr_pct: float) -> float:
    confidence = 0.46 + min(abs(edge), 0.22) * 0.95 + min(signal_count, 5) * 0.025
    if atr_pct > 0.055:
        confidence -= 0.08
    elif atr_pct > 0.040:
        confidence -= 0.04
    return clamp(confidence, 0.35, 0.78)


def volatility_damper(atr_pct: float) -> float:
    if atr_pct > 0.065:
        return 0.70
    if atr_pct > 0.045:
        return 0.82
    return 1.0


def squash(score: float) -> float:
    return math.tanh(score / 5.2) * 0.28


def clamp_probability(value: float) -> float:
    return clamp(value, 0.18, 0.82)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def nearest_below(price: float, values: Sequence[float]) -> float:
    below = [value for value in values if 0 < value <= price]
    if below:
        return max(below)
    positive = [value for value in values if value > 0]
    return min(positive) if positive else price


def nearest_above(price: float, values: Sequence[float]) -> float:
    above = [value for value in values if 0 < value >= price]
    if above:
        return min(above)
    positive = [value for value in values if value > 0]
    return max(positive) if positive else price
