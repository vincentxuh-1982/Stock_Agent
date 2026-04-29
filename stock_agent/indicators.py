from __future__ import annotations

from statistics import mean, pstdev
from typing import Dict, Iterable, List, Optional, Tuple

from .models import Bar


def latest_metrics(bars: List[Bar]) -> Dict[str, float]:
    if len(bars) < 30:
        raise ValueError("At least 30 bars are required for analysis.")

    closes = [bar.close for bar in bars]
    highs = [bar.high for bar in bars]
    lows = [bar.low for bar in bars]
    volumes = [bar.volume for bar in bars]

    ma5 = moving_average(closes, 5)
    ma10 = moving_average(closes, 10)
    ma20 = moving_average(closes, 20)
    ma60 = moving_average(closes, 60)
    rsi14 = rsi(closes, 14)
    macd_line, signal_line, hist = macd(closes)
    atr14 = atr(bars, 14)
    bb_upper, bb_mid, bb_lower = bollinger(closes, 20, 2)

    high20 = max(highs[-20:])
    low20 = min(lows[-20:])
    high60 = max(highs[-60:]) if len(highs) >= 60 else max(highs)
    low60 = min(lows[-60:]) if len(lows) >= 60 else min(lows)
    last_close = closes[-1]
    prev_close = closes[-2]
    volume5 = safe_mean(volumes[-5:])
    volume20 = safe_mean(volumes[-20:])
    position60 = (
        (last_close - low60) / (high60 - low60) if high60 > low60 else 0.5
    )

    return {
        "close": last_close,
        "prev_close": prev_close,
        "change_pct": pct_change(last_close, prev_close),
        "ma5": last_value(ma5),
        "ma10": last_value(ma10),
        "ma20": last_value(ma20),
        "ma60": last_value(ma60),
        "rsi14": last_value(rsi14),
        "macd": last_value(macd_line),
        "macd_signal": last_value(signal_line),
        "macd_hist": last_value(hist),
        "macd_hist_prev": previous_value(hist),
        "atr14": last_value(atr14),
        "atr_pct": last_value(atr14) / last_close if last_close else 0.0,
        "bb_upper": last_value(bb_upper),
        "bb_mid": last_value(bb_mid),
        "bb_lower": last_value(bb_lower),
        "volume_ratio_5_20": volume5 / volume20 if volume20 else 1.0,
        "high20": high20,
        "low20": low20,
        "high60": high60,
        "low60": low60,
        "position60": position60,
        "drawdown_from_60_high": pct_change(last_close, high60),
    }


def moving_average(values: List[float], window: int) -> List[Optional[float]]:
    output: List[Optional[float]] = []
    for index in range(len(values)):
        if index + 1 < window:
            output.append(None)
        else:
            output.append(mean(values[index + 1 - window : index + 1]))
    return output


def ema(values: List[float], window: int) -> List[Optional[float]]:
    if not values:
        return []
    alpha = 2 / (window + 1)
    output: List[Optional[float]] = []
    current: Optional[float] = None
    for index, value in enumerate(values):
        if index + 1 < window:
            output.append(None)
            continue
        if current is None:
            current = mean(values[index + 1 - window : index + 1])
        else:
            current = alpha * value + (1 - alpha) * current
        output.append(current)
    return output


def rsi(values: List[float], window: int = 14) -> List[Optional[float]]:
    output: List[Optional[float]] = [None]
    gains: List[float] = []
    losses: List[float] = []

    for index in range(1, len(values)):
        delta = values[index] - values[index - 1]
        gains.append(max(delta, 0))
        losses.append(abs(min(delta, 0)))
        if index < window:
            output.append(None)
            continue
        recent_gains = gains[-window:]
        recent_losses = losses[-window:]
        avg_gain = safe_mean(recent_gains)
        avg_loss = safe_mean(recent_losses)
        if avg_loss == 0:
            output.append(100.0)
        else:
            relative_strength = avg_gain / avg_loss
            output.append(100 - (100 / (1 + relative_strength)))
    return output


def macd(
    values: List[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> Tuple[List[Optional[float]], List[Optional[float]], List[Optional[float]]]:
    fast_ema = ema(values, fast)
    slow_ema = ema(values, slow)
    macd_line: List[Optional[float]] = []
    macd_values: List[float] = []

    for fast_value, slow_value in zip(fast_ema, slow_ema):
        if fast_value is None or slow_value is None:
            macd_line.append(None)
        else:
            value = fast_value - slow_value
            macd_line.append(value)
            macd_values.append(value)

    signal_values = ema(macd_values, signal)
    signal_line: List[Optional[float]] = []
    signal_iter = iter(signal_values)
    for value in macd_line:
        if value is None:
            signal_line.append(None)
        else:
            signal_line.append(next(signal_iter))

    hist: List[Optional[float]] = []
    for macd_value, signal_value in zip(macd_line, signal_line):
        if macd_value is None or signal_value is None:
            hist.append(None)
        else:
            hist.append(macd_value - signal_value)
    return macd_line, signal_line, hist


def atr(bars: List[Bar], window: int = 14) -> List[Optional[float]]:
    true_ranges: List[float] = []
    for index, bar in enumerate(bars):
        if index == 0:
            true_ranges.append(bar.high - bar.low)
            continue
        prev_close = bars[index - 1].close
        true_ranges.append(
            max(
                bar.high - bar.low,
                abs(bar.high - prev_close),
                abs(bar.low - prev_close),
            )
        )
    return moving_average(true_ranges, window)


def bollinger(
    values: List[float],
    window: int = 20,
    width: float = 2.0,
) -> Tuple[List[Optional[float]], List[Optional[float]], List[Optional[float]]]:
    upper: List[Optional[float]] = []
    middle: List[Optional[float]] = []
    lower: List[Optional[float]] = []
    for index in range(len(values)):
        if index + 1 < window:
            upper.append(None)
            middle.append(None)
            lower.append(None)
            continue
        recent = values[index + 1 - window : index + 1]
        mid = mean(recent)
        deviation = pstdev(recent)
        upper.append(mid + width * deviation)
        middle.append(mid)
        lower.append(mid - width * deviation)
    return upper, middle, lower


def safe_mean(values: Iterable[float]) -> float:
    materialized = list(values)
    if not materialized:
        return 0.0
    return sum(materialized) / len(materialized)


def pct_change(current: float, previous: float) -> float:
    if previous == 0:
        return 0.0
    return (current - previous) / previous


def last_value(values: List[Optional[float]]) -> float:
    for value in reversed(values):
        if value is not None:
            return float(value)
    return 0.0


def previous_value(values: List[Optional[float]]) -> float:
    seen_latest = False
    for value in reversed(values):
        if value is None:
            continue
        if not seen_latest:
            seen_latest = True
            continue
        return float(value)
    return 0.0
