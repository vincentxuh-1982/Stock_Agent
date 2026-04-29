from __future__ import annotations

import csv
import json
import math
import random
import re
import time
from abc import ABC, abstractmethod
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, TypeVar

from .config import AgentConfig
from .models import Bar, Instrument

T = TypeVar("T")
HISTORY_CACHE_TTL_SECONDS = 30 * 60


class MarketDataProvider(ABC):
    @abstractmethod
    def history(
        self,
        instrument: Instrument,
        start: Optional[date] = None,
        end: Optional[date] = None,
    ) -> List[Bar]:
        raise NotImplementedError


class CsvMarketDataProvider(MarketDataProvider):
    def __init__(self, data_dir: str) -> None:
        self.data_dir = Path(data_dir)

    def history(
        self,
        instrument: Instrument,
        start: Optional[date] = None,
        end: Optional[date] = None,
    ) -> List[Bar]:
        path = self.data_dir / f"{instrument.symbol}.csv"
        if not path.exists():
            raise FileNotFoundError(
                f"Missing CSV for {instrument.symbol}: {path}. "
                "Expected columns: date,open,high,low,close,volume,amount"
            )

        bars: List[Bar] = []
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                bar = Bar(
                    date=parse_date(str(row["date"])),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row.get("volume") or 0),
                    amount=float(row.get("amount") or 0),
                )
                if start and bar.date < start:
                    continue
                if end and bar.date > end:
                    continue
                bars.append(bar)
        return sorted(bars, key=lambda x: x.date)


class SyntheticMarketDataProvider(MarketDataProvider):
    """Deterministic fake prices for smoke tests and demos."""

    def history(
        self,
        instrument: Instrument,
        start: Optional[date] = None,
        end: Optional[date] = None,
    ) -> List[Bar]:
        end_date = end or date.today()
        start_date = start or (end_date - timedelta(days=260))
        seed = sum(ord(ch) for ch in instrument.symbol)
        rng = random.Random(seed)
        phase = seed % 17
        base = 12 + (seed % 60)
        trend = 0.0009 if "GROWTH" in instrument.symbol else 0.00025
        if instrument.kind.lower() in {"index", "cn_index", "a_index", "hk_index"}:
            base = 3000 + (seed % 500)
            trend = 0.0002

        bars: List[Bar] = []
        close = float(base)
        current = start_date
        day_index = 0
        while current <= end_date:
            if current.weekday() >= 5:
                current += timedelta(days=1)
                continue

            wave = math.sin((day_index + phase) / 8) * 0.012
            shock = rng.uniform(-0.015, 0.015)
            daily_return = trend + wave + shock
            previous = close
            close = max(1.0, close * (1 + daily_return))
            high = max(previous, close) * (1 + rng.uniform(0.002, 0.018))
            low = min(previous, close) * (1 - rng.uniform(0.002, 0.018))
            open_price = previous * (1 + rng.uniform(-0.008, 0.008))
            volume = 1_000_000 * (1 + abs(daily_return) * 25 + rng.random())
            bars.append(
                Bar(
                    date=current,
                    open=round(open_price, 2),
                    high=round(high, 2),
                    low=round(low, 2),
                    close=round(close, 2),
                    volume=round(volume, 2),
                    amount=round(volume * close, 2),
                )
            )
            current += timedelta(days=1)
            day_index += 1

        return bars


class AkshareMarketDataProvider(MarketDataProvider):
    def __init__(self, config: AgentConfig) -> None:
        self.adjust = config.adjust
        self.cache_dir = Path(config.data_dir) / "akshare_cache"

    def history(
        self,
        instrument: Instrument,
        start: Optional[date] = None,
        end: Optional[date] = None,
    ) -> List[Bar]:
        try:
            import akshare as ak  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "AKShare provider requires `python3 -m pip install -r requirements.txt`."
            ) from exc

        start_date = format_ak_date(start or date.today() - timedelta(days=365))
        end_date = format_ak_date(end or date.today())
        symbol = instrument.provider_symbol or instrument.symbol
        cache_path = self.history_cache_path(instrument, start_date, end_date)
        cached = read_cached_history(cache_path)
        if cached is not None:
            return cached

        kind = instrument.kind.lower()
        if kind in {"index", "cn_index", "a_index"}:
            try:
                frame = call_with_retries(
                    lambda: ak.index_zh_a_hist(
                        symbol=symbol,
                        period="daily",
                        start_date=start_date,
                        end_date=end_date,
                    )
                )
            except Exception:
                frame = call_with_retries(
                    lambda: ak.stock_zh_index_daily(
                        symbol=cn_index_symbol_for_sina(symbol)
                    )
                )
        elif kind in {"hk_stock", "h_stock"}:
            hk_symbol = normalize_hk_symbol(symbol)
            try:
                frame = call_with_retries(
                    lambda: ak.stock_hk_hist(
                        symbol=hk_symbol,
                        period="daily",
                        start_date=start_date,
                        end_date=end_date,
                        adjust=self.adjust if self.adjust in {"qfq", "hfq"} else "",
                    )
                )
            except Exception:
                frame = call_with_retries(
                    lambda: ak.stock_hk_daily(
                        symbol=hk_symbol,
                        adjust=self.adjust if self.adjust in {"qfq", "qfq-factor"} else "",
                    )
                )
        elif kind == "hk_index":
            frame = call_with_retries(lambda: ak.stock_hk_index_daily_sina(symbol=symbol))
        else:
            a_symbol = normalize_a_symbol(symbol)
            try:
                frame = call_with_retries(
                    lambda: ak.stock_zh_a_hist(
                        symbol=symbol,
                        period="daily",
                        start_date=start_date,
                        end_date=end_date,
                        adjust=self.adjust,
                    )
                )
            except Exception:
                try:
                    frame = call_with_retries(
                        lambda: ak.stock_zh_a_hist_tx(
                            symbol=a_symbol,
                            start_date=start_date,
                            end_date=end_date,
                            adjust=self.adjust if self.adjust in {"qfq", "hfq"} else "",
                        )
                    )
                except Exception:
                    frame = call_with_retries(
                        lambda: ak.stock_zh_a_daily(
                            symbol=a_symbol,
                            start_date=start_date,
                            end_date=end_date,
                            adjust=(
                                self.adjust
                                if self.adjust in {"qfq", "hfq", "qfq-factor", "hfq-factor"}
                                else ""
                            ),
                        )
                    )

        rows: Iterable[Dict[str, object]] = frame.to_dict("records")
        bars = [bar_from_akshare_row(row) for row in rows]
        if start:
            bars = [bar for bar in bars if bar.date >= start]
        if end:
            bars = [bar for bar in bars if bar.date <= end]
        bars = sorted(bars, key=lambda x: x.date)
        write_cached_history(cache_path, bars)
        return bars

    def history_cache_path(
        self,
        instrument: Instrument,
        start_date: str,
        end_date: str,
    ) -> Path:
        symbol = safe_cache_part(instrument.provider_symbol or instrument.symbol)
        kind = safe_cache_part(instrument.kind)
        adjust = safe_cache_part(self.adjust)
        return self.cache_dir / f"{kind}_{symbol}_{adjust}_{start_date}_{end_date}.json"


def provider_from_config(config: AgentConfig) -> MarketDataProvider:
    provider_name = config.data_provider.lower()
    if provider_name == "csv":
        return CsvMarketDataProvider(config.data_dir)
    if provider_name == "akshare":
        return AkshareMarketDataProvider(config)
    if provider_name == "synthetic":
        return SyntheticMarketDataProvider()
    raise ValueError(f"Unsupported data_provider: {config.data_provider}")


def bar_from_akshare_row(row: Dict[str, object]) -> Bar:
    volume = float(value_of(row, "成交量", "volume", default=0) or 0)
    amount = float(value_of(row, "成交额", "amount", default=0) or 0)
    if volume == 0 and "amount" in row and "成交额" not in row:
        volume = amount
        amount = 0.0
    return Bar(
        date=parse_date(str(value_of(row, "日期", "date"))),
        open=float(value_of(row, "开盘", "open")),
        high=float(value_of(row, "最高", "high")),
        low=float(value_of(row, "最低", "low")),
        close=float(value_of(row, "收盘", "close")),
        volume=volume,
        amount=amount,
    )


def value_of(row: Dict[str, object], *names: str, default: object = None) -> object:
    for name in names:
        if name in row:
            return row[name]
    if default is not None:
        return default
    raise KeyError(f"None of the columns exist: {', '.join(names)}")


def normalize_hk_symbol(symbol: str) -> str:
    value = symbol.upper().replace(".HK", "").replace("HK", "")
    return value.zfill(5)


def normalize_a_symbol(symbol: str) -> str:
    value = symbol.lower()
    if value.startswith(("sh", "sz", "bj")):
        return value
    if value.startswith(("6", "688", "689")):
        return f"sh{value}"
    if value.startswith(("0", "2", "3")):
        return f"sz{value}"
    if value.startswith(("4", "8", "9")):
        return f"bj{value}"
    return value


def cn_index_symbol_for_sina(symbol: str) -> str:
    value = symbol.lower()
    if value.startswith(("sh", "sz")):
        return value
    if value.startswith("399"):
        return f"sz{value}"
    if value.startswith("000"):
        return f"sh{value}"
    return value


def read_cached_history(path: Path) -> Optional[List[Bar]]:
    if not path.exists():
        return None
    if time.time() - path.stat().st_mtime > HISTORY_CACHE_TTL_SECONDS:
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return [
            Bar(
                date=parse_date(str(item["date"])),
                open=float(item["open"]),
                high=float(item["high"]),
                low=float(item["low"]),
                close=float(item["close"]),
                volume=float(item.get("volume") or 0),
                amount=float(item.get("amount") or 0),
            )
            for item in raw
        ]
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def write_cached_history(path: Path, bars: List[Bar]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = [
            {
                "date": bar.date.isoformat(),
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
                "amount": bar.amount,
            }
            for bar in bars
        ]
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError:
        return


def safe_cache_part(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z._-]+", "_", value.strip() or "unknown")


def call_with_retries(callback: Callable[[], T], attempts: int = 3) -> T:
    last_error: Optional[Exception] = None
    for attempt in range(attempts):
        try:
            return callback()
        except Exception as exc:
            last_error = exc
            if attempt == attempts - 1:
                break
            time.sleep(1.5 * (attempt + 1))
    assert last_error is not None
    raise last_error


def parse_date(value: str) -> date:
    for pattern in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(value.strip(), pattern).date()
        except ValueError:
            continue
    raise ValueError(f"Unsupported date format: {value}")


def format_ak_date(value: date) -> str:
    return value.strftime("%Y%m%d")
