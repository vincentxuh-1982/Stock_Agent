from datetime import datetime
from unittest import TestCase

from stock_agent.models import Instrument
from stock_agent.trading_hours import market_session_status


class TradingHoursTests(TestCase):
    def test_cn_instrument_is_active_during_morning_session(self):
        status = market_session_status(
            Instrument(symbol="300476", name="胜宏科技", kind="a_stock"),
            now=datetime(2026, 4, 28, 10, 0),
            use_remote_calendar=False,
        )

        self.assertTrue(status.is_trading)
        self.assertEqual(status.market, "CN")
        self.assertEqual(status.phase, "上午交易")

    def test_cn_instrument_stays_active_during_lunch_break(self):
        status = market_session_status(
            Instrument(symbol="300476", name="胜宏科技", kind="a_stock"),
            now=datetime(2026, 4, 28, 12, 10),
            use_remote_calendar=False,
        )

        self.assertTrue(status.is_trading)
        self.assertEqual(status.phase, "盘中休市")
        self.assertEqual(status.next_open, "")

    def test_hk_instrument_trades_after_a_share_close(self):
        status = market_session_status(
            Instrument(
                symbol="00175",
                name="吉利汽车",
                kind="hk_stock",
                market="HK",
            ),
            now=datetime(2026, 4, 28, 15, 30),
            use_remote_calendar=False,
        )

        self.assertTrue(status.is_trading)
        self.assertEqual(status.market, "HK")
        self.assertEqual(status.phase, "下午交易")

    def test_cn_instrument_is_inactive_after_close(self):
        status = market_session_status(
            Instrument(symbol="300476", name="胜宏科技", kind="a_stock"),
            now=datetime(2026, 4, 28, 15, 30),
            use_remote_calendar=False,
        )

        self.assertFalse(status.is_trading)
        self.assertEqual(status.phase, "盘后")
        self.assertTrue(status.next_open.endswith("09:30"))

    def test_cn_call_auction_is_before_realtime_span(self):
        status = market_session_status(
            Instrument(symbol="300476", name="胜宏科技", kind="a_stock"),
            now=datetime(2026, 4, 28, 9, 20),
            use_remote_calendar=False,
        )

        self.assertFalse(status.is_trading)
        self.assertEqual(status.phase, "盘前")
        self.assertTrue(status.next_open.endswith("09:30"))
