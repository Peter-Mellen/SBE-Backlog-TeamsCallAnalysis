from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json

from .vtt import parse_webvtt


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


class LocalStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.notifications_dir = self.root / "notifications"
        self.transcripts_dir = self.root / "transcripts"
        self.subscriptions_dir = self.root / "subscriptions"
        self.state_dir = self.root / "state"
        for directory in (
            self.root,
            self.notifications_dir,
            self.transcripts_dir,
            self.subscriptions_dir,
            self.state_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def transcript_dir_for(self, transcript_id: str) -> Path:
        key = hashlib.sha256(transcript_id.encode("utf-8")).hexdigest()[:16]
        return self.transcripts_dir / key

    def has_transcript_content(self, transcript_id: str) -> bool:
        return (self.transcript_dir_for(transcript_id) / "content.vtt").exists()

    def save_notification(self, payload: object, *, kind: str) -> Path:
        path = self.notifications_dir / f"{_utc_stamp()}_{kind}.json"
        _write_json(path, payload)
        return path

    def save_subscription(self, subscription: dict[str, object]) -> None:
        subscription_id = str(subscription.get("id", "unknown"))
        _write_json(self.subscriptions_dir / f"{subscription_id}.json", subscription)
        _write_json(self.state_dir / "last_subscription.json", subscription)

    def load_last_subscription_id(self) -> str | None:
        path = self.state_dir / "last_subscription.json"
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        value = payload.get("id")
        return str(value) if value else None

    def save_delta_link(self, delta_link: str) -> None:
        (self.state_dir / "delta_link.txt").write_text(delta_link, encoding="utf-8")

    def load_delta_link(self) -> str | None:
        path = self.state_dir / "delta_link.txt"
        if not path.exists():
            return None
        value = path.read_text(encoding="utf-8").strip()
        return value or None

    def save_sync_report(self, report: dict[str, object]) -> None:
        _write_json(self.state_dir / "last_sync.json", report)

    def save_transcript_metadata(self, transcript: dict[str, object]) -> Path:
        transcript_id = str(transcript["id"])
        transcript_dir = self.transcript_dir_for(transcript_id)
        transcript_dir.mkdir(parents=True, exist_ok=True)
        _write_json(transcript_dir / "metadata.json", transcript)
        return transcript_dir

    def save_transcript_bundle(
        self,
        transcript: dict[str, object],
        transcript_content: bytes | None,
        metadata_content: bytes | None = None,
    ) -> Path:
        transcript_dir = self.save_transcript_metadata(transcript)

        if transcript_content is not None:
            content_path = transcript_dir / "content.vtt"
            content_path.write_bytes(transcript_content)
            utterances = parse_webvtt(transcript_content.decode("utf-8", errors="replace"))
            _write_json(transcript_dir / "utterances.json", utterances)

        if metadata_content is not None:
            binary_path = transcript_dir / "metadataContent.bin"
            binary_path.write_bytes(metadata_content)
            try:
                parsed = json.loads(metadata_content.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                parsed = None
            if parsed is not None:
                _write_json(transcript_dir / "metadataContent.json", parsed)

        return transcript_dir

    def save_transcript_failure(self, transcript_id: str, error_message: str) -> None:
        transcript_dir = self.transcript_dir_for(transcript_id)
        transcript_dir.mkdir(parents=True, exist_ok=True)
        _write_json(
            transcript_dir / "last_error.json",
            {
                "capturedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "error": error_message,
            },
        )
