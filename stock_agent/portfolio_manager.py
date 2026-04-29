from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from .config import AgentConfig, load_mapping
from .models import Instrument, Portfolio, Position, TradeRecord


def load_portfolio_file(path: Optional[str]) -> Portfolio:
    if not path:
        return Portfolio(cash=0.0, positions=[])
    target = Path(path)
    if not target.exists():
        return Portfolio(cash=0.0, positions=[])
    return Portfolio.from_dict(load_mapping(str(target)))


def save_portfolio_file(path: str, portfolio: Portfolio) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(portfolio.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def add_position(
    path: str,
    payload: Dict[str, object],
    config: AgentConfig,
) -> Portfolio:
    portfolio = load_portfolio_file(path)
    position = position_from_payload(payload, config)
    if any(item.symbol == position.symbol for item in portfolio.positions):
        raise ValueError(f"{position.symbol} {position.name} 已在持仓中")
    portfolio.positions.append(position)
    save_portfolio_file(path, portfolio)
    return portfolio


def delete_position(path: str, symbol: str) -> Portfolio:
    portfolio = load_portfolio_file(path)
    before = len(portfolio.positions)
    portfolio.positions = [item for item in portfolio.positions if item.symbol != symbol]
    if len(portfolio.positions) == before:
        raise ValueError(f"{symbol} 不在持仓中")
    save_portfolio_file(path, portfolio)
    return portfolio


def record_trade(
    path: str,
    payload: Dict[str, object],
    config: AgentConfig,
) -> tuple[Portfolio, TradeRecord]:
    portfolio = load_portfolio_file(path)
    symbol = str(payload.get("symbol", "")).strip()
    side = str(payload.get("side", "")).strip().lower()
    shares = positive_float(payload.get("shares"), "shares")
    price = positive_float(payload.get("price"), "price")
    if not symbol:
        raise ValueError("symbol is required")
    if side not in {"buy", "sell"}:
        raise ValueError("side must be buy or sell")

    position = find_position(portfolio, symbol)
    if position is None:
        if side != "buy":
            raise ValueError(f"{symbol} 不在持仓中，不能卖出")
        position = position_from_payload(payload, config)
        position.shares = 0.0
        position.cost = 0.0
        portfolio.positions.append(position)

    realized = 0.0
    if side == "buy":
        total_cost = position.cost * position.shares + price * shares
        position.shares += shares
        position.cost = total_cost / position.shares if position.shares else 0.0
        portfolio.cash -= price * shares
    else:
        if shares > position.shares:
            raise ValueError(
                f"卖出股数 {shares:g} 超过当前持仓 {position.shares:g}"
            )
        realized = (price - position.cost) * shares
        position.realized_pnl += realized
        position.shares -= shares
        portfolio.cash += price * shares
        if position.shares <= 0:
            position.shares = 0.0
            position.cost = 0.0

    trade = TradeRecord(
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        symbol=position.symbol,
        name=position.name,
        side=side,
        shares=shares,
        price=price,
        realized_pnl=realized,
        cost_after=position.cost,
        shares_after=position.shares,
    )
    portfolio.trades.append(trade)
    save_portfolio_file(path, portfolio)
    return portfolio, trade


def position_from_payload(payload: Dict[str, object], config: AgentConfig) -> Position:
    symbol = str(payload.get("symbol", "")).strip()
    if not symbol:
        raise ValueError("symbol is required")
    known = known_instruments(config)
    instrument = known.get(symbol)
    name = str(payload.get("name", "")).strip() or (
        instrument.name if instrument else symbol
    )
    kind = str(payload.get("kind", "")).strip() or (
        instrument.kind if instrument else "stock"
    )
    market = str(payload.get("market", "")).strip() or (
        instrument.market if instrument else "CN"
    )
    provider_symbol = str(payload.get("provider_symbol", "")).strip() or (
        instrument.provider_symbol if instrument else None
    )
    return Position(
        symbol=symbol,
        name=name,
        cost=float(payload.get("cost", 0) or 0),
        shares=float(payload.get("shares", 0) or 0),
        kind=kind,
        market=market,
        provider_symbol=provider_symbol,
        target_weight=float(payload.get("target_weight", 0) or 0),
        max_loss_pct=float(payload.get("max_loss_pct", 0.08) or 0.08),
        realized_pnl=float(payload.get("realized_pnl", 0) or 0),
    )


def known_instruments(config: AgentConfig) -> Dict[str, Instrument]:
    return {item.symbol: item for item in config.watchlist + config.indices}


def find_position(portfolio: Portfolio, symbol: str) -> Optional[Position]:
    for position in portfolio.positions:
        if position.symbol == symbol:
            return position
    return None


def positive_float(value: object, name: str) -> float:
    try:
        number = float(value or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    if number <= 0:
        raise ValueError(f"{name} must be greater than 0")
    return number
