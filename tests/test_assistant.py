import os
from unittest import TestCase
from unittest.mock import patch

from stock_agent.assistant import answer_question
from stock_agent.config import AgentConfig
from stock_agent.models import Instrument


class AssistantTests(TestCase):
    def test_local_answer_uses_current_report_lines(self):
        config = AgentConfig(
            watchlist=[
                Instrument(symbol="300476", name="胜宏科技", kind="a_stock")
            ]
        )
        report = """
# 实时行情分析

| 代码 | 名称 | 状态 | 当日概率 | 条件说明 |
| --- | --- | --- | --- | --- |
| 300476 | 胜宏科技 | 放量上涨 | 涨 61% / 跌 39% | 站稳 310 则上修 |
"""
        with patch.dict(os.environ, {"STOCK_AGENT_AI_PROVIDER": "local"}, clear=False):
            response = answer_question(
                question="胜宏科技今天怎么看？",
                kind="realtime",
                report_markdown=report,
                config=config,
            )

        self.assertEqual(response["provider"], "local")
        self.assertFalse(response["external"])
        self.assertIn("胜宏科技", response["answer"])
        self.assertIn("涨 61%", response["answer"])
