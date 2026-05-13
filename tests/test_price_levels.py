from datetime import date
from unittest import TestCase

from stock_agent.models import AnalysisResult, Instrument, Position
from stock_agent.price_levels import analysis_price_plan


class PriceLevelsTests(TestCase):
    def test_analysis_plan_has_entry_stop_and_target(self):
        result = AnalysisResult(
            instrument=Instrument(symbol="300476", name="胜宏科技"),
            as_of=date(2026, 5, 13),
            close=100,
            change_pct=0.02,
            short_view="偏强",
            mid_view="偏强",
            risk_level="中",
            score_short=3,
            score_mid=2,
            metrics={"atr14": 4, "position60": 0.7, "rsi14": 62},
            signals=[],
            supports=[92, 96],
            resistances=[108, 116],
        )

        plan = analysis_price_plan(result)

        self.assertEqual(plan.entry_zone, "96.80-98.40")
        self.assertGreaterEqual(plan.target_price, 108)
        self.assertLess(plan.stop_loss, 96)

    def test_position_plan_respects_hard_stop(self):
        result = AnalysisResult(
            instrument=Instrument(symbol="603228", name="景旺电子"),
            as_of=date(2026, 5, 13),
            close=80,
            change_pct=-0.01,
            short_view="震荡",
            mid_view="偏强",
            risk_level="中",
            score_short=0,
            score_mid=1,
            metrics={"atr14": 3, "position60": 0.5, "rsi14": 50},
            signals=[],
            supports=[70],
            resistances=[88],
        )

        plan = analysis_price_plan(
            result,
            Position(symbol="603228", name="景旺电子", cost=100, shares=100, max_loss_pct=0.08),
        )

        self.assertEqual(plan.stop_loss, 92)
