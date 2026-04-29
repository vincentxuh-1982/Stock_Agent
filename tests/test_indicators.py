from datetime import date, timedelta
from unittest import TestCase

from stock_agent.indicators import latest_metrics
from stock_agent.models import Bar


class IndicatorTests(TestCase):
    def test_latest_metrics_has_core_fields(self):
        bars = []
        start = date(2024, 1, 1)
        for index in range(80):
            close = 10 + index * 0.1
            bars.append(
                Bar(
                    date=start + timedelta(days=index),
                    open=close - 0.05,
                    high=close + 0.2,
                    low=close - 0.2,
                    close=close,
                    volume=1000 + index,
                )
            )

        metrics = latest_metrics(bars)

        self.assertGreater(metrics["close"], metrics["ma20"])
        self.assertGreater(metrics["ma20"], metrics["ma60"])
        self.assertGreater(metrics["rsi14"], 50)
        self.assertGreater(metrics["atr14"], 0)
