from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from typing import Iterable, List, Optional, Set

from .models import Instrument, MarketSessionStatus

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python 3.9+ provides zoneinfo.
    ZoneInfo = None  # type: ignore


@dataclass(frozen=True)
class TradingWindow:
    start: time
    end: time
    label: str

    @property
    def text(self) -> str:
        return f"{self.start.strftime('%H:%M')}-{self.end.strftime('%H:%M')}"


CN_WINDOWS = [
    TradingWindow(time(9, 15), time(9, 25), "集合竞价"),
    TradingWindow(time(9, 30), time(11, 30), "上午交易"),
    TradingWindow(time(13, 0), time(15, 0), "下午交易"),
]

HK_WINDOWS = [
    TradingWindow(time(9, 0), time(9, 30), "开市前时段"),
    TradingWindow(time(9, 30), time(12, 0), "上午交易"),
    TradingWindow(time(13, 0), time(16, 0), "下午交易"),
    TradingWindow(time(16, 0), time(16, 10), "收市竞价"),
]

CN_REALTIME_SPAN = TradingWindow(time(9, 30), time(15, 0), "实时段")
HK_REALTIME_SPAN = TradingWindow(time(9, 30), time(16, 0), "实时段")


def market_session_status(
    instrument: Instrument,
    now: Optional[datetime] = None,
    timezone: str = "Asia/Shanghai",
    use_remote_calendar: bool = True,
) -> MarketSessionStatus:
    checked_at = local_datetime(now, timezone)
    market = trading_market(instrument)
    windows = trading_windows(market)
    realtime_window = realtime_span(market)
    session_text = session_text_for(market, realtime_window)

    if not is_business_day(checked_at.date(), market, use_remote_calendar):
        return MarketSessionStatus(
            instrument=instrument,
            market=market,
            is_trading=False,
            phase="周末/节假日",
            session_text=session_text,
            checked_at=format_checked_at(checked_at),
            next_open=format_next_open(
                next_open_datetime(
                    checked_at,
                    market,
                    realtime_window,
                    use_remote_calendar,
                )
            ),
        )

    current = checked_at.time()
    if realtime_window.start <= current <= realtime_window.end:
        return MarketSessionStatus(
            instrument=instrument,
            market=market,
            is_trading=True,
            phase=active_phase(current, windows),
            session_text=session_text,
            checked_at=format_checked_at(checked_at),
        )

    phase = closed_phase(current, realtime_window)
    return MarketSessionStatus(
        instrument=instrument,
        market=market,
        is_trading=False,
        phase=phase,
        session_text=session_text,
        checked_at=format_checked_at(checked_at),
        next_open=format_next_open(
            next_open_datetime(
                checked_at,
                market,
                realtime_window,
                use_remote_calendar,
            )
        ),
    )


def split_by_session(
    instruments: Iterable[Instrument],
    now: Optional[datetime] = None,
    timezone: str = "Asia/Shanghai",
) -> tuple[List[MarketSessionStatus], List[MarketSessionStatus]]:
    statuses = [
        market_session_status(instrument, now=now, timezone=timezone)
        for instrument in instruments
    ]
    active = [status for status in statuses if status.is_trading]
    inactive = [status for status in statuses if not status.is_trading]
    return active, inactive


def trading_market(instrument: Instrument) -> str:
    kind = instrument.kind.lower()
    if kind in {"hk_stock", "h_stock", "hk_index"} or instrument.market.upper() == "HK":
        return "HK"
    return "CN"


def market_label(market: str) -> str:
    if market == "HK":
        return "港股"
    return "A股/内地指数"


def trading_windows(market: str) -> List[TradingWindow]:
    return HK_WINDOWS if market == "HK" else CN_WINDOWS


def realtime_span(market: str) -> TradingWindow:
    return HK_REALTIME_SPAN if market == "HK" else CN_REALTIME_SPAN


def session_text_for(market: str, window: TradingWindow) -> str:
    return f"{market_label(market)} {window.text}（午休仍计入）"


def closed_phase(current: time, realtime_window: TradingWindow) -> str:
    if current < realtime_window.start:
        return "盘前"
    if current > realtime_window.end:
        return "盘后"
    return "盘中休市"


def active_phase(current: time, windows: List[TradingWindow]) -> str:
    for window in windows:
        if window.start <= current <= window.end:
            return window.label
    return "盘中休市"


def next_open_datetime(
    checked_at: datetime,
    market: str,
    realtime_window: TradingWindow,
    use_remote_calendar: bool,
) -> datetime:
    current = checked_at.time()
    if is_business_day(checked_at.date(), market, use_remote_calendar):
        if current < realtime_window.start:
            return checked_at.replace(
                hour=realtime_window.start.hour,
                minute=realtime_window.start.minute,
                second=0,
                microsecond=0,
            )

    candidate = checked_at.date() + timedelta(days=1)
    while not is_business_day(candidate, market, use_remote_calendar):
        candidate += timedelta(days=1)
    return datetime.combine(
        candidate,
        realtime_window.start,
        tzinfo=checked_at.tzinfo,
    )


def is_business_day(
    day: date,
    market: str,
    use_remote_calendar: bool = True,
) -> bool:
    if day.weekday() >= 5:
        return False
    if market != "CN" or not use_remote_calendar:
        return True
    trade_dates = cn_trade_dates()
    if not trade_dates:
        return True
    if day < min(trade_dates) or day > max(trade_dates):
        return True
    return day in trade_dates


@lru_cache(maxsize=1)
def cn_trade_dates() -> Set[date]:
    try:
        import akshare as ak  # type: ignore

        frame = ak.tool_trade_date_hist_sina()
    except Exception:
        return set()

    dates: Set[date] = set()
    for value in frame.iloc[:, 0].tolist():
        try:
            dates.add(parse_trade_date(str(value)))
        except ValueError:
            continue
    return dates


def parse_trade_date(value: str) -> date:
    value = value.strip()
    for pattern in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(value, pattern).date()
        except ValueError:
            continue
    raise ValueError(f"Unsupported trade date: {value}")


def local_datetime(now: Optional[datetime], timezone: str) -> datetime:
    tz = ZoneInfo(timezone) if ZoneInfo else None
    if now is None:
        return datetime.now(tz)
    if now.tzinfo is None:
        return now.replace(tzinfo=tz)
    return now.astimezone(tz)


def format_checked_at(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M")


def format_next_open(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M")
