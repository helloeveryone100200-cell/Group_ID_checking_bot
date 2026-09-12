"""MongoDB persistence with a global unique ID record and occurrence history."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.collection import ReturnDocument
from pymongo.errors import DuplicateKeyError
from pymongo.uri_parser import parse_uri

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class OccurrenceMetadata:
    id: str
    chat_id: str
    chat_title: str
    message_id: str
    user_id: str
    username: str | None
    display_name: str
    timestamp: datetime


@dataclass(frozen=True)
class RecordResult:
    is_new: bool
    record: dict[str, Any]


class IDRepository:
    """Repository for globally unique IDs and their occurrences."""

    def __init__(
        self,
        mongodb_uri: str,
        *,
        server_selection_timeout_ms: int = 10_000,
        client: MongoClient | None = None,
    ) -> None:
        parsed_uri = parse_uri(mongodb_uri)
        database_name = parsed_uri.get("database")
        if not database_name:
            raise ValueError("MONGODB_URI must include a database name")
        self.client = client or MongoClient(
            mongodb_uri,
            serverSelectionTimeoutMS=server_selection_timeout_ms,
            connectTimeoutMS=server_selection_timeout_ms,
        )
        self.database = self.client[database_name]
        self.id_records = self.database["id_records"]
        self.id_occurrences = self.database["id_occurrences"]

    def connect(self) -> None:
        self.client.admin.command("ping")
        self.id_records.create_index(
            [("id", ASCENDING)],
            unique=True,
            name="id_unique",
        )
        self.id_occurrences.create_index([("timestamp", DESCENDING)])
        self.id_occurrences.create_index(
            [("id", ASCENDING), ("timestamp", DESCENDING)]
        )
        LOGGER.info("MongoDB connection established and indexes are ready")

    def close(self) -> None:
        self.client.close()

    @staticmethod
    def _record_from_metadata(metadata: OccurrenceMetadata) -> dict[str, Any]:
        return {
            "id": metadata.id,
            "first_seen": metadata.timestamp,
            "first_chat_id": metadata.chat_id,
            "first_chat_title": metadata.chat_title,
            "first_message_id": metadata.message_id,
            "first_user_id": metadata.user_id,
            "first_username": metadata.username,
            "first_display_name": metadata.display_name,
            "occurrence_count": 1,
        }

    @staticmethod
    def _occurrence_from_metadata(
        metadata: OccurrenceMetadata,
        *,
        is_duplicate: bool,
    ) -> dict[str, Any]:
        return {
            "id": metadata.id,
            "chat_id": metadata.chat_id,
            "chat_title": metadata.chat_title,
            "message_id": metadata.message_id,
            "user_id": metadata.user_id,
            "username": metadata.username,
            "display_name": metadata.display_name,
            "timestamp": metadata.timestamp,
            "is_duplicate": is_duplicate,
        }

    def record_occurrence(self, metadata: OccurrenceMetadata) -> RecordResult:
        """Atomically create the primary record or increment its count."""
        primary = self._record_from_metadata(metadata)
        try:
            self.id_records.insert_one(primary)
        except DuplicateKeyError:
            record = self.id_records.find_one_and_update(
                {"id": metadata.id},
                {"$inc": {"occurrence_count": 1}},
                return_document=ReturnDocument.AFTER,
            )
            if record is None:
                # A transient race with a manually removed record is safest to
                # surface to the caller rather than treating it as a new ID.
                raise RuntimeError("ID record disappeared during duplicate update")
            self.id_occurrences.insert_one(
                self._occurrence_from_metadata(metadata, is_duplicate=True)
            )
            return RecordResult(is_new=False, record=record)

        self.id_occurrences.insert_one(
            self._occurrence_from_metadata(metadata, is_duplicate=False)
        )
        return RecordResult(is_new=True, record=primary)

    def find_by_id(self, value: str) -> dict[str, Any] | None:
        return self.id_records.find_one({"id": value}, {"_id": 0})

    def stats(self, start_of_day: datetime) -> dict[str, int]:
        return {
            "new_today": self.id_occurrences.count_documents(
                {"timestamp": {"$gte": start_of_day}, "is_duplicate": False}
            ),
            "duplicates_today": self.id_occurrences.count_documents(
                {"timestamp": {"$gte": start_of_day}, "is_duplicate": True}
            ),
            "unique_ids": self.id_records.count_documents({}),
            "duplicate_occurrences": self.id_occurrences.count_documents(
                {"is_duplicate": True}
            ),
        }

    def recent_occurrences(
        self,
        *,
        duplicates_only: bool = False,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        query = {"is_duplicate": True} if duplicates_only else {}
        return list(
            self.id_occurrences.find(query, {"_id": 0})
            .sort("timestamp", DESCENDING)
            .limit(limit)
        )


def utc_now() -> datetime:
    return datetime.now(timezone.utc)