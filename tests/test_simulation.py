import tempfile
from pathlib import Path
from unittest import TestCase

from stock_agent.config import load_config
from stock_agent.config import AgentConfig
from stock_agent.models import Instrument
from stock_agent.simulation import (
    DEFAULT_INITIAL_CASH,
    SimulationAccount,
    default_strategy,
    reset_simulation_account,
    run_simulation_cycle,
    simulation_instruments,
    simulation_view,
)


class SimulationTests(TestCase):
    def test_reset_simulation_account_uses_default_cash(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "simulation.json"

            account = reset_simulation_account(path, DEFAULT_INITIAL_CASH)

            self.assertEqual(account.cash, DEFAULT_INITIAL_CASH)
            self.assertEqual(account.initial_cash, DEFAULT_INITIAL_CASH)
            self.assertTrue(path.exists())

    def test_run_simulation_cycle_records_account_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = load_config("config/demo.json")
            config.output_dir = str(Path(tmp) / "reports")
            path = Path(tmp) / "simulation.json"

            view = run_simulation_cycle(config, account_path=path)

            self.assertTrue(path.exists())
            self.assertIn("summary", view)
            self.assertIn("strategy", view)
            self.assertIn("cycle", view)

    def test_simulation_view_filters_trades(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "simulation.json"
            account = reset_simulation_account(path, DEFAULT_INITIAL_CASH)
            account.trades.append(
                {
                    "id": 1,
                    "timestamp": "2026-05-01 10:00:00",
                    "symbol": "A",
                    "side": "sell",
                    "realized_pnl": 100,
                }
            )
            account.trades.append(
                {
                    "id": 2,
                    "timestamp": "2026-05-10 10:00:00",
                    "symbol": "B",
                    "side": "sell",
                    "realized_pnl": -50,
                }
            )
            from stock_agent.simulation import save_simulation_account

            save_simulation_account(path, account)
            config = load_config("config/demo.json")

            view = simulation_view(config, path=path, start="2026-05-05", end="2026-05-12")

            self.assertEqual(len(view["trades"]), 1)
            self.assertEqual(view["filtered_summary"]["realized_pnl"], -50)

    def test_simulation_instruments_include_etf_pools_and_report_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            report_dir = Path(tmp) / "reports"
            report_dir.mkdir()
            (report_dir / "news_hotspots_latest.md").write_text(
                "- 688001 华兴科技（A，排名 1，涨跌 3.20%）：新闻热点候选\n",
                encoding="utf-8",
            )
            strategy = default_strategy()
            strategy["use_hot_candidates"] = False
            config = AgentConfig(
                output_dir=str(report_dir),
                watchlist=[],
                etf_pools={
                    "AI池": [
                        Instrument(
                            symbol="159819",
                            name="人工智能ETF",
                            kind="a_etf",
                            themes=["AI"],
                        )
                    ]
                },
            )

            instruments = simulation_instruments(
                config,
                portfolio=None,
                account=SimulationAccount(strategy=strategy),
            )

            symbols = {instrument.symbol for instrument in instruments}
            self.assertIn("159819", symbols)
            self.assertIn("688001", symbols)
