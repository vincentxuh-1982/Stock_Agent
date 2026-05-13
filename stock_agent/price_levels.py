from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .models import AnalysisResult, Position, RealtimeResult
from .recommender import nearest_resistance, nearest_support


@dataclass(frozen=True)
class PricePlan:
    entry_low: float
    entry_high: float
    add_price: float
    reduce_price: float
    stop_loss: float
    target_price: float
    note: str

    @property
    def entry_zone(self) -> str:
        return price_range(self.entry_low, self.entry_high)


def analysis_price_plan(
    result: AnalysisResult,
    position: Optional[Position] = None,
) -> PricePlan:
    close = result.close
    atr = max(float(result.metrics.get("atr14", 0) or 0), close * 0.015)
    support = nearest_support(result)
    resistance = nearest_resistance(result)
    forecast = result.forecast_3d
    up_probability = forecast.up_probability if forecast else 0.5

    entry_low = max(support, close - 0.8 * atr)
    entry_high = min(close, support + 0.6 * atr)
    if result.score_short >= 3 and up_probability >= 0.58:
        entry_low = max(support, close - 0.5 * atr)
        entry_high = close

    stop_loss = max(0.0, support - 0.8 * atr)
    target_price = max(resistance, close + 1.8 * atr)
    reduce_price = max(resistance, close + 1.2 * atr)
    add_price = min(entry_high, close)
    note = "回踩不破支撑可分批，突破目标位后移动止盈。"

    if position:
        hard_stop = position.cost * (1 - position.max_loss_pct) if position.cost else 0.0
        stop_loss = max(hard_stop, stop_loss)
        add_price = support if result.score_short >= 1 else 0.0
        reduce_price = stop_loss if result.score_short < 0 else reduce_price
        target_price = max(target_price, position.cost * 1.12 if position.cost else target_price)
        note = "加仓只在支撑确认后执行；跌破减仓价先控回撤。"

    return PricePlan(
        entry_low=round_price(entry_low),
        entry_high=round_price(max(entry_low, entry_high)),
        add_price=round_price(add_price),
        reduce_price=round_price(reduce_price),
        stop_loss=round_price(stop_loss),
        target_price=round_price(target_price),
        note=note,
    )


def realtime_price_plan(
    result: RealtimeResult,
    position: Optional[Position] = None,
) -> PricePlan:
    price = result.quote.price
    day_range = max(result.quote.high - result.quote.low, price * 0.01)
    entry_low = max(result.support, price - 0.5 * day_range)
    entry_high = min(price, result.support + 0.35 * day_range)
    stop_loss = max(0.0, result.support - 0.35 * day_range)
    reduce_price = max(result.resistance, price + 0.6 * day_range)
    target_price = max(result.resistance, price + 1.2 * day_range)
    add_price = min(entry_high, price)
    note = "盘中价位随支撑、量能和压力位快速变化，优先小仓位验证。"

    if position:
        hard_stop = position.cost * (1 - position.max_loss_pct) if position.cost else 0.0
        stop_loss = max(hard_stop, stop_loss)
        add_price = result.support if result.urgency >= 2 else 0.0
        if result.status in {"跌破支撑", "放量回落", "盘中走弱", "支撑告警"}:
            reduce_price = stop_loss
        target_price = max(target_price, position.cost * 1.1 if position.cost else target_price)
        note = "持仓股先看减仓价和目标价；加仓必须等支撑位重新确认。"

    return PricePlan(
        entry_low=round_price(entry_low),
        entry_high=round_price(max(entry_low, entry_high)),
        add_price=round_price(add_price),
        reduce_price=round_price(reduce_price),
        stop_loss=round_price(stop_loss),
        target_price=round_price(target_price),
        note=note,
    )


def price_range(low: float, high: float) -> str:
    if low <= 0 and high <= 0:
        return "-"
    if abs(low - high) < 0.005:
        return f"{high:.2f}"
    return f"{low:.2f}-{high:.2f}"


def price_or_dash(value: float) -> str:
    return "-" if value <= 0 else f"{value:.2f}"


def round_price(value: float) -> float:
    if value <= 0:
        return 0.0
    if value < 1:
        return round(value, 3)
    return round(value, 2)
