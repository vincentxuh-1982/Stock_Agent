from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, List, Optional


@dataclass
class Instrument:
    symbol: str
    name: str
    kind: str = "stock"
    market: str = "CN"
    provider_symbol: Optional[str] = None
    themes: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "Instrument":
        return cls(
            symbol=str(data["symbol"]),
            name=str(data.get("name", data["symbol"])),
            kind=str(data.get("kind", "stock")),
            market=str(data.get("market", "CN")),
            provider_symbol=(
                str(data["provider_symbol"]) if data.get("provider_symbol") else None
            ),
            themes=[str(x) for x in data.get("themes", [])],
        )


@dataclass
class Bar:
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    amount: float = 0.0


@dataclass
class DirectionalForecast:
    horizon: str
    up_probability: float
    down_probability: float
    expected_move_pct: float
    confidence: float
    bias: str
    condition: str
    signals: List[str] = field(default_factory=list)


@dataclass
class AnalysisResult:
    instrument: Instrument
    as_of: date
    close: float
    change_pct: float
    short_view: str
    mid_view: str
    risk_level: str
    score_short: int
    score_mid: int
    metrics: Dict[str, float]
    signals: List[str]
    supports: List[float]
    resistances: List[float]
    forecast_3d: Optional[DirectionalForecast] = None


@dataclass
class Quote:
    instrument: Instrument
    price: float
    change: float
    change_pct: float
    open: float
    high: float
    low: float
    prev_close: float
    volume: float = 0.0
    amount: float = 0.0
    timestamp: str = ""
    source: str = ""


@dataclass
class MarketSessionStatus:
    instrument: Instrument
    market: str
    is_trading: bool
    phase: str
    session_text: str
    checked_at: str
    next_open: str = ""


@dataclass
class RealtimeResult:
    instrument: Instrument
    quote: Quote
    technical: AnalysisResult
    status: str
    urgency: int
    action: str
    signals: List[str]
    support: float
    resistance: float
    range_position: float
    amount_ratio: float
    intraday_forecast: Optional[DirectionalForecast] = None
    session: Optional[MarketSessionStatus] = None


@dataclass
class NewsItem:
    title: str
    link: str
    source: str
    published_at: Optional[datetime] = None
    summary: str = ""


@dataclass
class Hotspot:
    theme: str
    score: int
    headlines: List[NewsItem]
    related_symbols: List[str]
    candidates: List["StockCandidate"] = field(default_factory=list)


@dataclass
class StockCandidate:
    symbol: str
    name: str
    market: str
    reason: str
    score: int = 0
    change_pct: float = 0.0


@dataclass
class Position:
    symbol: str
    name: str
    cost: float
    shares: float
    kind: str = "stock"
    market: str = "CN"
    provider_symbol: Optional[str] = None
    target_weight: float = 0.0
    max_loss_pct: float = 0.08
    realized_pnl: float = 0.0

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "Position":
        return cls(
            symbol=str(data["symbol"]),
            name=str(data.get("name", data["symbol"])),
            cost=float(data.get("cost", 0) or 0),
            shares=float(data.get("shares", 0) or 0),
            kind=str(data.get("kind", "stock")),
            market=str(data.get("market", "CN")),
            provider_symbol=(
                str(data["provider_symbol"]) if data.get("provider_symbol") else None
            ),
            target_weight=float(data.get("target_weight", 0) or 0),
            max_loss_pct=float(data.get("max_loss_pct", 0.08) or 0.08),
            realized_pnl=float(data.get("realized_pnl", 0) or 0),
        )

    def to_dict(self) -> Dict[str, object]:
        output: Dict[str, object] = {
            "symbol": self.symbol,
            "name": self.name,
            "cost": self.cost,
            "shares": self.shares,
            "kind": self.kind,
            "market": self.market,
            "target_weight": self.target_weight,
            "max_loss_pct": self.max_loss_pct,
            "realized_pnl": self.realized_pnl,
        }
        if self.provider_symbol:
            output["provider_symbol"] = self.provider_symbol
        return output


@dataclass
class TradeRecord:
    timestamp: str
    symbol: str
    name: str
    side: str
    shares: float
    price: float
    realized_pnl: float = 0.0
    cost_after: float = 0.0
    shares_after: float = 0.0

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "TradeRecord":
        return cls(
            timestamp=str(data.get("timestamp", "")),
            symbol=str(data.get("symbol", "")),
            name=str(data.get("name", "")),
            side=str(data.get("side", "")),
            shares=float(data.get("shares", 0) or 0),
            price=float(data.get("price", 0) or 0),
            realized_pnl=float(data.get("realized_pnl", 0) or 0),
            cost_after=float(data.get("cost_after", 0) or 0),
            shares_after=float(data.get("shares_after", 0) or 0),
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "timestamp": self.timestamp,
            "symbol": self.symbol,
            "name": self.name,
            "side": self.side,
            "shares": self.shares,
            "price": self.price,
            "realized_pnl": self.realized_pnl,
            "cost_after": self.cost_after,
            "shares_after": self.shares_after,
        }


@dataclass
class Portfolio:
    cash: float
    positions: List[Position]
    trades: List[TradeRecord] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "Portfolio":
        return cls(
            cash=float(data.get("cash", 0) or 0),
            positions=[Position.from_dict(x) for x in data.get("positions", [])],
            trades=[TradeRecord.from_dict(x) for x in data.get("trades", [])],
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "cash": self.cash,
            "positions": [position.to_dict() for position in self.positions],
            "trades": [trade.to_dict() for trade in self.trades],
        }
