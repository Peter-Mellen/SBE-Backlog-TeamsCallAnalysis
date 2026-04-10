from __future__ import annotations

from html import unescape
import re


TIMECODE_RE = re.compile(
    r"^(?P<start>\d{2}:\d{2}:\d{2}\.\d{3})\s+-->\s+(?P<end>\d{2}:\d{2}:\d{2}\.\d{3})"
)
VOICE_TAG_RE = re.compile(r"^<v(?:\s+([^>]+))?>(.*)$", re.IGNORECASE)


def _strip_markup(text: str) -> str:
    text = re.sub(r"</?v[^>]*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return unescape(text).strip()


def _extract_speaker_and_text(payload: str) -> tuple[str | None, str]:
    match = VOICE_TAG_RE.match(payload)
    if not match:
        return None, _strip_markup(payload)

    speaker = (match.group(1) or "").strip() or None
    text = _strip_markup(match.group(2))
    return speaker, text


def parse_webvtt(content: str) -> list[dict[str, str | None]]:
    lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    utterances: list[dict[str, str | None]] = []
    index = 0

    while index < len(lines):
        line = lines[index].strip().lstrip("\ufeff")
        if not line or line == "WEBVTT" or line.startswith("NOTE"):
            index += 1
            continue

        if "-->" not in line:
            if index + 1 >= len(lines) or "-->" not in lines[index + 1]:
                index += 1
                continue
            index += 1
            line = lines[index].strip()

        timing = TIMECODE_RE.match(line)
        if not timing:
            index += 1
            continue

        start = timing.group("start")
        end = timing.group("end")
        index += 1

        payload_lines: list[str] = []
        while index < len(lines) and lines[index].strip():
            payload_lines.append(lines[index].strip())
            index += 1

        payload = " ".join(payload_lines).strip()
        speaker, text = _extract_speaker_and_text(payload)
        utterances.append(
            {
                "start": start,
                "end": end,
                "speaker": speaker,
                "text": text,
            }
        )

    return utterances
