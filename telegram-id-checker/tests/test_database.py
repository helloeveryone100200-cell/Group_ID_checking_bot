from datetime import datetime, timezone

import mongomock
import pytest
from pymongo.errors import DuplicateKeyError

from database import IDRepository, OccurrenceMetadata


def _metadata(value: str, message_id: str) -> OccurrenceMetadata:
    return OccurrenceMetadata(
        id=value,
        chat_id="-1001",
        chat_title="Test Group",
        message_id=message_id,
        user_id="42",
        username="tester",
        display_name="Test User",
        timestamp=datetime.now(timezone.utc),
    )


@pytest.fixture
def repository() -> IDRepository:
    client = mongomock.MongoClient()
    repo = IDRepository("mongodb://localhost/id_checker", client=client)
    repo.connect()
    return repo


def test_unique_index_and_concurrency_safe_duplicate_accounting(
    repository: IDRepository,
) -> None:
    first = repository.record_occurrence(_metadata("000123", "1"))
    second = repository.record_occurrence(_metadata("000123", "2"))

    assert first.is_new is True
    assert second.is_new is False
    assert repository.id_records.count_documents({"id": "000123"}) == 1
    assert repository.find_by_id("000123")["occurrence_count"] == 2
    assert repository.id_occurrences.count_documents({"id": "000123"}) == 2


def test_unique_index_rejects_direct_duplicate_primary_insert(repository: IDRepository) -> None:
    repository.id_records.insert_one({"id": "123", "occurrence_count": 1})
    with pytest.raises(DuplicateKeyError):
        repository.id_records.insert_one({"id": "123", "occurrence_count": 1})