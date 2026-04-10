from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
import json
import logging
import threading

from .config import Settings
from .storage import LocalStore
from .sync import TranscriptSyncService


class WebhookApplication:
    def __init__(self, settings: Settings, store: LocalStore, sync_service: TranscriptSyncService) -> None:
        self.settings = settings
        self.store = store
        self.sync_service = sync_service
        self.logger = logging.getLogger(__name__)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="transcript-sync")
        self._state_lock = threading.Lock()
        self._sync_running = False

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def send_json(self, handler: BaseHTTPRequestHandler, status: HTTPStatus, payload: object) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8")
        handler.send_response(status.value)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    def send_text(self, handler: BaseHTTPRequestHandler, status: HTTPStatus, payload: str) -> None:
        body = payload.encode("utf-8")
        handler.send_response(status.value)
        handler.send_header("Content-Type", "text/plain")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    def validation_token(self, path: str) -> str | None:
        query = parse_qs(urlparse(path).query)
        token = query.get("validationToken")
        if not token:
            return None
        return token[0]

    def read_json(self, handler: BaseHTTPRequestHandler) -> dict[str, object]:
        length = int(handler.headers.get("Content-Length", "0"))
        raw = handler.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON payload: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("Expected top-level JSON object.")
        return payload

    def schedule_sync(self, reason: str) -> bool:
        with self._state_lock:
            if self._sync_running:
                self.logger.info("Sync already running; skipping new request triggered by %s.", reason)
                return False
            self._sync_running = True

        self._executor.submit(self._run_sync, reason)
        return True

    def _run_sync(self, reason: str) -> None:
        try:
            self.logger.info("Starting transcript sync triggered by %s.", reason)
            result = self.sync_service.sync_once()
            self.logger.info("Transcript sync completed: %s", result.to_dict())
        except Exception:
            self.logger.exception("Transcript sync failed.")
        finally:
            with self._state_lock:
                self._sync_running = False

    def _has_expected_client_state(self, payload: dict[str, object]) -> bool:
        notifications = payload.get("value", [])
        if not isinstance(notifications, list):
            return False

        valid = False
        for item in notifications:
            if not isinstance(item, dict):
                continue
            client_state = item.get("clientState")
            if client_state in {None, self.settings.client_state}:
                valid = True
                continue
            self.logger.warning("Ignoring notification with unexpected clientState: %s", client_state)
        return valid

    def handle_webhook(self, handler: BaseHTTPRequestHandler) -> None:
        token = self.validation_token(handler.path)
        if token is not None:
            self.send_text(handler, HTTPStatus.OK, token)
            return

        try:
            payload = self.read_json(handler)
        except ValueError as exc:
            self.send_json(handler, HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return

        self.store.save_notification(payload, kind="webhook")
        if self._has_expected_client_state(payload):
            self.schedule_sync("webhook notification")
        self.send_json(handler, HTTPStatus.ACCEPTED, {"status": "accepted"})

    def handle_lifecycle(self, handler: BaseHTTPRequestHandler) -> None:
        token = self.validation_token(handler.path)
        if token is not None:
            self.send_text(handler, HTTPStatus.OK, token)
            return

        try:
            payload = self.read_json(handler)
        except ValueError as exc:
            self.send_json(handler, HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return

        self.store.save_notification(payload, kind="lifecycle")
        notifications = payload.get("value", [])
        if isinstance(notifications, list):
            for item in notifications:
                if not isinstance(item, dict):
                    continue
                lifecycle_event = item.get("lifecycleEvent")
                subscription_id = item.get("subscriptionId")
                self.logger.warning(
                    "Received lifecycle event %s for subscription %s.",
                    lifecycle_event,
                    subscription_id,
                )
                if lifecycle_event == "missed":
                    self.schedule_sync("lifecycle missed event")
                if lifecycle_event == "reauthorizationRequired" and subscription_id:
                    try:
                        renewed = self.sync_service.graph.renew_subscription(str(subscription_id))
                        self.store.save_subscription(renewed)
                        self.logger.info("Renewed subscription %s after lifecycle event.", subscription_id)
                    except Exception:
                        self.logger.exception("Failed to renew subscription %s.", subscription_id)

        self.send_json(handler, HTTPStatus.ACCEPTED, {"status": "accepted"})


def build_handler(app: WebhookApplication) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path == "/healthz":
                app.send_json(self, HTTPStatus.OK, {"status": "ok"})
                return
            if path in {"/webhook", "/lifecycle"}:
                token = app.validation_token(self.path)
                if token is not None:
                    app.send_text(self, HTTPStatus.OK, token)
                    return
                app.send_json(self, HTTPStatus.METHOD_NOT_ALLOWED, {"error": "Use POST for this endpoint."})
                return
            app.send_json(self, HTTPStatus.NOT_FOUND, {"error": "Not found"})

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path == "/healthz":
                app.send_json(self, HTTPStatus.OK, {"status": "ok"})
                return
            if path == "/webhook":
                app.handle_webhook(self)
                return
            if path == "/lifecycle":
                app.handle_lifecycle(self)
                return
            app.send_json(self, HTTPStatus.NOT_FOUND, {"error": "Not found"})

        def log_message(self, format: str, *args: object) -> None:
            app.logger.info("%s - %s", self.client_address[0], format % args)

    return Handler


def run_server(
    settings: Settings,
    store: LocalStore,
    sync_service: TranscriptSyncService,
    *,
    sync_on_start: bool = False,
) -> None:
    app = WebhookApplication(settings, store, sync_service)
    server = ThreadingHTTPServer((settings.webhook_host, settings.webhook_port), build_handler(app))
    logger = logging.getLogger(__name__)

    logger.info("Webhook server listening on http://%s:%s", settings.webhook_host, settings.webhook_port)
    logger.info("Health endpoint: http://%s:%s/healthz", settings.webhook_host, settings.webhook_port)
    logger.info("Webhook endpoint: http://%s:%s/webhook", settings.webhook_host, settings.webhook_port)
    logger.info("Lifecycle endpoint: http://%s:%s/lifecycle", settings.webhook_host, settings.webhook_port)

    if sync_on_start:
        app.schedule_sync("startup")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down webhook server.")
    finally:
        server.server_close()
        app.shutdown()
