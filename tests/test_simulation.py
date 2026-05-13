import tempfile
from pathlib import Path
from unittest import TestCase

from stock_agent.config import load_config
from stock_agent.simulation import (
    DEFAULT_INITIAL_CASH,
    reset_simulation_account,
    run_simulation_cycle,
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
