from __future__ import annotations

from dataclasses import asdict, dataclass
import logging

from .config import Settings
from .graph import GraphApiError, GraphClient
from .storage import LocalStore


@dataclass
class SyncResult:
    pages_seen: int = 0
    transcripts_seen: int = 0
    transcripts_downloaded: int = 0
    transcripts_skipped: int = 0
    transcripts_failed: int = 0
    removed_items_seen: int = 0
    delta_link_updated: bool = False

    def to_dict(self) -> dict[str, int | bool]:
        return asdict(self)


class TranscriptSyncService:
    def __init__(self, settings: Settings, graph: GraphClient, store: LocalStore) -> None:
        self.settings = settings
        self.graph = graph
        self.store = store
        self.logger = logging.getLogger(__name__)

    def sync_once(self, *, force: bool = False) -> SyncResult:
        result = SyncResult()
        next_link = self.store.load_delta_link() or self.settings.organizer_delta_resource

        while next_link:
            page = self.graph.request_json("GET", next_link)
            result.pages_seen += 1

            for transcript in page.get("value", []):
                if "@removed" in transcript:
                    result.removed_items_seen += 1
                    continue

                transcript_id = str(transcript.get("id", "")).strip()
                if not transcript_id:
                    result.transcripts_failed += 1
                    continue

                result.transcripts_seen += 1
                self.store.save_transcript_metadata(transcript)

                if self.store.has_transcript_content(transcript_id) and not force:
                    result.transcripts_skipped += 1
                    continue

                try:
                    transcript_content = self.graph.get_transcript_content(transcript)
                    metadata_content: bytes | None = None
                    try:
                        metadata_content = self.graph.get_metadata_content(transcript)
                    except (GraphApiError, ValueError) as exc:
                        self.logger.info("Metadata content unavailable for transcript %s: %s", transcript_id, exc)

                    self.store.save_transcript_bundle(transcript, transcript_content, metadata_content)
                    result.transcripts_downloaded += 1
                except (GraphApiError, ValueError) as exc:
                    self.store.save_transcript_failure(transcript_id, str(exc))
                    result.transcripts_failed += 1
                    self.logger.warning("Failed to download transcript %s: %s", transcript_id, exc)

            next_link = page.get("@odata.nextLink")
            if not next_link:
                delta_link = page.get("@odata.deltaLink")
                if delta_link:
                    self.store.save_delta_link(delta_link)
                    result.delta_link_updated = True

        self.store.save_sync_report(result.to_dict())
        return result
