import tempfile
from pathlib import Path
from unittest import TestCase

from stock_agent.config import AgentConfig
from stock_agent.portfolio_manager import (
    add_position,
    load_portfolio_file,
    record_trade,
)


class PortfolioManagerTests(TestCase):
    def test_buy_trade_updates_weighted_cost(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "portfolio.json")
            config = AgentConfig()
            add_position(
                path,
                {
                    "symbol": "300476",
                    "name": "胜宏科技",
                    "kind": "a_stock",
                    "shares": 100,
                    "cost": 10,
                },
                config,
            )

            portfolio, trade = record_trade(
                path,
                {
                    "symbol": "300476",
                    "side": "buy",
                    "shares": 100,
                    "price": 20,
                },
                config,
            )

            position = portfolio.positions[0]
            self.assertEqual(position.shares, 200)
            self.assertEqual(position.cost, 15)
            self.assertEqual(trade.cost_after, 15)

    def test_sell_trade_records_realized_pnl(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "portfolio.json")
            config = AgentConfig()
            add_position(
                path,
                {
                    "symbol": "300476",
                    "name": "胜宏科技",
                    "shares": 100,
                    "cost": 10,
                },
                config,
            )

            portfolio, trade = record_trade(
                path,
                {
                    "symbol": "300476",
                    "side": "sell",
                    "shares": 40,
                    "price": 12,
                },
                config,
            )

            position = portfolio.positions[0]
            self.assertEqual(position.shares, 60)
            self.assertEqual(position.cost, 10)
            self.assertEqual(position.realized_pnl, 80)
            self.assertEqual(trade.realized_pnl, 80)

            loaded = load_portfolio_file(path)
            self.assertEqual(len(loaded.trades), 1)
