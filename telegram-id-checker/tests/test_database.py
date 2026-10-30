from datetime import datetime, timezone

import mongomock
import pytest
from pymongo.errors import DuplicateKeyError

from database import IDRepository, OccurrenceMetadata


def _metadata(
    value: str,
    message_id: str,
    *,
    username: str = "tester",
    timestamp: datetime | None = None,
    gmail: str | None = None,
) -> OccurrenceMetadata:
    return OccurrenceMetadata(
        id=value,
        chat_id="-1001",
        chat_title="Test Group",
        message_id=message_id,
        user_id="42",
        username=username,
        display_name="Test User",
        timestamp=timestamp or datetime.now(timezone.utc),
        gmail=gmail,
    )


@pytest.fixture
def repository() -> IDRepository:
    client = mongomock.MongoClient()
    repo = IDRepository("mongodb://localhost/id_checker", client=client)
    repo.connect()
    return repo


def test_one_record_is_updated_for_repeated_checks(repository: IDRepository) -> None:
    first = repository.record_occurrence(_metadata("000123", "1"))
    second = repository.record_occurrence(_metadata("000123", "2"))

    assert first.is_new is True
    assert second.is_new is False
    assert second.previous_record["user"] == "@tester"
    assert repository.id_records.count_documents({"id": "000123"}) == 1
    record = repository.find_by_id("000123")
    assert set(record) == {"id", "user", "date", "occurrence_count"}
    assert record["user"] == "@tester"
    assert record["occurrence_count"] == 2


def test_previous_user_is_returned_before_each_duplicate_update(
    repository: IDRepository,
) -> None:
    first_date = datetime(2026, 9, 12, tzinfo=timezone.utc)
    second_date = datetime(2026, 9, 13, tzinfo=timezone.utc)
    third_date = datetime(2026, 9, 14, tzinfo=timezone.utc)

    first = repository.record_occurrence(
        _metadata("123456789", "1", username="userA", timestamp=first_date)
    )
    second = repository.record_occurrence(
        _metadata("123456789", "2", username="userB", timestamp=second_date)
    )
    third = repository.record_occurrence(
        _metadata("123456789", "3", username="userC", timestamp=third_date)
    )

    assert first.record["user"] == "@userA"
    assert second.previous_record["user"] == "@userA"
    assert second.record["user"] == "@userB"
    assert second.record["occurrence_count"] == 2
    assert third.previous_record["user"] == "@userB"
    assert third.record["user"] == "@userC"
    assert third.record["occurrence_count"] == 3


def test_same_user_is_still_a_duplicate(repository: IDRepository) -> None:
    repository.record_occurrence(_metadata("123", "1", username="same_user"))
    duplicate = repository.record_occurrence(
        _metadata("123", "2", username="same_user")
    )

    assert duplicate.is_new is False
    assert duplicate.record["occurrence_count"] == 2


def test_unique_index_rejects_direct_duplicate_primary_insert(repository: IDRepository) -> None:
    repository.id_records.insert_one({"id": "123", "occurrence_count": 1})
    with pytest.raises(DuplicateKeyError):
        repository.id_records.insert_one({"id": "123", "occurrence_count": 1})


def test_legacy_record_is_reduced_to_the_current_schema(repository: IDRepository) -> None:
    first_seen = datetime(2026, 9, 12, tzinfo=timezone.utc)
    repository.id_records.insert_one(
        {
            "id": "legacy",
            "first_seen": first_seen,
            "first_username": "old_user",
            "first_chat_id": "-1001",
            "first_chat_title": "Old Group",
            "occurrence_count": 4,
        }
    )

    repository.connect()
    record = repository.find_by_id("legacy")

    assert set(record) == {"id", "user", "date", "occurrence_count"}
    assert record["user"] == "@old_user"
    assert record["date"] == first_seen.replace(tzinfo=None)
    assert record["occurrence_count"] == 4


def test_group_registry_and_control_panel_queries(repository: IDRepository) -> None:
    first_date = datetime(2026, 9, 12, tzinfo=timezone.utc)
    second_date = datetime(2026, 9, 13, tzinfo=timezone.utc)
    repository.record_group("-1001", "First Group", first_date)
    repository.record_group("-1002", "Second Group", first_date)
    repository.record_group("-1001", "Renamed Group", second_date)

    repository.record_occurrence(
        _metadata("id-1", "1", username="userA", timestamp=first_date)
    )
    repository.record_occurrence(
        _metadata("id-2", "2", username="userA", timestamp=second_date)
    )
    repository.record_occurrence(
        _metadata("id-3", "3", username="userB", timestamp=second_date)
    )

    groups = repository.list_groups()
    users = repository.current_user_list()
    status = repository.control_status()

    assert groups[0]["chat_id"] == "-1001"
    assert groups[0]["chat_title"] == "Renamed Group"
    assert set(groups[0]) == {"chat_id", "chat_title", "last_seen"}
    assert repository.find_group("-1002")["chat_title"] == "Second Group"
    assert users[0] == {"user": "@userA", "id_count": 2}
    assert status == {
        "unique_ids": 3,
        "duplicate_occurrences": 0,
        "current_users": 2,
        "groups": 2,
    }


def test_group_message_can_be_claimed_only_once(repository: IDRepository) -> None:
    assert repository.claim_group_message("-1001", "message-1") is True
    assert repository.claim_group_message("-1001", "message-1") is False
    assert repository.claim_group_message("-1002", "message-1") is True
    assert repository.processed_messages.count_documents({}) == 2


def test_failed_group_message_processing_can_be_retried(repository: IDRepository) -> None:
    repository.claim_group_message("-1001", "message-1")
    repository.release_group_message("-1001", "message-1")

    assert repository.claim_group_message("-1001", "message-1") is True


def test_message_templates_round_trip(repository: IDRepository) -> None:
    entities = [
        {
            "type": "custom_emoji",
            "offset": 0,
            "length": 2,
            "custom_emoji_id": "animated-emoji-id",
        }
    ]

    repository.save_message_template(
        "welcome",
        "🎉 Welcome",
        entities,
    )

    assert repository.get_message_template("welcome") == {
        "key": "welcome",
        "text": "🎉 Welcome",
        "entities": entities,
    }


def test_native_rich_message_template_round_trip(repository: IDRepository) -> None:
    rich_message = {
        "blocks": [
            {"type": "heading", "size": 2, "text": "Duplicate Warning"},
            {"type": "paragraph", "text": "{details}"},
        ],
        "is_rtl": False,
    }

    repository.save_message_template(
        "duplicate_warning",
        "",
        [],
        rich_message=rich_message,
    )

    assert repository.get_message_template("duplicate_warning") == {
        "key": "duplicate_warning",
        "text": "",
        "entities": [],
        "rich_message": rich_message,
    }

    # Saving a regular text template clears an earlier RichMessage payload.
    repository.save_message_template("duplicate_warning", "Plain warning", [])
    assert repository.get_message_template("duplicate_warning") == {
        "key": "duplicate_warning",
        "text": "Plain warning",
        "entities": [],
    }


def test_clear_id_records_does_not_delete_other_collections(
    repository: IDRepository,
) -> None:
    repository.id_records.insert_one(
        {
            "id": "123",
            "user": "@tester",
            "date": datetime.now(timezone.utc),
            "occurrence_count": 2,
        }
    )
    repository.record_group("-1001", "Test Group", datetime.now(timezone.utc))
    repository.save_message_template("welcome", "Welcome", [])

    assert repository.clear_id_records() == 1
    assert repository.id_records.count_documents({}) == 0
    assert repository.find_group("-1001") is not None
    assert repository.get_message_template("welcome") is not None



def test_latest_duplicate_rewrites_gmail_in_the_same_id_record(
    repository: IDRepository,
) -> None:
    repository.record_occurrence(
        _metadata("gmail-123", "1", username="userA", gmail="old@example.com")
    )
    duplicate = repository.record_occurrence(
        _metadata("gmail-123", "2", username="userB", gmail="Mimifentahun")
    )

    assert duplicate.is_new is False
    assert duplicate.record["user"] == "@userB"
    assert duplicate.record["gmail"] == "Mimifentahun"
    assert repository.id_records.count_documents({"id": "gmail-123"}) == 1
    assert repository.find_by_id("gmail-123")["gmail"] == "Mimifentahun"


def test_missing_gmail_clears_previous_users_gmail(
    repository: IDRepository,
) -> None:
    repository.record_occurrence(
        _metadata("gmail-clear-123", "1", username="userA", gmail="old@example.com")
    )
    duplicate = repository.record_occurrence(
        _metadata("gmail-clear-123", "2", username="userB", gmail=None)
    )

    record = repository.find_by_id("gmail-clear-123")
    assert duplicate.is_new is False
    assert record["user"] == "@userB"
    assert "gmail" not in record
    assert repository.id_records.count_documents({"id": "gmail-clear-123"}) == 1


def test_migration_preserves_current_gmail_field(repository: IDRepository) -> None:
    repository.id_records.insert_one(
        {
            "id": "gmail-legacy",
            "user": "@latest",
            "date": datetime(2026, 10, 30, tzinfo=timezone.utc),
            "occurrence_count": 3,
            "gmail": "latest@example.com",
            "obsolete_field": "remove-me",
        }
    )

    repository.connect()
    record = repository.find_by_id("gmail-legacy")

    assert record["gmail"] == "latest@example.com"
    assert set(record) == {"id", "user", "date", "occurrence_count", "gmail"}
