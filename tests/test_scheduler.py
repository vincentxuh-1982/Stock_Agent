from datetime import datetime
from unittest import TestCase

from stock_agent.config import AgentConfig
from stock_agent.scheduler import TIMED_PUSH_JOBS, scheduled_time_due, scheduled_time_reached


class SchedulerTests(TestCase):
    def test_timed_push_jobs_match_requested_schedule(self):
        self.assertEqual(
            [(job.name, job.default_time, job.title) for job in TIMED_PUSH_JOBS],
            [
                ("opening_brief", "09:15", "开盘早知道"),
                ("morning_flash", "10:30", "早盘快讯"),
                ("midday_flash", "11:30", "午间快讯"),
                ("golden_1430", "14:30", "黄金两点半"),
                ("daily_summary", "15:00", "今日总结"),
            ],
        )

    def test_scheduled_time_uses_default_when_config_missing(self):
        config = AgentConfig.from_dict({"schedules": {}})

        self.assertFalse(
            scheduled_time_reached(
                "opening_brief",
                config,
                datetime(2026, 5, 12, 9, 14),
                default_time="09:15",
            )
        )
        self.assertTrue(
            scheduled_time_reached(
                "opening_brief",
                config,
                datetime(2026, 5, 12, 9, 15),
                default_time="09:15",
            )
        )

    def test_timed_push_does_not_backfill_hours_late(self):
        config = AgentConfig.from_dict({"schedules": {}})

        self.assertTrue(
            scheduled_time_due(
                "opening_brief",
                config,
                datetime(2026, 5, 12, 9, 20),
                default_time="09:15",
            )
        )
        self.assertFalse(
            scheduled_time_due(
                "opening_brief",
                config,
                datetime(2026, 5, 12, 10, 0),
                default_time="09:15",
            )
        )
