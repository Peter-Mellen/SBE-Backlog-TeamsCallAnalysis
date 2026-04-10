from __future__ import annotations

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sbe_teams_call_analysis.vtt import parse_webvtt


class ParseWebVttTests(unittest.TestCase):
    def test_parse_webvtt_extracts_speaker_and_text(self) -> None:
        content = """WEBVTT

00:00:01.000 --> 00:00:03.000
<v Alice Example>Hello there</v>

00:00:03.500 --> 00:00:05.000
<v Bob Example>Hi Alice</v>
"""

        utterances = parse_webvtt(content)

        self.assertEqual(len(utterances), 2)
        self.assertEqual(utterances[0]["speaker"], "Alice Example")
        self.assertEqual(utterances[0]["text"], "Hello there")
        self.assertEqual(utterances[1]["speaker"], "Bob Example")
        self.assertEqual(utterances[1]["text"], "Hi Alice")


if __name__ == "__main__":
    unittest.main()
