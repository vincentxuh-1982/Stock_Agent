from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

from .analyzer import analyze_many
from .config import AgentConfig
from .data_providers import provider_from_config
from .digest import (
    active_portfolio_positions,
    instruments_for_positions,
    select_entry_opportunities,
    select_realtime_entry_opportunities,
    unique_instruments,
)
from .models import AnalysisResult, Instrument, Portfolio, Position, RealtimeResult
from .prediction import forecast_probability_text
from .price_levels import PricePlan, analysis_price_plan, price_or_dash, realtime_price_plan
from .realtime import run_realtime_analysis
from .recommender import entry_advice, position_advice


DEFAULT_INITIAL_CASH = 1_000_000.0


@dataclass
class SimulationPosition:
    symbol: str
    name: str
    shares: float
    cost: float
    market: str = "CN"
    kind: str = "stock"
    provider_symbol: str = ""
    last_price: float = 0.0
    target_price: float = 0.0
    stop_loss: float = 0.0
    opened_at: str = ""
    thesis: str = ""

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "SimulationPosition":
        return cls(
            symbol=str(data.get("symbol", "")),
            name=str(data.get("name", "")),
            shares=float(data.get("shares", 0) or 0),
            cost=float(data.get("cost", 0) or 0),
            market=str(data.get("market", "CN")),
            kind=str(data.get("kind", "stock")),
            provider_symbol=str(data.get("provider_symbol", "")),
            last_price=float(data.get("last_price", 0) or 0),
            target_price=float(data.get("target_price", 0) or 0),
            stop_loss=float(data.get("stop_loss", 0) or 0),
            opened_at=str(data.get("opened_at", "")),
            thesis=str(data.get("thesis", "")),
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "shares": self.shares,
            "cost": self.cost,
            "market": self.market,
            "kind": self.kind,
            "provider_symbol": self.provider_symbol,
            "last_price": self.last_price,
            "target_price": self.target_price,
            "stop_loss": self.stop_loss,
            "opened_at": self.opened_at,
            "thesis": self.thesis,
        }


@dataclass
class SimulationAccount:
    initial_cash: float = DEFAULT_INITIAL_CASH
    cash: float = DEFAULT_INITIAL_CASH
    positions: List[SimulationPosition] = field(default_factory=list)
    trades: List[Dict[str, object]] = field(default_factory=list)
    reviews: List[Dict[str, object]] = field(default_factory=list)
    optimizations: List[Dict[str, object]] = field(default_factory=list)
    strategy: Dict[str, object] = field(default_factory=dict)
    last_run_at: str = ""

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "SimulationAccount":
        strategy = dict(data.get("strategy", {}) or {})
        merged_strategy = default_strategy()
        merged_strategy.update(strategy)
        return cls(
            initial_cash=float(data.get("initial_cash", DEFAULT_INITIAL_CASH) or DEFAULT_INITIAL_CASH),
            cash=float(data.get("cash", DEFAULT_INITIAL_CASH) or DEFAULT_INITIAL_CASH),
            positions=[
                SimulationPosition.from_dict(item)
                for item in data.get("positions", [])
            ],
            trades=[dict(item) for item in data.get("trades", [])],
            reviews=[dict(item) for item in data.get("reviews", [])],
            optimizations=[dict(item) for item in data.get("optimizations", [])],
            strategy=merged_strategy,
            last_run_at=str(data.get("last_run_at", "")),
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "initial_cash": self.initial_cash,
            "cash": self.cash,
            "positions": [position.to_dict() for position in self.positions],
            "trades": self.trades,
            "reviews": self.reviews,
            "optimizations": self.optimizations,
            "strategy": self.strategy,
            "last_run_at": self.last_run_at,
        }


def default_strategy() -> Dict[str, object]:
    return {
        "enabled": True,
        "min_up_probability": 0.58,
        "position_size_pct": 0.12,
        "max_positions": 6,
        "sell_down_probability": 0.58,
        "loss_tighten_step": 0.01,
        "review_window": 20,
    }


def simulation_path_for(
    config: AgentConfig,
    portfolio_path: Optional[str] = None,
    explicit_path: Optional[str] = None,
) -> Path:
    if explicit_path:
        return Path(explicit_path)
    if portfolio_path:
        return Path(portfolio_path).with_name("simulation.local.json")
    return Path(config.output_dir).resolve().parent / "data" / "simulation.local.json"


def load_simulation_account(path: Path) -> SimulationAccount:
    if not path.exists():
        return SimulationAccount(strategy=default_strategy())
    return SimulationAccount.from_dict(json.loads(path.read_text(encoding="utf-8")))


def save_simulation_account(path: Path, account: SimulationAccount) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(account.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def reset_simulation_account(path: Path, initial_cash: float = DEFAULT_INITIAL_CASH) -> SimulationAccount:
    if initial_cash <= 0:
        raise ValueError("initial_cash must be greater than 0")
    account = SimulationAccount(
        initial_cash=float(initial_cash),
        cash=float(initial_cash),
        strategy=default_strategy(),
        last_run_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )
    save_simulation_account(path, account)
    return account


def set_simulation_enabled(path: Path, enabled: bool) -> SimulationAccount:
    account = load_simulation_account(path)
    account.strategy["enabled"] = bool(enabled)
    account.last_run_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_simulation_account(path, account)
    return account


def run_simulation_cycle(
    config: AgentConfig,
    portfolio: Optional[Portfolio] = None,
    account_path: Optional[Path] = None,
) -> Dict[str, object]:
    path = account_path or simulation_path_for(config)
    account = load_simulation_account(path)
    if not bool(account.strategy.get("enabled", True)):
        view = simulation_view(config, path=path)
        view["cycle"] = {
            "source": "paused",
            "new_trades": 0,
            "errors": ["模拟交易已暂停"],
        }
        return view
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    trades_before = len(account.trades)
    errors: List[str] = []

    realtime_results, _, realtime_errors = run_realtime_analysis(config)
    errors.extend(realtime_errors)
    if realtime_results:
        run_realtime_simulation(account, realtime_results, timestamp)
        source = "realtime"
    else:
        provider = provider_from_config(config)
        instruments = simulation_instruments(config, portfolio, account)
        analysis_results = analyze_many(instruments, provider, config, errors=errors)
        run_analysis_simulation(account, analysis_results, portfolio, timestamp)
        source = "analysis"

    account.last_run_at = timestamp
    save_simulation_account(path, account)
    view = simulation_view(config, path=path)
    view["cycle"] = {
        "source": source,
        "new_trades": len(account.trades) - trades_before,
        "errors": errors,
    }
    return view


def run_realtime_simulation(
    account: SimulationAccount,
    results: Sequence[RealtimeResult],
    timestamp: str,
) -> None:
    by_symbol = {result.instrument.symbol: result for result in results}
    for position in list(account.positions):
        result = by_symbol.get(position.symbol)
        if result:
            update_position_mark(position, result.quote.price, realtime_price_plan(result))
            maybe_sell_realtime(account, position, result, timestamp)

    held = {position.symbol for position in account.positions if position.shares > 0}
    candidates = [
        result
        for result in select_realtime_entry_opportunities(results)
        if result.instrument.symbol not in held
    ]
    for result in candidates:
        if len(account.positions) >= int(account.strategy["max_positions"]):
            break
        plan = realtime_price_plan(result)
        forecast = result.intraday_forecast
        up_probability = forecast.up_probability if forecast else 0.5
        if up_probability < float(account.strategy["min_up_probability"]):
            continue
        buy_position(account, result.instrument, result.quote.price, plan, timestamp, result.action)


def run_analysis_simulation(
    account: SimulationAccount,
    results: Sequence[AnalysisResult],
    portfolio: Optional[Portfolio],
    timestamp: str,
) -> None:
    by_symbol = {result.instrument.symbol: result for result in results}
    real_positions = {
        position.symbol: position
        for position in active_portfolio_positions(portfolio)
    } if portfolio else {}
    for position in list(account.positions):
        result = by_symbol.get(position.symbol)
        if result:
            update_position_mark(position, result.close, analysis_price_plan(result))
            maybe_sell_analysis(account, position, result, timestamp)

    held = {position.symbol for position in account.positions if position.shares > 0}
    candidates = [
        result
        for result in select_entry_opportunities(results)
        if result.instrument.symbol not in held
    ]
    for result in candidates:
        if len(account.positions) >= int(account.strategy["max_positions"]):
            break
        plan = analysis_price_plan(result)
        forecast = result.forecast_3d
        up_probability = forecast.up_probability if forecast else 0.5
        if up_probability < float(account.strategy["min_up_probability"]):
            continue
        real_position = real_positions.get(result.instrument.symbol)
        thesis = (
            position_advice(result, real_position)
            if real_position else entry_advice(result)
        )
        buy_position(account, result.instrument, result.close, plan, timestamp, thesis)


def maybe_sell_realtime(
    account: SimulationAccount,
    position: SimulationPosition,
    result: RealtimeResult,
    timestamp: str,
) -> None:
    forecast = result.intraday_forecast
    down_probability = forecast.down_probability if forecast else 0.5
    plan = realtime_price_plan(result)
    price = result.quote.price
    reason = ""
    sell_ratio = 0.0
    if price <= max(position.stop_loss, plan.stop_loss):
        reason = "触发止损/减仓价"
        sell_ratio = 1.0
    elif price >= max(position.target_price, plan.target_price):
        reason = "达到目标价，先兑现收益"
        sell_ratio = 0.5
    elif down_probability >= float(account.strategy["sell_down_probability"]) and result.status in {
        "跌破支撑",
        "放量回落",
        "盘中走弱",
    }:
        reason = "下行概率升高且盘中走弱"
        sell_ratio = 0.5
    if sell_ratio:
        sell_position(account, position, price, sell_ratio, timestamp, reason)


def maybe_sell_analysis(
    account: SimulationAccount,
    position: SimulationPosition,
    result: AnalysisResult,
    timestamp: str,
) -> None:
    forecast = result.forecast_3d
    down_probability = forecast.down_probability if forecast else 0.5
    plan = analysis_price_plan(result)
    price = result.close
    reason = ""
    sell_ratio = 0.0
    if price <= max(position.stop_loss, plan.stop_loss):
        reason = "收盘跌破止损/减仓价"
        sell_ratio = 1.0
    elif price >= max(position.target_price, plan.target_price):
        reason = "收盘达到目标价"
        sell_ratio = 0.5
    elif result.score_short <= -2 or down_probability >= float(account.strategy["sell_down_probability"]):
        reason = "短线结构转弱，降低仓位"
        sell_ratio = 0.5
    if sell_ratio:
        sell_position(account, position, price, sell_ratio, timestamp, reason)


def buy_position(
    account: SimulationAccount,
    instrument: Instrument,
    price: float,
    plan: PricePlan,
    timestamp: str,
    thesis: str,
) -> None:
    allocation = account_equity(account) * float(account.strategy["position_size_pct"])
    spend = min(account.cash, allocation)
    shares = lot_shares(spend, price, instrument.market)
    if shares <= 0:
        return
    amount = shares * price
    account.cash -= amount
    position = SimulationPosition(
        symbol=instrument.symbol,
        name=instrument.name,
        shares=shares,
        cost=price,
        market=instrument.market,
        kind=instrument.kind,
        provider_symbol=instrument.provider_symbol or "",
        last_price=price,
        target_price=plan.target_price,
        stop_loss=plan.stop_loss,
        opened_at=timestamp,
        thesis=thesis,
    )
    account.positions.append(position)
    trade = append_trade(account, position, "buy", shares, price, 0.0, timestamp, thesis)
    append_review(account, trade, "买入后复盘", thesis, plan, realized_pnl=0.0)
    append_optimization(account, trade, 0.0, "买入交易不立即调参，等待卖出或止损结果验证。")


def sell_position(
    account: SimulationAccount,
    position: SimulationPosition,
    price: float,
    ratio: float,
    timestamp: str,
    reason: str,
) -> None:
    shares = min(position.shares, max(0.0, position.shares * ratio))
    shares = normalize_sell_shares(shares, position.shares)
    if shares <= 0:
        return
    realized = (price - position.cost) * shares
    account.cash += shares * price
    position.shares -= shares
    position.last_price = price
    trade = append_trade(account, position, "sell", shares, price, realized, timestamp, reason)
    append_review(account, trade, "卖出后复盘", reason, position_plan(position), realized)
    optimize_strategy_after_sell(account, trade, realized)
    if position.shares <= 0:
        account.positions = [item for item in account.positions if item.symbol != position.symbol]


def append_trade(
    account: SimulationAccount,
    position: SimulationPosition,
    side: str,
    shares: float,
    price: float,
    realized_pnl: float,
    timestamp: str,
    reason: str,
) -> Dict[str, object]:
    trade = {
        "id": len(account.trades) + 1,
        "timestamp": timestamp,
        "symbol": position.symbol,
        "name": position.name,
        "side": side,
        "shares": shares,
        "price": price,
        "amount": shares * price,
        "realized_pnl": realized_pnl,
        "cash_after": account.cash,
        "equity_after": account_equity(account),
        "reason": reason,
    }
    account.trades.append(trade)
    return trade


def append_review(
    account: SimulationAccount,
    trade: Dict[str, object],
    title: str,
    reason: str,
    plan: PricePlan,
    realized_pnl: float,
) -> None:
    account.reviews.append(
        {
            "trade_id": trade["id"],
            "timestamp": trade["timestamp"],
            "symbol": trade["symbol"],
            "name": trade["name"],
            "side": trade["side"],
            "title": title,
            "reason": reason,
            "realized_pnl": realized_pnl,
            "entry_zone": plan.entry_zone,
            "stop_loss": plan.stop_loss,
            "target_price": plan.target_price,
            "strategy_snapshot": deepcopy(account.strategy),
        }
    )


def append_optimization(
    account: SimulationAccount,
    trade: Dict[str, object],
    realized_pnl: float,
    note: str,
) -> None:
    account.optimizations.append(
        {
            "trade_id": trade["id"],
            "timestamp": trade["timestamp"],
            "symbol": trade["symbol"],
            "realized_pnl": realized_pnl,
            "strategy": deepcopy(account.strategy),
            "change": note,
        }
    )


def optimize_strategy_after_sell(
    account: SimulationAccount,
    trade: Dict[str, object],
    realized_pnl: float,
) -> None:
    before = deepcopy(account.strategy)
    if realized_pnl < 0:
        step = float(account.strategy["loss_tighten_step"])
        account.strategy["min_up_probability"] = min(
            0.68,
            round(float(account.strategy["min_up_probability"]) + step, 3),
        )
        account.strategy["position_size_pct"] = max(
            0.05,
            round(float(account.strategy["position_size_pct"]) - step, 3),
        )
        note = (
            f"亏损交易，入场门槛提高到 {account.strategy['min_up_probability']:.2f}，"
            f"单笔仓位降到 {account.strategy['position_size_pct']:.0%}。"
        )
    else:
        win_rate = recent_win_rate(account.trades, int(account.strategy["review_window"]))
        if win_rate >= 0.6:
            account.strategy["position_size_pct"] = min(
                0.16,
                round(float(account.strategy["position_size_pct"]) + 0.005, 3),
            )
            note = f"近期胜率 {win_rate:.0%}，单笔仓位小幅提升到 {account.strategy['position_size_pct']:.1%}。"
        else:
            note = f"盈利交易但近期胜率 {win_rate:.0%}，暂不调整核心参数。"
    append_optimization(account, trade, realized_pnl, f"{note} 原参数：{before}")


def update_position_mark(position: SimulationPosition, price: float, plan: PricePlan) -> None:
    position.last_price = price
    position.target_price = plan.target_price
    position.stop_loss = plan.stop_loss


def position_plan(position: SimulationPosition) -> PricePlan:
    return PricePlan(
        entry_low=position.cost,
        entry_high=position.cost,
        add_price=0.0,
        reduce_price=position.stop_loss,
        stop_loss=position.stop_loss,
        target_price=position.target_price,
        note=position.thesis,
    )


def simulation_view(
    config: AgentConfig,
    portfolio_path: Optional[str] = None,
    path: Optional[Path] = None,
    start: str = "",
    end: str = "",
) -> Dict[str, object]:
    account_path = path or simulation_path_for(config, portfolio_path)
    account = load_simulation_account(account_path)
    trades = filter_by_date(account.trades, start, end)
    reviews = filter_by_date(account.reviews, start, end)
    optimizations = filter_by_date(account.optimizations, start, end)
    positions = [position.to_dict() for position in account.positions]
    for item in positions:
        item["market_value"] = float(item["shares"]) * float(item["last_price"] or item["cost"])
        item["unrealized_pnl"] = (float(item["last_price"] or item["cost"]) - float(item["cost"])) * float(item["shares"])
        item["target_price_text"] = price_or_dash(float(item.get("target_price", 0) or 0))
        item["stop_loss_text"] = price_or_dash(float(item.get("stop_loss", 0) or 0))
    summary = simulation_summary(account)
    filtered_summary = filtered_trade_summary(trades)
    return {
        "path": str(account_path),
        "summary": summary,
        "filtered_summary": filtered_summary,
        "positions": positions,
        "trades": trades,
        "reviews": reviews,
        "optimizations": optimizations,
        "strategy": account.strategy,
        "last_run_at": account.last_run_at,
    }


def simulation_summary(account: SimulationAccount) -> Dict[str, object]:
    equity = account_equity(account)
    realized = sum(float(trade.get("realized_pnl", 0) or 0) for trade in account.trades)
    unrealized = sum(
        (position.last_price - position.cost) * position.shares
        for position in account.positions
    )
    sells = [trade for trade in account.trades if trade.get("side") == "sell"]
    wins = [trade for trade in sells if float(trade.get("realized_pnl", 0) or 0) > 0]
    return {
        "initial_cash": account.initial_cash,
        "cash": account.cash,
        "equity": equity,
        "total_return": (equity - account.initial_cash) / account.initial_cash if account.initial_cash else 0,
        "realized_pnl": realized,
        "unrealized_pnl": unrealized,
        "position_count": len(account.positions),
        "trade_count": len(account.trades),
        "win_rate": len(wins) / len(sells) if sells else 0,
    }


def filtered_trade_summary(trades: Sequence[Dict[str, object]]) -> Dict[str, object]:
    sells = [trade for trade in trades if trade.get("side") == "sell"]
    realized = sum(float(trade.get("realized_pnl", 0) or 0) for trade in sells)
    wins = [trade for trade in sells if float(trade.get("realized_pnl", 0) or 0) > 0]
    return {
        "trade_count": len(trades),
        "realized_pnl": realized,
        "win_rate": len(wins) / len(sells) if sells else 0,
    }


def simulation_instruments(
    config: AgentConfig,
    portfolio: Optional[Portfolio],
    account: SimulationAccount,
) -> List[Instrument]:
    known = {item.symbol: item for item in config.watchlist + config.indices}
    instruments = list(config.watchlist)
    if portfolio:
        instruments.extend(instruments_for_positions(config, active_portfolio_positions(portfolio)))
    for position in account.positions:
        if position.symbol in known:
            instruments.append(known[position.symbol])
        else:
            instruments.append(
                Instrument(
                    symbol=position.symbol,
                    name=position.name,
                    kind=position.kind,
                    market=position.market,
                    provider_symbol=position.provider_symbol or None,
                )
            )
    return unique_instruments(instruments)


def account_equity(account: SimulationAccount) -> float:
    return account.cash + sum(
        position.shares * (position.last_price or position.cost)
        for position in account.positions
    )


def lot_shares(amount: float, price: float, market: str) -> float:
    if price <= 0:
        return 0.0
    lot = 100
    shares = int(amount / price / lot) * lot
    return float(max(0, shares))


def normalize_sell_shares(shares: float, total_shares: float) -> float:
    if shares >= total_shares:
        return total_shares
    lot = 100
    rounded = int(shares / lot) * lot
    return float(max(lot if total_shares >= lot and rounded <= 0 else rounded, 0))


def recent_win_rate(trades: Sequence[Dict[str, object]], window: int) -> float:
    sells = [trade for trade in trades if trade.get("side") == "sell"][-window:]
    if not sells:
        return 0.0
    wins = [trade for trade in sells if float(trade.get("realized_pnl", 0) or 0) > 0]
    return len(wins) / len(sells)


def filter_by_date(items: Sequence[Dict[str, object]], start: str, end: str) -> List[Dict[str, object]]:
    output = []
    for item in items:
        day = str(item.get("timestamp", ""))[:10]
        if start and day < start:
            continue
        if end and day > end:
            continue
        output.append(dict(item))
    return output
