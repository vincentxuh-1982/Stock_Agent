from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable, List, Optional, Tuple

from .config import AgentConfig
from .data_providers import MarketDataProvider
from .indicators import latest_metrics
from .models import AnalysisResult, Bar, Instrument
from .prediction import predict_next_3d


def analyze_instrument(instrument: Instrument, bars: List[Bar]) -> AnalysisResult:
    metrics = latest_metrics(bars)
    score_short, short_signals = short_term_score(metrics)
    score_mid, mid_signals = mid_term_score(metrics)
    risk_level = classify_risk(metrics)

    result = AnalysisResult(
        instrument=instrument,
        as_of=bars[-1].date,
        close=metrics["close"],
        change_pct=metrics["change_pct"],
        short_view=view_from_score(score_short),
        mid_view=view_from_score(score_mid),
        risk_level=risk_level,
        score_short=score_short,
        score_mid=score_mid,
        metrics=metrics,
        signals=short_signals + mid_signals + risk_signals(metrics),
        supports=dedupe_prices([metrics["low20"], metrics["ma20"], metrics["ma60"]]),
        resistances=dedupe_prices([metrics["high20"], metrics["high60"], metrics["bb_upper"]]),
    )
    result.forecast_3d = predict_next_3d(result, bars)
    return result


def analyze_many(
    instruments: Iterable[Instrument],
    provider: MarketDataProvider,
    config: AgentConfig,
    errors: Optional[List[str]] = None,
) -> List[AnalysisResult]:
    end = date.today()
    start = end - timedelta(days=max(config.lookback_days, 90) * 2)
    results: List[AnalysisResult] = []
    for instrument in instruments:
        try:
            bars = provider.history(instrument, start=start, end=end)
            if len(bars) < 30:
                raise ValueError(
                    f"not enough bars for {instrument.symbol}: {len(bars)}"
                )
            results.append(analyze_instrument(instrument, bars[-config.lookback_days :]))
        except Exception as exc:
            if errors is None:
                raise
            message = str(exc).replace("\n", " ")
            errors.append(f"{instrument.symbol} {instrument.name}: {message[:180]}")
    return results


def short_term_score(metrics: dict) -> Tuple[int, List[str]]:
    score = 0
    signals: List[str] = []
    close = metrics["close"]

    if close > metrics["ma5"] > metrics["ma10"]:
        score += 2
        signals.append("短线价格站上 MA5/MA10，动量偏强")
    elif close < metrics["ma5"] < metrics["ma10"]:
        score -= 2
        signals.append("短线跌破 MA5/MA10，动量转弱")

    if close > metrics["ma20"]:
        score += 1
        signals.append("收盘价位于 MA20 上方")
    else:
        score -= 1
        signals.append("收盘价位于 MA20 下方")

    if metrics["macd_hist"] > 0 and metrics["macd_hist"] > metrics["macd_hist_prev"]:
        score += 2
        signals.append("MACD 柱线为正且继续扩张")
    elif metrics["macd_hist"] < 0 and metrics["macd_hist"] < metrics["macd_hist_prev"]:
        score -= 2
        signals.append("MACD 柱线为负且继续走弱")

    if metrics["rsi14"] > 72:
        score -= 1
        signals.append("RSI 进入偏热区域，短线追高风险上升")
    elif metrics["rsi14"] < 32:
        score += 1
        signals.append("RSI 接近超卖区域，存在修复弹性")

    if metrics["volume_ratio_5_20"] > 1.35 and metrics["change_pct"] > 0:
        score += 1
        signals.append("近 5 日量能高于 20 日均量且价格上涨")
    elif metrics["volume_ratio_5_20"] > 1.35 and metrics["change_pct"] < 0:
        score -= 1
        signals.append("放量下跌，短线抛压需要观察")

    return clamp(score, -5, 5), signals


def mid_term_score(metrics: dict) -> Tuple[int, List[str]]:
    score = 0
    signals: List[str] = []
    close = metrics["close"]

    if close > metrics["ma20"] > metrics["ma60"]:
        score += 3
        signals.append("中期均线多头排列，趋势结构较好")
    elif close < metrics["ma20"] < metrics["ma60"]:
        score -= 3
        signals.append("中期均线空头排列，趋势结构偏弱")

    if metrics["position60"] > 0.75:
        score += 1
        signals.append("价格处于 60 日区间高位，趋势占优但需防波动")
    elif metrics["position60"] < 0.25:
        score -= 1
        signals.append("价格处于 60 日区间低位，中期仍需等待修复")

    if metrics["drawdown_from_60_high"] < -0.12:
        score -= 1
        signals.append("距离 60 日高点回撤较深")

    if metrics["atr_pct"] > 0.045:
        score -= 1
        signals.append("ATR 波动率偏高，中期仓位应更谨慎")

    return clamp(score, -5, 5), signals


def classify_risk(metrics: dict) -> str:
    if metrics["atr_pct"] > 0.055 or metrics["drawdown_from_60_high"] < -0.18:
        return "高"
    if metrics["atr_pct"] > 0.035 or metrics["rsi14"] > 75:
        return "中高"
    if metrics["atr_pct"] < 0.018 and metrics["position60"] > 0.45:
        return "中低"
    return "中"


def risk_signals(metrics: dict) -> List[str]:
    signals: List[str] = []
    if metrics["close"] > metrics["bb_upper"]:
        signals.append("收盘价突破布林上轨，强势同时伴随回撤风险")
    if metrics["close"] < metrics["bb_lower"]:
        signals.append("收盘价跌破布林下轨，弱势中可能出现技术反抽")
    return signals


def view_from_score(score: int) -> str:
    if score >= 4:
        return "偏强"
    if score >= 2:
        return "震荡偏强"
    if score <= -4:
        return "偏弱"
    if score <= -2:
        return "震荡偏弱"
    return "中性震荡"


def dedupe_prices(values: List[float]) -> List[float]:
    rounded = sorted({round(value, 2) for value in values if value > 0})
    return rounded


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))
