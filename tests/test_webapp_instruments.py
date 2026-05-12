import json
import tempfile
from pathlib import Path
from unittest import TestCase

from stock_agent.webapp import WebApp


class WebAppInstrumentTests(TestCase):
    def test_delete_watchlist_instrument_removes_theme_mapping(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "output_dir": str(root / "reports"),
                        "watchlist": [
                            {
                                "symbol": "300476",
                                "name": "胜宏科技",
                                "kind": "a_stock",
                                "themes": ["PCB"],
                            },
                            {"symbol": "603228", "name": "景旺电子", "kind": "a_stock"},
                        ],
                        "indices": [],
                        "theme_stock_map": {"PCB": ["300476", "603228"]},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            app = WebApp(str(config_path), None)

            result = app.delete_instrument("300476")
            updated = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertTrue(result["deleted"])
        self.assertEqual([item["symbol"] for item in updated["watchlist"]], ["603228"])
        self.assertEqual(updated["theme_stock_map"]["PCB"], ["603228"])
