import os
from unittest import TestCase
from unittest.mock import patch

from stock_agent.config import AgentConfig, PushConfig
from stock_agent.notifier import (
    format_mobile_markdown,
    send_markdown,
    split_markdown,
    trim_markdown,
)


class NotifierTests(TestCase):
    def test_push_config_reads_environment(self):
        with patch.dict(
            os.environ,
            {
                "STOCK_AGENT_PUSH_PROVIDER": "wecom",
                "STOCK_AGENT_WECHAT_WEBHOOK_URL": "https://example.test/webhook",
            },
            clear=False,
        ):
            config = AgentConfig.from_dict({})

        self.assertTrue(config.push.enabled)
        self.assertEqual(config.push.provider, "wecom")
        self.assertEqual(config.push.wechat_webhook_url, "https://example.test/webhook")

    def test_disabled_notifier_is_noop(self):
        result = send_markdown(PushConfig(enabled=False), "title", "body")

        self.assertFalse(result.sent)
        self.assertEqual(result.message, "push disabled")

    def test_trim_markdown_keeps_short_content(self):
        self.assertEqual(trim_markdown("abc", 800), "abc")

    def test_trim_markdown_truncates_long_content(self):
        content = "a" * 1000
        trimmed = trim_markdown(content, 850)

        self.assertLess(len(trimmed), len(content))
        self.assertIn("内容过长已截断", trimmed)

    def test_format_mobile_markdown_removes_local_report_path(self):
        formatted = format_mobile_markdown(
            "# 标题\n\n正文\n\n完整报告：`/Users/demo/Library/Application Support/StockAgent/reports/a.md`\n"
        )

        self.assertIn("正文", formatted)
        self.assertNotIn("完整报告", formatted)
        self.assertNotIn("/Users/demo", formatted)

    def test_format_mobile_markdown_turns_tables_into_readable_items(self):
        formatted = format_mobile_markdown(
            "| 代码 | 名称 | 现价 | 策略 |\n"
            "| --- | --- | ---: | --- |\n"
            "| 603228 | 景旺电子 | 70.61 | 持有观察 |\n"
        )

        self.assertIn("**603228 景旺电子**", formatted)
        self.assertIn("现价 70.61；策略 持有观察", formatted)
        self.assertNotIn("| 603228", formatted)

    def test_split_markdown_preserves_full_content(self):
        content = "第一段\n" + "第二段\n" * 400
        chunks = split_markdown(content, 900)

        self.assertGreater(len(chunks), 1)
        self.assertEqual("".join(chunks).replace("\n", ""), content.replace("\n", ""))
