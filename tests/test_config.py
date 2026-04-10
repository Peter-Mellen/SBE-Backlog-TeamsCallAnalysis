from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sbe_teams_call_analysis.config import parse_dotenv


class ParseDotenvTests(unittest.TestCase):
    def test_parse_dotenv_supports_quotes_comments_and_export(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "# comment",
                        "TENANT_ID=tenant-id",
                        "export CLIENT_SECRET='super-secret'",
                        'CLIENT_ID="client-id"',
                        "PLAIN=value # trailing comment",
                    ]
                ),
                encoding="utf-8",
            )

            values = parse_dotenv(env_path)

            self.assertEqual(values["TENANT_ID"], "tenant-id")
            self.assertEqual(values["CLIENT_SECRET"], "super-secret")
            self.assertEqual(values["CLIENT_ID"], "client-id")
            self.assertEqual(values["PLAIN"], "value")


if __name__ == "__main__":
    unittest.main()
