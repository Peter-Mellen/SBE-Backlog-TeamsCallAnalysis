from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
import json

from .config import Settings


class GraphApiError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        url: str | None = None,
        payload: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.url = url
        self.payload = payload


class GraphClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._access_token: str | None = None
        self._token_expiry = datetime.now(timezone.utc)

    def _full_url(self, path_or_url: str) -> str:
        if path_or_url.startswith("https://") or path_or_url.startswith("http://"):
            return path_or_url
        if not path_or_url.startswith("/"):
            path_or_url = f"/{path_or_url}"
        return f"{self.settings.graph_base_url.rstrip('/')}{path_or_url}"

    def _acquire_access_token(self) -> str:
        form = urlencode(
            {
                "client_id": self.settings.client_id,
                "client_secret": self.settings.client_secret,
                "scope": "https://graph.microsoft.com/.default",
                "grant_type": "client_credentials",
            }
        ).encode("utf-8")
        request = Request(self.settings.token_url, data=form, method="POST")
        request.add_header("Content-Type", "application/x-www-form-urlencoded")

        try:
            with urlopen(request, timeout=self.settings.request_timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise GraphApiError(
                f"Token request failed with {exc.code}: {body}",
                status_code=exc.code,
                url=self.settings.token_url,
                payload=body,
            ) from exc

        self._access_token = payload["access_token"]
        expires_in = int(payload.get("expires_in", 3600))
        self._token_expiry = datetime.now(timezone.utc) + timedelta(seconds=max(expires_in - 60, 60))
        return self._access_token

    def _get_access_token(self) -> str:
        if self._access_token and datetime.now(timezone.utc) < self._token_expiry:
            return self._access_token
        return self._acquire_access_token()

    def _request(
        self,
        method: str,
        path_or_url: str,
        *,
        json_body: dict[str, Any] | None = None,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        url = self._full_url(path_or_url)
        request_data = data
        request_headers = {
            "Authorization": f"Bearer {self._get_access_token()}",
        }
        if json_body is not None:
            request_data = json.dumps(json_body).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        if headers:
            request_headers.update(headers)

        request = Request(url, data=request_data, method=method)
        for key, value in request_headers.items():
            request.add_header(key, value)

        try:
            with urlopen(request, timeout=self.settings.request_timeout_seconds) as response:
                return response.status, dict(response.headers.items()), response.read()
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise GraphApiError(
                f"Graph request failed with {exc.code}: {body}",
                status_code=exc.code,
                url=url,
                payload=body,
            ) from exc

    def request_json(
        self,
        method: str,
        path_or_url: str,
        *,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        _, response_headers, body = self._request(
            method,
            path_or_url,
            json_body=json_body,
            headers=headers,
        )
        if not body:
            return {}
        content_type = response_headers.get("Content-Type", "")
        if "json" not in content_type:
            raise GraphApiError(
                f"Expected JSON response but received {content_type or 'unknown content type'}",
                url=self._full_url(path_or_url),
            )
        return json.loads(body.decode("utf-8"))

    def request_bytes(
        self,
        method: str,
        path_or_url: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> bytes:
        _, _, body = self._request(method, path_or_url, headers=headers)
        return body

    def _subscription_expiration(self) -> str:
        expires = datetime.now(timezone.utc) + timedelta(minutes=self.settings.subscription_duration_minutes)
        return expires.replace(microsecond=0).isoformat().replace("+00:00", "Z")

    def list_subscriptions(self) -> dict[str, Any]:
        return self.request_json("GET", "/subscriptions")

    def create_subscription(self) -> dict[str, Any]:
        notification_url = self.settings.require_notification_url()
        if self.settings.subscription_duration_minutes > 60 and not self.settings.lifecycle_notification_url:
            raise ValueError(
                "LIFECYCLE_NOTIFICATION_URL must be set if SUBSCRIPTION_DURATION_MINUTES is greater than 60."
            )

        body: dict[str, Any] = {
            "changeType": "created",
            "notificationUrl": notification_url,
            "resource": self.settings.subscription_resource,
            "expirationDateTime": self._subscription_expiration(),
            "clientState": self.settings.client_state,
            "latestSupportedTlsVersion": "v1_2",
        }
        if self.settings.lifecycle_notification_url:
            body["lifecycleNotificationUrl"] = self.settings.lifecycle_notification_url
        return self.request_json("POST", "/subscriptions", json_body=body)

    def renew_subscription(self, subscription_id: str) -> dict[str, Any]:
        body = {"expirationDateTime": self._subscription_expiration()}
        return self.request_json("PATCH", f"/subscriptions/{quote(subscription_id, safe='')}", json_body=body)

    def delete_subscription(self, subscription_id: str) -> None:
        self._request("DELETE", f"/subscriptions/{quote(subscription_id, safe='')}")

    def get_transcript_content(self, transcript: dict[str, Any]) -> bytes:
        content_url = transcript.get("transcriptContentUrl")
        if content_url:
            return self.request_bytes("GET", str(content_url))

        meeting_id = transcript.get("meetingId")
        transcript_id = transcript.get("id")
        if not meeting_id or not transcript_id:
            raise ValueError("Transcript payload does not include enough data to fetch content.")

        path = (
            f"/users/{self.settings.encoded_target_user_id}/onlineMeetings/{quote(str(meeting_id), safe='')}/"
            f"transcripts/{quote(str(transcript_id), safe='')}/content?$format=text/vtt"
        )
        return self.request_bytes("GET", path)

    def get_metadata_content(self, transcript: dict[str, Any]) -> bytes:
        meeting_id = transcript.get("meetingId")
        transcript_id = transcript.get("id")
        if not meeting_id or not transcript_id:
            raise ValueError("Transcript payload does not include enough data to fetch metadata content.")

        path = (
            f"/users/{self.settings.encoded_target_user_id}/onlineMeetings/{quote(str(meeting_id), safe='')}/"
            f"transcripts/{quote(str(transcript_id), safe='')}/metadataContent"
        )
        return self.request_bytes("GET", path)
