from __future__ import annotations

from typing import Any
import argparse
import json
import logging
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from sbe_teams_call_analysis.config import Settings
    from sbe_teams_call_analysis.graph import GraphClient
    from sbe_teams_call_analysis.server import run_server
    from sbe_teams_call_analysis.storage import LocalStore
    from sbe_teams_call_analysis.sync import TranscriptSyncService
else:
    from .config import Settings
    from .graph import GraphClient
    from .server import run_server
    from .storage import LocalStore
    from .sync import TranscriptSyncService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Backend-only proof of concept for pulling Teams transcripts with Microsoft Graph."
    )
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Path to the env file. Defaults to .env in the repo root.",
    )

    subparsers = parser.add_subparsers(dest="command")

    serve = subparsers.add_parser("serve", help="Run the local webhook server.")
    serve.add_argument(
        "--sync-on-start",
        action="store_true",
        help="Kick off a delta sync when the server starts.",
    )

    sync = subparsers.add_parser("sync", help="Run a transcript delta sync immediately.")
    sync.add_argument("--force", action="store_true", help="Re-download transcript content even if already stored.")

    subparsers.add_parser("create-subscription", help="Create a Graph webhook subscription.")

    renew = subparsers.add_parser("renew-subscription", help="Renew a Graph subscription.")
    renew.add_argument("subscription_id", nargs="?", help="Subscription ID. Defaults to the last created subscription.")

    subparsers.add_parser("list-subscriptions", help="List current Graph subscriptions.")

    delete = subparsers.add_parser("delete-subscription", help="Delete a Graph subscription.")
    delete.add_argument("subscription_id", help="Subscription ID to delete.")

    return parser


def _json_print(payload: Any) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def _load_runtime(env_file: str) -> tuple[Settings, LocalStore, GraphClient, TranscriptSyncService]:
    settings = Settings.from_env(env_file)
    store = LocalStore(settings.storage_root)
    graph = GraphClient(settings)
    sync_service = TranscriptSyncService(settings, graph, store)
    return settings, store, graph, sync_service


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return

    try:
        settings, store, graph, sync_service = _load_runtime(args.env_file)
    except ValueError as exc:
        parser.error(str(exc))
        return

    if args.command == "serve":
        run_server(settings, store, sync_service, sync_on_start=args.sync_on_start)
        return

    if args.command == "sync":
        result = sync_service.sync_once(force=args.force)
        _json_print(result.to_dict())
        return

    if args.command == "create-subscription":
        subscription = graph.create_subscription()
        store.save_subscription(subscription)
        _json_print(subscription)
        return

    if args.command == "renew-subscription":
        subscription_id = args.subscription_id or store.load_last_subscription_id()
        if not subscription_id:
            parser.error("No subscription ID supplied and no last subscription stored in data/state.")
            return
        subscription = graph.renew_subscription(subscription_id)
        store.save_subscription(subscription)
        _json_print(subscription)
        return

    if args.command == "list-subscriptions":
        _json_print(graph.list_subscriptions())
        return

    if args.command == "delete-subscription":
        graph.delete_subscription(args.subscription_id)
        _json_print({"deleted": args.subscription_id})
        return

    parser.error(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    main()
