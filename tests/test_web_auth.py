import base64
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from stock_agent.webapp import WebApp


class WebAuthTests(TestCase):
    def test_auth_is_optional_until_enabled_with_password(self):
        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "local.json"
            config_path.write_text(json.dumps({"web": {"auth_enabled": True}}))
            app = WebApp(str(config_path), None)

            self.assertTrue(app.check_auth_header(None))

    def test_basic_auth_requires_matching_credentials(self):
        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "local.json"
            config_path.write_text(
                json.dumps(
                    {
                        "web": {
                            "auth_enabled": True,
                            "username": "stock",
                            "password": "secret",
                        }
                    }
                )
            )
            app = WebApp(str(config_path), None)

            valid = base64.b64encode(b"stock:secret").decode("ascii")
            invalid = base64.b64encode(b"stock:wrong").decode("ascii")

            self.assertTrue(app.check_auth_header(f"Basic {valid}"))
            self.assertFalse(app.check_auth_header(f"Basic {invalid}"))
            self.assertFalse(app.check_auth_header(None))
