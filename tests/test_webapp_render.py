from unittest import TestCase

from stock_agent.webapp import markdown_to_html


class WebAppRenderTests(TestCase):
    def test_table_renders_probability_visual(self):
        html = markdown_to_html(
            "| 代码 | 名称 | 当日概率 |\n"
            "| --- | --- | --- |\n"
            "| 603228 | 景旺电子 | 涨 46% / 跌 54%（震荡均衡） |\n"
        )

        self.assertIn("prob-cell", html)
        self.assertIn("width:46%", html)
        self.assertIn("width:54%", html)

    def test_long_table_text_is_collapsible(self):
        html = markdown_to_html(
            "| 代码 | 名称 | 条件说明 |\n"
            "| --- | --- | --- |\n"
            "| 688802 | 沐曦股份-U | 维持在开盘价上方并突破压力位，上行概率上修；跌回支撑位下方则转为回落优先 |\n"
        )

        self.assertIn("cell-detail", html)
        self.assertIn("<summary>", html)

    def test_first_columns_are_marked_as_sticky_keys(self):
        html = markdown_to_html(
            "| 代码 | 名称 | 现价 |\n"
            "| --- | --- | ---: |\n"
            "| 00175 | 吉利汽车 | 22.16 |\n"
        )

        self.assertIn("is-sticky-key", html)
