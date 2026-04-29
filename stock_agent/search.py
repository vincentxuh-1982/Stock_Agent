from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, Iterable, List, Optional

from .config import AgentConfig
from .models import Instrument
from .news import parse_percent


@dataclass
class InstrumentSearchResult:
    symbol: str
    name: str
    kind: str
    market: str
    provider_symbol: str = ""
    price: Optional[float] = None
    change_pct: Optional[float] = None
    source: str = ""
    already_added: bool = False

    def as_dict(self) -> Dict[str, object]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "kind": self.kind,
            "market": self.market,
            "provider_symbol": self.provider_symbol or self.symbol,
            "price": self.price,
            "change_pct": self.change_pct,
            "source": self.source,
            "already_added": self.already_added,
        }


STATIC_INDICES = [
    InstrumentSearchResult("000001", "上证指数", "cn_index", "CN", "000001", source="内置指数"),
    InstrumentSearchResult("399001", "深证成指", "cn_index", "CN", "399001", source="内置指数"),
    InstrumentSearchResult("399006", "创业板指", "cn_index", "CN", "399006", source="内置指数"),
    InstrumentSearchResult("000300", "沪深300", "cn_index", "CN", "000300", source="内置指数"),
    InstrumentSearchResult("000688", "科创50", "cn_index", "CN", "000688", source="内置指数"),
    InstrumentSearchResult("HSI", "恒生指数", "hk_index", "HK", "HSI", source="内置指数"),
]

_CACHE: Dict[str, object] = {}
_CACHE_TIME: Dict[str, datetime] = {}


def search_instruments(
    config: AgentConfig,
    query: str,
    limit: int = 24,
) -> List[Dict[str, object]]:
    normalized = normalize_query(query)
    if not normalized:
        return []

    results: List[InstrumentSearchResult] = []
    results.extend(search_existing_config(config, normalized))
    results.extend(search_indices(normalized))
    if should_search_a(normalized, results):
        results.extend(search_a_stocks(normalized, limit=limit))
    if should_search_hk(normalized, results, limit):
        results.extend(search_hk_stocks(normalized, limit=limit))
    results = mark_existing(config, dedupe_results(results))
    results = sorted(
        results,
        key=lambda item: ranking_key(item, normalized),
    )
    return [item.as_dict() for item in results[:limit]]


def should_search_a(query: str, current_results: List[InstrumentSearchResult]) -> bool:
    if not current_results:
        return True
    return query.isdigit() and len(query) == 6


def should_search_hk(
    query: str,
    current_results: List[InstrumentSearchResult],
    limit: int,
) -> bool:
    if len(current_results) == 0:
        return True
    if query.startswith("hk") or "港" in query:
        return True
    if query.isdigit() and (query.startswith("0") or len(query) <= 5):
        return True
    return False


def search_existing_config(
    config: AgentConfig,
    query: str,
) -> List[InstrumentSearchResult]:
    results: List[InstrumentSearchResult] = []
    for instrument in config.watchlist + config.indices:
        if matches(instrument.symbol, instrument.name, query):
            results.append(
                InstrumentSearchResult(
                    symbol=instrument.symbol,
                    name=instrument.name,
                    kind=instrument.kind,
                    market=instrument.market,
                    provider_symbol=instrument.provider_symbol or instrument.symbol,
                    source="当前配置",
                )
            )
    return results


def search_indices(query: str) -> List[InstrumentSearchResult]:
    return [item for item in STATIC_INDICES if matches(item.symbol, item.name, query)]


def search_a_stocks(query: str, limit: int) -> List[InstrumentSearchResult]:
    frame = cached_frame("a_stock_names", load_a_stock_names, ttl_minutes=60)
    if frame is None:
        return []
    results: List[InstrumentSearchResult] = []
    for row in frame.to_dict("records"):
        symbol = str(row.get("code") or row.get("代码") or "").zfill(6)
        name = str(row.get("name") or row.get("名称") or "").strip()
        if not symbol or not name or not matches(symbol, name, query):
            continue
        results.append(
            InstrumentSearchResult(
                symbol=symbol,
                name=name,
                kind="a_stock",
                market="CN",
                provider_symbol=symbol,
                source="AKShare A股代码表",
            )
        )
        if len(results) >= limit:
            break
    return results


def search_hk_stocks(query: str, limit: int) -> List[InstrumentSearchResult]:
    frame = cached_frame("hk_spot", load_hk_spot, ttl_minutes=5)
    if frame is None:
        return []
    results: List[InstrumentSearchResult] = []
    for row in frame.to_dict("records"):
        symbol = normalize_hk_code(str(row.get("代码", "")).strip())
        name = str(row.get("名称") or row.get("股票名称") or "").strip()
        if not symbol or not name or not matches(symbol, name, query):
            continue
        results.append(
            InstrumentSearchResult(
                symbol=symbol,
                name=name,
                kind="hk_stock",
                market="HK",
                provider_symbol=symbol,
                price=number_value(row.get("最新价")),
                change_pct=parse_percent(row.get("涨跌幅")) / 100,
                source="AKShare 港股实时",
            )
        )
        if len(results) >= limit:
            break
    return results


def load_a_stock_names():
    import akshare as ak  # type: ignore

    return ak.stock_info_a_code_name()


def load_hk_spot():
    import akshare as ak  # type: ignore

    try:
        return ak.stock_hk_spot_em()
    except Exception:
        return ak.stock_hk_spot()


def cached_frame(name: str, loader, ttl_minutes: int):
    now = datetime.now()
    cached_at = _CACHE_TIME.get(name)
    if cached_at and now - cached_at < timedelta(minutes=ttl_minutes):
        return _CACHE.get(name)
    try:
        frame = loader()
    except Exception:
        return _CACHE.get(name)
    _CACHE[name] = frame
    _CACHE_TIME[name] = now
    return frame


def mark_existing(
    config: AgentConfig,
    results: Iterable[InstrumentSearchResult],
) -> List[InstrumentSearchResult]:
    watch_keys = {instrument_key(item) for item in config.watchlist}
    index_keys = {instrument_key(item) for item in config.indices}
    output = []
    for result in results:
        key = result_key(result)
        if result.kind.endswith("index"):
            result.already_added = key in index_keys
        else:
            result.already_added = key in watch_keys
        output.append(result)
    return output


def dedupe_results(results: Iterable[InstrumentSearchResult]) -> List[InstrumentSearchResult]:
    output: List[InstrumentSearchResult] = []
    seen = set()
    for result in results:
        key = result_key(result)
        if key in seen:
            continue
        seen.add(key)
        output.append(result)
    return output


def ranking_key(result: InstrumentSearchResult, query: str):
    symbol = normalize_query(result.symbol)
    name = normalize_query(result.name)
    exact = 0 if query in {symbol, name} else 1
    starts = 0 if symbol.startswith(query) or name.startswith(query) else 1
    kind_rank = 0 if result.source == "当前配置" else 1
    return (exact, starts, kind_rank, result.market, result.symbol)


def matches(symbol: str, name: str, query: str) -> bool:
    return query in normalize_query(symbol) or query in normalize_query(name)


def instrument_key(instrument: Instrument) -> str:
    return f"{instrument.kind}:{normalize_query(instrument.symbol)}"


def result_key(result: InstrumentSearchResult) -> str:
    return f"{result.kind}:{normalize_query(result.symbol)}"


def normalize_query(value: str) -> str:
    return str(value).strip().lower().replace(" ", "").replace(".", "")


def normalize_hk_code(value: str) -> str:
    cleaned = value.upper().replace("HK", "").replace(".", "")
    return cleaned.zfill(5) if cleaned.isdigit() else cleaned


def number_value(value: object) -> Optional[float]:
    if value is None or value == "-":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
