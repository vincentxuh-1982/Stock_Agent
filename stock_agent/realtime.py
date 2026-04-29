from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Dict, Iterable, List, Optional, Tuple

from .analyzer import analyze_instrument
from .config import AgentConfig
from .data_providers import (
    MarketDataProvider,
    call_with_retries,
    normalize_a_symbol,
    normalize_hk_symbol,
    provider_from_config,
)
from .indicators import safe_mean
from .models import Instrument, MarketSessionStatus, Quote, RealtimeResult
from .prediction import predict_intraday
from .trading_hours import split_by_session


def run_realtime_analysis(
    config: AgentConfig,
) -> Tuple[List[RealtimeResult], List[MarketSessionStatus], List[str]]:
    provider = provider_from_config(config)
    quote_provider = realtime_provider_from_config(config, provider)
    errors: List[str] = []
    instruments = unique_instruments(config.indices + config.watchlist)
    active_sessions, inactive_sessions = split_by_session(
        instruments,
        timezone=config.timezone,
    )
    active_instruments = [status.instrument for status in active_sessions]
    quotes = quote_provider.quotes(active_instruments, errors) if active_instruments else {}

    results: List[RealtimeResult] = []
    end = date.today()
    start = end - timedelta(days=max(config.lookback_days, 90) * 2)
    session_by_symbol = {
        instrument_key(status.instrument): status for status in active_sessions
    }
    for instrument in active_instruments:
        quote = quotes.get(instrument.symbol)
        if quote is None:
            errors.append(f"{instrument.symbol} {instrument.name}: 未获取到实时行情")
            continue
        try:
            bars = provider.history(instrument, start=start, end=end)
            if len(bars) < 30:
                raise ValueError(f"not enough bars: {len(bars)}")
            technical = analyze_instrument(instrument, bars[-config.lookback_days :])
            results.append(
                analyze_realtime(
                    instrument,
                    quote,
                    technical,
                    bars,
                    session=session_by_symbol.get(instrument_key(instrument)),
                )
            )
        except Exception as exc:
            message = str(exc).replace("\n", " ")
            errors.append(f"{instrument.symbol} {instrument.name}: {message[:180]}")

    return sorted(results, key=lambda item: item.urgency, reverse=True), inactive_sessions, errors


class RealtimeQuoteProvider:
    def quotes(
        self,
        instruments: Iterable[Instrument],
        errors: List[str],
    ) -> Dict[str, Quote]:
        raise NotImplementedError


class SyntheticRealtimeQuoteProvider(RealtimeQuoteProvider):
    def __init__(self, history_provider: MarketDataProvider) -> None:
        self.history_provider = history_provider

    def quotes(
        self,
        instruments: Iterable[Instrument],
        errors: List[str],
    ) -> Dict[str, Quote]:
        output: Dict[str, Quote] = {}
        for instrument in instruments:
            try:
                bars = self.history_provider.history(instrument)
                latest = bars[-1]
                previous = bars[-2]
                output[instrument.symbol] = Quote(
                    instrument=instrument,
                    price=latest.close,
                    change=latest.close - previous.close,
                    change_pct=(latest.close - previous.close) / previous.close,
                    open=latest.open,
                    high=latest.high,
                    low=latest.low,
                    prev_close=previous.close,
                    volume=latest.volume,
                    amount=latest.amount,
                    timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    source="synthetic",
                )
            except Exception as exc:
                errors.append(f"{instrument.symbol} {instrument.name}: {exc}")
        return output


class AkshareRealtimeQuoteProvider(RealtimeQuoteProvider):
    def quotes(
        self,
        instruments: Iterable[Instrument],
        errors: List[str],
    ) -> Dict[str, Quote]:
        try:
            import akshare as ak  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "AKShare realtime provider requires `python3 -m pip install -r requirements.txt`."
            ) from exc

        instruments_list = list(instruments)
        output: Dict[str, Quote] = {}
        cn_indices = [item for item in instruments_list if is_cn_index(item)]
        hk_indices = [item for item in instruments_list if is_hk_index(item)]
        a_stocks = [
            item
            for item in instruments_list
            if not is_hk_instrument(item) and not is_cn_index(item)
        ]
        hk_stocks = [item for item in instruments_list if is_hk_stock(item)]

        if cn_indices:
            output.update(load_cn_index_quotes(ak, cn_indices, errors))
        if hk_indices:
            output.update(load_hk_index_quotes(ak, hk_indices, errors))
        if a_stocks:
            output.update(load_a_quotes(ak, a_stocks, errors))
        if hk_stocks:
            output.update(load_hk_quotes(ak, hk_stocks, errors))
        return output


def realtime_provider_from_config(
    config: AgentConfig,
    history_provider: MarketDataProvider,
) -> RealtimeQuoteProvider:
    if config.data_provider.lower() == "akshare":
        return AkshareRealtimeQuoteProvider()
    return SyntheticRealtimeQuoteProvider(history_provider)


def load_a_quotes(ak, instruments: List[Instrument], errors: List[str]) -> Dict[str, Quote]:
    output: Dict[str, Quote] = {}
    try:
        frame = call_with_retries(lambda: ak.stock_zh_a_spot_em(), attempts=2)
        rows = frame.to_dict("records")
        by_symbol = {str(row.get("代码", "")).zfill(6): row for row in rows}
        source = "东方财富 A 股实时"
    except Exception:
        frame = call_with_retries(lambda: ak.stock_zh_a_spot(), attempts=2)
        rows = frame.to_dict("records")
        by_symbol = {
            normalize_a_quote_code(str(row.get("代码", ""))): row for row in rows
        }
        source = "新浪 A 股实时"

    for instrument in instruments:
        symbol = instrument.provider_symbol or instrument.symbol
        row = by_symbol.get(symbol.zfill(6))
        if row is None:
            errors.append(f"{instrument.symbol} {instrument.name}: 实时快照中未找到")
            continue
        output[instrument.symbol] = quote_from_row(instrument, row, source)
    return output


def load_hk_quotes(ak, instruments: List[Instrument], errors: List[str]) -> Dict[str, Quote]:
    output: Dict[str, Quote] = {}
    try:
        frame = call_with_retries(lambda: ak.stock_hk_spot_em(), attempts=2)
        rows = frame.to_dict("records")
        by_symbol = {
            normalize_hk_symbol(str(row.get("代码", ""))): row for row in rows
        }
        source = "东方财富港股实时"
    except Exception:
        frame = call_with_retries(lambda: ak.stock_hk_spot(), attempts=2)
        rows = frame.to_dict("records")
        by_symbol = {
            normalize_hk_symbol(str(row.get("代码", ""))): row for row in rows
        }
        source = "新浪港股实时"

    for instrument in instruments:
        symbol = normalize_hk_symbol(instrument.provider_symbol or instrument.symbol)
        row = by_symbol.get(symbol)
        if row is None:
            errors.append(f"{instrument.symbol} {instrument.name}: 实时快照中未找到")
            continue
        output[instrument.symbol] = quote_from_row(instrument, row, source)
    return output


def load_cn_index_quotes(
    ak,
    instruments: List[Instrument],
    errors: List[str],
) -> Dict[str, Quote]:
    output: Dict[str, Quote] = {}
    try:
        frame = call_with_retries(lambda: ak.stock_zh_index_spot_em(), attempts=2)
        rows = frame.to_dict("records")
        by_symbol = {
            normalize_a_quote_code(str(row.get("代码", ""))): row for row in rows
        }
        source = "东方财富内地指数实时"
    except Exception:
        frame = call_with_retries(lambda: ak.stock_zh_index_spot_sina(), attempts=2)
        rows = frame.to_dict("records")
        by_symbol = {
            normalize_a_quote_code(str(row.get("代码", ""))): row for row in rows
        }
        source = "新浪内地指数实时"

    for instrument in instruments:
        symbol = instrument.provider_symbol or instrument.symbol
        row = by_symbol.get(normalize_a_quote_code(symbol))
        if row is None:
            errors.append(f"{instrument.symbol} {instrument.name}: 指数实时快照中未找到")
            continue
        output[instrument.symbol] = quote_from_row(instrument, row, source)
    return output


def load_hk_index_quotes(
    ak,
    instruments: List[Instrument],
    errors: List[str],
) -> Dict[str, Quote]:
    output: Dict[str, Quote] = {}
    try:
        frame = call_with_retries(lambda: ak.stock_hk_index_spot_em(), attempts=2)
        rows = frame.to_dict("records")
        by_symbol = {
            normalize_hk_index_symbol(str(row.get("代码", ""))): row for row in rows
        }
        source = "东方财富港股指数实时"
    except Exception:
        frame = call_with_retries(lambda: ak.stock_hk_index_spot_sina(), attempts=2)
        rows = frame.to_dict("records")
        by_symbol = {
            normalize_hk_index_symbol(str(row.get("代码", ""))): row for row in rows
        }
        source = "新浪港股指数实时"

    for instrument in instruments:
        symbol = instrument.provider_symbol or instrument.symbol
        row = by_symbol.get(normalize_hk_index_symbol(symbol))
        if row is None:
            errors.append(f"{instrument.symbol} {instrument.name}: 港股指数实时快照中未找到")
            continue
        output[instrument.symbol] = quote_from_row(instrument, row, source)
    return output


def analyze_realtime(
    instrument: Instrument,
    quote: Quote,
    technical,
    bars,
    session: Optional[MarketSessionStatus] = None,
) -> RealtimeResult:
    price = quote.price
    support = nearest_price_below(
        price,
        technical.supports
        + [
            technical.metrics["ma5"],
            technical.metrics["ma10"],
            technical.metrics["ma20"],
        ],
    )
    resistance = nearest_price_above(
        price,
        technical.resistances
        + [
            technical.metrics["ma5"],
            technical.metrics["ma10"],
            technical.metrics["ma20"],
            technical.metrics["bb_upper"],
        ],
    )
    range_position = intraday_range_position(quote)
    amount_ratio = activity_ratio(quote, bars)
    urgency, signals = realtime_signals(quote, technical, support, resistance, amount_ratio)
    status = realtime_status(
        quote,
        technical,
        support,
        resistance,
        range_position,
        amount_ratio,
    )
    action = realtime_action(status, quote, support, resistance, technical)
    intraday_forecast = predict_intraday(
        technical,
        quote,
        support,
        resistance,
        range_position,
        amount_ratio,
        status,
    )

    return RealtimeResult(
        instrument=instrument,
        quote=quote,
        technical=technical,
        status=status,
        urgency=urgency,
        action=action,
        signals=signals,
        support=support,
        resistance=resistance,
        range_position=range_position,
        amount_ratio=amount_ratio,
        intraday_forecast=intraday_forecast,
        session=session,
    )


def quote_from_row(instrument: Instrument, row: Dict[str, object], source: str) -> Quote:
    price = number_value(row, "最新价")
    change_pct = number_value(row, "涨跌幅") / 100
    change = number_value(row, "涨跌额")
    prev_close = number_value(row, "昨收")
    open_price = number_value(row, "今开")
    high = number_value(row, "最高")
    low = number_value(row, "最低")
    timestamp = str(row.get("时间戳") or row.get("日期时间") or "")
    volume = number_value(row, "成交量", default=0)
    if source == "新浪 A 股实时":
        volume = volume / 100
    return Quote(
        instrument=instrument,
        price=price,
        change=change,
        change_pct=change_pct,
        open=open_price,
        high=high,
        low=low,
        prev_close=prev_close,
        volume=volume,
        amount=number_value(row, "成交额", default=0),
        timestamp=timestamp,
        source=source,
    )


def activity_ratio(quote: Quote, bars) -> float:
    avg_amount20 = safe_mean([bar.amount for bar in bars[-20:] if bar.amount > 0])
    avg_volume20 = safe_mean([bar.volume for bar in bars[-20:] if bar.volume > 0])
    amount_ratio = quote.amount / avg_amount20 if avg_amount20 and quote.amount else 0.0
    volume_ratio = quote.volume / avg_volume20 if avg_volume20 and quote.volume else 0.0

    if 0 < amount_ratio <= 20 and 0 < volume_ratio <= 20:
        return max(amount_ratio, volume_ratio)
    if 0 < amount_ratio <= 20:
        return amount_ratio
    if 0 < volume_ratio <= 20:
        return volume_ratio
    return amount_ratio


def realtime_signals(
    quote: Quote,
    technical,
    support: float,
    resistance: float,
    amount_ratio: float,
) -> Tuple[int, List[str]]:
    urgency = 0
    signals: List[str] = []
    price = quote.price

    if quote.change_pct >= 0.05:
        urgency += 3
        signals.append("盘中涨幅超过 5%")
    elif quote.change_pct <= -0.05:
        urgency += 3
        signals.append("盘中跌幅超过 5%")
    elif abs(quote.change_pct) >= 0.03:
        urgency += 2
        signals.append("盘中涨跌幅超过 3%")

    if resistance and price > resistance:
        urgency += 2
        signals.append("现价突破最近压力位")
    if support and price < support:
        urgency += 2
        signals.append("现价跌破最近支撑位")

    if quote.open and quote.prev_close:
        gap = (quote.open - quote.prev_close) / quote.prev_close
        if abs(gap) >= 0.02:
            urgency += 1
            signals.append(f"开盘跳空 {gap:.1%}")

    if quote.high > quote.low and quote.prev_close:
        amplitude = (quote.high - quote.low) / quote.prev_close
        if amplitude >= max(0.05, technical.metrics["atr_pct"]):
            urgency += 1
            signals.append("盘中振幅高于常态波动")

    if amount_ratio >= 0.8:
        urgency += 1
        signals.append(f"成交额已达 20 日均额 {amount_ratio:.0%}")

    if not signals:
        signals.append("盘中暂无明显异动")
    return urgency, signals


def realtime_status(
    quote: Quote,
    technical,
    support: float,
    resistance: float,
    range_position: float,
    amount_ratio: float,
) -> str:
    price = quote.price
    if support and price < support and quote.change_pct < -0.015:
        return "跌破支撑"
    if support and price < support:
        return "支撑告警"
    if resistance and price > resistance and quote.change_pct > 0.015:
        return "突破压力"
    if resistance and price > resistance:
        return "压力试探"
    if quote.change_pct <= -0.03 and amount_ratio >= 0.8:
        return "放量回落"
    if quote.change_pct >= 0.03 and amount_ratio >= 0.8:
        return "放量上涨"
    if quote.change_pct >= 0.03 and range_position >= 0.75:
        return "盘中强势"
    if quote.change_pct <= -0.03 and range_position <= 0.35:
        return "盘中走弱"
    if technical.score_mid >= 2 and quote.change_pct > 0:
        return "趋势延续"
    if technical.score_mid <= -2 and quote.change_pct < 0:
        return "弱势延续"
    return "震荡观察"


def realtime_action(status: str, quote: Quote, support: float, resistance: float, technical) -> str:
    if status == "突破压力":
        return f"观察能否维持在 {resistance:.2f} 上方，放量回落则先不追。"
    if status == "跌破支撑":
        return f"先看风险，若收不回 {support:.2f}，短线应降低预期。"
    if status == "支撑告警":
        return f"已贴近/小幅跌破 {support:.2f}，观察能否快速收回。"
    if status == "盘中强势":
        return f"强势但避免追高，优先等回踩 {support:.2f} 附近确认。"
    if status == "放量上涨":
        return f"资金关注度提升，观察能否突破并站稳 {resistance:.2f}。"
    if status == "放量回落":
        return f"放量回落需要谨慎，先看 {support:.2f} 附近承接。"
    if status == "盘中走弱":
        return f"等待止跌信号，重新站回 {support:.2f} 后再评估。"
    if status == "趋势延续":
        return f"趋势仍在，盯 {resistance:.2f} 的突破质量和尾盘承接。"
    if status == "弱势延续":
        return f"弱势未改，反抽到 {resistance:.2f} 附近但无量突破时谨慎。"
    if status == "压力试探":
        return f"正在试探 {resistance:.2f}，需要放量并维持在其上方。"
    return f"暂无强动作信号，区间参考 {support:.2f} - {resistance:.2f}。"


def nearest_price_below(price: float, values: List[float]) -> float:
    candidates = [value for value in values if value > 0 and value <= price]
    if candidates:
        return max(candidates)
    positive = [value for value in values if value > 0]
    return min(positive) if positive else price


def nearest_price_above(price: float, values: List[float]) -> float:
    candidates = [value for value in values if value > 0 and value >= price]
    if candidates:
        return min(candidates)
    positive = [value for value in values if value > 0]
    return max(positive) if positive else price


def intraday_range_position(quote: Quote) -> float:
    if quote.high <= quote.low:
        return 0.5
    return max(0.0, min(1.0, (quote.price - quote.low) / (quote.high - quote.low)))


def is_hk_stock(instrument: Instrument) -> bool:
    kind = instrument.kind.lower()
    return kind in {"hk_stock", "h_stock"} or (
        instrument.market.upper() == "HK" and kind != "hk_index"
    )


def is_hk_index(instrument: Instrument) -> bool:
    return instrument.kind.lower() == "hk_index"


def is_hk_instrument(instrument: Instrument) -> bool:
    return is_hk_stock(instrument) or is_hk_index(instrument)


def is_cn_index(instrument: Instrument) -> bool:
    return instrument.kind.lower() in {"index", "cn_index", "a_index"}


def normalize_a_quote_code(value: str) -> str:
    return normalize_a_symbol(value).replace("sh", "").replace("sz", "").replace("bj", "")


def normalize_hk_index_symbol(value: str) -> str:
    return value.upper().replace(".HK", "").replace("HK", "").strip()


def instrument_key(instrument: Instrument) -> str:
    return f"{instrument.kind.lower()}:{instrument.market.upper()}:{instrument.symbol}"


def unique_instruments(instruments: Iterable[Instrument]) -> List[Instrument]:
    output: List[Instrument] = []
    seen = set()
    for instrument in instruments:
        key = instrument_key(instrument)
        if key in seen:
            continue
        seen.add(key)
        output.append(instrument)
    return output


def number_value(row: Dict[str, object], name: str, default: float = 0.0) -> float:
    value = row.get(name, default)
    if value is None or value == "-":
        return default
    try:
        number = float(value)
        if math.isnan(number):
            return default
        return number
    except (TypeError, ValueError):
        return default
