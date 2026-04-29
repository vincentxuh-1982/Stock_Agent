from datetime import date, timedelta
from unittest import TestCase

from stock_agent.analyzer import analyze_instrument
from stock_agent.models import Bar, Instrument


class AnalyzerTests(TestCase):
    def test_analyze_instrument_trending_up(self):
        bars = []
        start = date(2024, 1, 1)
        for index in range(90):
            close = 20 + index * 0.2
            bars.append(
                Bar(
                    date=start + timedelta(days=index),
                    open=close - 0.1,
                    high=close + 0.3,
                    low=close - 0.3,
                    close=close,
                    volume=10000,
                )
            )

        result = analyze_instrument(Instrument(symbol="T", name="Test"), bars)

        self.assertGreater(result.score_mid, 0)
        self.assertIn(result.mid_view, {"震荡偏强", "偏强"})
        self.assertIsNotNone(result.forecast_3d)
        self.assertGreater(result.forecast_3d.up_probability, 0.5)
