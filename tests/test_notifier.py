import os
from unittest import TestCase
from unittest.mock import patch

from stock_agent.config import AgentConfig, PushConfig
from stock_agent.notifier import send_markdown, trim_markdown


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
