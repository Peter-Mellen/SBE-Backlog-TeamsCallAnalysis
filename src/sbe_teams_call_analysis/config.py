from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote
import os


def _strip_wrapping_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def parse_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue

        if value and value[0] not in {"'", '"'} and " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        values[key] = _strip_wrapping_quotes(value)
    return values


def _coalesce_env(env_file: str | Path) -> dict[str, str]:
    file_values = parse_dotenv(Path(env_file))
    return {**file_values, **os.environ}


def _get_required(env: dict[str, str], key: str) -> str:
    value = env.get(key, "").strip()
    if not value:
        raise ValueError(f"Missing required setting: {key}")
    return value


def _get_optional(env: dict[str, str], key: str) -> str | None:
    value = env.get(key)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _get_int(env: dict[str, str], key: str, default: int) -> int:
    value = _get_optional(env, key)
    if value is None:
        return default
    return int(value)


@dataclass(frozen=True)
class Settings:
    tenant_id: str
    client_id: str
    client_secret: str
    target_user_id: str
    notification_url: str | None
    lifecycle_notification_url: str | None
    client_state: str
    storage_root: Path
    webhook_host: str
    webhook_port: int
    subscription_duration_minutes: int
    initial_sync_start_date: str | None
    graph_base_url: str
    request_timeout_seconds: int

    @classmethod
    def from_env(cls, env_file: str | Path = ".env") -> "Settings":
        env = _coalesce_env(env_file)
        return cls(
            tenant_id=_get_required(env, "TENANT_ID"),
            client_id=_get_required(env, "CLIENT_ID"),
            client_secret=_get_required(env, "CLIENT_SECRET"),
            target_user_id=_get_required(env, "TARGET_USER_ID"),
            notification_url=_get_optional(env, "NOTIFICATION_URL"),
            lifecycle_notification_url=_get_optional(env, "LIFECYCLE_NOTIFICATION_URL"),
            client_state=_get_optional(env, "CLIENT_STATE") or "replace-me-before-prod",
            storage_root=Path(_get_optional(env, "STORAGE_ROOT") or "data"),
            webhook_host=_get_optional(env, "WEBHOOK_HOST") or "127.0.0.1",
            webhook_port=_get_int(env, "WEBHOOK_PORT", 8080),
            subscription_duration_minutes=_get_int(env, "SUBSCRIPTION_DURATION_MINUTES", 55),
            initial_sync_start_date=_get_optional(env, "INITIAL_SYNC_START_DATE"),
            graph_base_url=_get_optional(env, "GRAPH_BASE_URL") or "https://graph.microsoft.com/v1.0",
            request_timeout_seconds=_get_int(env, "REQUEST_TIMEOUT_SECONDS", 30),
        )

    @property
    def token_url(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"

    @property
    def encoded_target_user_id(self) -> str:
        return quote(self.target_user_id, safe="")

    @property
    def subscription_resource(self) -> str:
        return f"users/{self.target_user_id}/onlineMeetings/getAllTranscripts"

    @property
    def organizer_delta_resource(self) -> str:
        start_clause = ""
        if self.initial_sync_start_date:
            encoded_start = quote(self.initial_sync_start_date, safe=":-TZ")
            start_clause = f",startDateTime={encoded_start}"
        return (
            f"/users/{self.encoded_target_user_id}/onlineMeetings/"
            f"getAllTranscripts(meetingOrganizerUserId='{self.encoded_target_user_id}'"
            f"{start_clause})/delta"
        )

    def require_notification_url(self) -> str:
        if not self.notification_url:
            raise ValueError("NOTIFICATION_URL must be set for subscription creation.")
        return self.notification_url
