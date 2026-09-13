"""MongoDB persistence with one current record per globally unique ID."""

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
    previous_record: dict[str, Any] | None = None


class IDRepository:
    """Repository for globally unique IDs and their current state."""

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
        self.group_records = self.database["group_records"]
        self.message_templates = self.database["message_templates"]
        self.processed_messages = self.database["processed_messages"]

    def connect(self) -> None:
        self.client.admin.command("ping")
        self._migrate_legacy_records()
        self.id_records.create_index(
            [("id", ASCENDING)],
            unique=True,
            name="id_unique",
        )
        self.id_records.create_index(
            [("date", DESCENDING)],
            name="date_desc",
        )
        self.group_records.create_index(
            [("chat_id", ASCENDING)],
            unique=True,
            name="chat_id_unique",
        )
        self.group_records.create_index(
            [("last_seen", DESCENDING)],
            name="last_seen_desc",
        )
        self.message_templates.create_index(
            [("key", ASCENDING)],
            unique=True,
            name="message_template_key_unique",
        )
        self.processed_messages.create_index(
            [("chat_id", ASCENDING), ("message_id", ASCENDING)],
            unique=True,
            name="processed_group_message_unique",
        )
        LOGGER.info("MongoDB connection established and indexes are ready")

    def close(self) -> None:
        self.client.close()

    def record_group(self, chat_id: str, chat_title: str, timestamp: datetime) -> None:
        """Register the latest group that delivered a message to the bot."""
        self.group_records.update_one(
            {"chat_id": chat_id},
            {
                "$set": {
                    "chat_title": chat_title,
                    "last_seen": timestamp,
                }
            },
            upsert=True,
        )

    def claim_group_message(self, chat_id: str, message_id: str) -> bool:
        """Claim one group message so redelivered updates are processed once."""
        try:
            self.processed_messages.insert_one(
                {
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "processed_at": utc_now(),
                }
            )
        except DuplicateKeyError:
            return False
        return True

    def release_group_message(self, chat_id: str, message_id: str) -> None:
        """Release a claim when ID processing fails before it is recorded."""
        self.processed_messages.delete_one(
            {
                "chat_id": chat_id,
                "message_id": message_id,
            }
        )

    def list_groups(self, *, limit: int | None = 100) -> list[dict[str, Any]]:
        cursor = self.group_records.find(
            {},
            {"_id": 0, "chat_id": 1, "chat_title": 1, "last_seen": 1},
        ).sort("last_seen", DESCENDING)
        if limit is not None:
            cursor = cursor.limit(limit)
        return list(cursor)

    def find_group(self, chat_id: str) -> dict[str, Any] | None:
        return self.group_records.find_one({"chat_id": chat_id}, {"_id": 0})

    def get_message_template(self, key: str) -> dict[str, Any] | None:
        return self.message_templates.find_one(
            {"key": key},
            {"_id": 0, "key": 1, "text": 1, "entities": 1},
        )

    def save_message_template(
        self,
        key: str,
        text: str,
        entities: list[dict[str, Any]],
    ) -> None:
        self.message_templates.update_one(
            {"key": key},
            {
                "$set": {
                    "text": text,
                    "entities": entities,
                    "updated_at": utc_now(),
                }
            },
            upsert=True,
        )

    def current_user_list(self, *, limit: int = 100) -> list[dict[str, Any]]:
        pipeline = [
            {"$group": {"_id": "$user", "id_count": {"$sum": 1}}},
            {"$sort": {"id_count": -1, "_id": 1}},
            {"$limit": limit},
            {"$project": {"_id": 0, "user": "$_id", "id_count": 1}},
        ]
        return list(self.id_records.aggregate(pipeline))

    def control_status(self) -> dict[str, int]:
        return {
            "unique_ids": self.id_records.count_documents({}),
            "duplicate_occurrences": self._duplicate_occurrence_total(),
            "current_users": len(self.id_records.distinct("user")),
            "groups": self.group_records.count_documents({}),
        }

    def _migrate_legacy_records(self) -> None:
        """Keep existing records while reducing them to the current schema."""
        allowed_fields = {"_id", "id", "user", "date", "occurrence_count"}
        migrated = 0
        for record in self.id_records.find({}):
            if (
                set(record).issubset(allowed_fields)
                and {"id", "user", "date", "occurrence_count"}.issubset(record)
            ):
                continue

            username = record.get("user") or record.get("first_username") or record.get("username")
            if username:
                user = str(username)
                if not user.startswith("@"):
                    user = f"@{user}"
            else:
                user = str(
                    record.get("first_display_name")
                    or record.get("display_name")
                    or record.get("first_user_id")
                    or record.get("user_id")
                    or "unknown"
                )

            date = (
                record.get("date")
                or record.get("first_seen")
                or record.get("timestamp")
                or utc_now()
            )
            try:
                occurrence_count = max(int(record.get("occurrence_count", 1)), 1)
            except (TypeError, ValueError):
                occurrence_count = 1

            clean_record = {
                "_id": record["_id"],
                "id": str(record["id"]),
                "user": user,
                "date": date,
                "occurrence_count": occurrence_count,
            }
            self.id_records.replace_one({"_id": record["_id"]}, clean_record)
            migrated += 1

        if migrated:
            LOGGER.info("Migrated %s ID records to the current schema", migrated)

    @staticmethod
    def _user_from_metadata(metadata: OccurrenceMetadata) -> str:
        if metadata.username:
            return f"@{metadata.username.lstrip('@')}"
        return metadata.display_name or metadata.user_id

    @staticmethod
    def _record_from_metadata(metadata: OccurrenceMetadata) -> dict[str, Any]:
        return {
            "id": metadata.id,
            "user": IDRepository._user_from_metadata(metadata),
            "date": metadata.timestamp,
            "occurrence_count": 1,
        }

    def record_occurrence(self, metadata: OccurrenceMetadata) -> RecordResult:
        """Create a record or atomically replace its current user and date."""
        primary = self._record_from_metadata(metadata)
        try:
            self.id_records.insert_one(primary)
        except DuplicateKeyError:
            previous = self.id_records.find_one_and_update(
                {"id": metadata.id},
                {
                    "$set": {
                        "user": primary["user"],
                        "date": primary["date"],
                    },
                    "$inc": {"occurrence_count": 1},
                },
                projection={"_id": 0},
                return_document=ReturnDocument.BEFORE,
            )
            if previous is None:
                # A transient race with a manually removed record is safest to
                # surface to the caller rather than treating it as a new ID.
                raise RuntimeError("ID record disappeared during duplicate update")
            occurrence_count = int(previous.get("occurrence_count", 1)) + 1
            updated = {
                "id": metadata.id,
                "user": primary["user"],
                "date": primary["date"],
                "occurrence_count": occurrence_count,
            }
            previous["occurrence_count"] = int(previous.get("occurrence_count", 1))
            return RecordResult(
                is_new=False,
                record=updated,
                previous_record=previous,
            )

        return RecordResult(is_new=True, record=primary)

    def find_by_id(self, value: str) -> dict[str, Any] | None:
        return self.id_records.find_one({"id": value}, {"_id": 0})

    def clear_id_records(self) -> int:
        """Delete all stored duplicate-check records and return the count."""
        result = self.id_records.delete_many({})
        return int(result.deleted_count)

    def clear_processed_messages(self) -> int:
        """Delete all processed-message idempotency records and return the count."""
        result = self.processed_messages.delete_many({})
        return int(result.deleted_count)

    def stats(self, start_of_day: datetime) -> dict[str, int]:
        today_query = {"date": {"$gte": start_of_day}}
        return {
            "new_today": self.id_records.count_documents(
                {**today_query, "occurrence_count": 1}
            ),
            "duplicates_today": self.id_records.count_documents(
                {**today_query, "occurrence_count": {"$gt": 1}}
            ),
            "unique_ids": self.id_records.count_documents({}),
            "duplicate_occurrences": self._duplicate_occurrence_total(),
        }

    def _duplicate_occurrence_total(self) -> int:
        return sum(
            max(int(record.get("occurrence_count", 1)) - 1, 0)
            for record in self.id_records.find({}, {"occurrence_count": 1})
        )

    def recent_occurrences(
        self,
        *,
        duplicates_only: bool = False,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        query = {"occurrence_count": {"$gt": 1}} if duplicates_only else {}
        return list(
            self.id_records.find(query, {"_id": 0})
            .sort("date", DESCENDING)
            .limit(limit)
        )


def utc_now() -> datetime:
    return datetime.now(timezone.utc)