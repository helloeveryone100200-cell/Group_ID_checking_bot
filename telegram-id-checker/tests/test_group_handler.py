import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import mongomock
from telegram.constants import ChatType

from database import IDRepository
from handlers import handle_group_message


def _repository() -> IDRepository:
    client = mongomock.MongoClient()
    repository = IDRepository("mongodb://localhost/id_checker", client=client)
    repository.connect()
    return repository


def _context(repository: IDRepository) -> SimpleNamespace:
    return SimpleNamespace(
        application=SimpleNamespace(
            bot_data={
                "repository": repository,
                "admin_ids": frozenset(),
            }
        )
    )


def _update(
    *,
    message_id: int,
    update_id: int,
    value: str,
    is_bot: bool = False,
    username: str = "tester",
    sent_at: datetime | None = None,
) -> SimpleNamespace:
    sender = SimpleNamespace(
        id=42,
        username=username,
        first_name=username,
        last_name="User",
        is_bot=is_bot,
    )
    message = SimpleNamespace(
        from_user=sender,
        message_id=message_id,
        date=sent_at or datetime.now(timezone.utc),
        text=f"ID: {value}",
        caption=None,
        reply_text=AsyncMock(),
    )
    chat = SimpleNamespace(
        id=-1001,
        type=ChatType.GROUP,
        title="Test Group",
        full_name="Test Group",
    )
    return SimpleNamespace(
        update_id=update_id,
        effective_message=message,
        effective_chat=chat,
        effective_user=sender,
    )


def test_redelivered_group_message_is_processed_once() -> None:
    repository = _repository()
    context = _context(repository)
    first_delivery = _update(message_id=10, update_id=100, value="123")
    redelivery = _update(message_id=10, update_id=101, value="123")

    asyncio.run(handle_group_message(first_delivery, context))
    asyncio.run(handle_group_message(redelivery, context))

    record = repository.find_by_id("123")
    assert record["occurrence_count"] == 1
    assert repository.processed_messages.count_documents({}) == 1
    first_delivery.effective_message.reply_text.assert_not_awaited()
    redelivery.effective_message.reply_text.assert_not_awaited()


def test_same_id_in_a_new_group_message_is_still_a_duplicate() -> None:
    repository = _repository()
    context = _context(repository)
    first_message = _update(message_id=10, update_id=100, value="123")
    second_message = _update(message_id=11, update_id=101, value="123")

    asyncio.run(handle_group_message(first_message, context))
    asyncio.run(handle_group_message(second_message, context))

    record = repository.find_by_id("123")
    assert record["occurrence_count"] == 2
    second_message.effective_message.reply_text.assert_awaited_once()


def test_first_seen_rolls_to_the_previous_user_and_dates_are_message_dates(
    monkeypatch,
) -> None:
    monkeypatch.setenv("DISPLAY_TIMEZONE", "Asia/Bangkok")
    repository = _repository()
    context = _context(repository)

    events = [
        ("userA", datetime(2026, 10, 27, 4, tzinfo=timezone.utc)),
        ("userB", datetime(2026, 10, 29, 4, tzinfo=timezone.utc)),
        ("userC", datetime(2026, 10, 30, 4, tzinfo=timezone.utc)),
        ("userD", datetime(2026, 10, 31, 4, tzinfo=timezone.utc)),
    ]
    notifications = []

    for index, (username, sent_at) in enumerate(events, start=1):
        update = _update(
            message_id=100 + index,
            update_id=200 + index,
            value="rolling-123",
            username=username,
            sent_at=sent_at,
        )
        asyncio.run(handle_group_message(update, context))
        if index == 1:
            update.effective_message.reply_text.assert_not_awaited()
        else:
            update.effective_message.reply_text.assert_awaited_once()
            notifications.append(update.effective_message.reply_text.await_args.args[0])

    expectations = [
        (
            "@userA",
            "2026-10-27",
            "@userB",
            "2026-10-29",
            "Total occurrences: 2",
        ),
        (
            "@userB",
            "2026-10-29",
            "@userC",
            "2026-10-30",
            "Total occurrences: 3",
        ),
        (
            "@userC",
            "2026-10-30",
            "@userD",
            "2026-10-31",
            "Total occurrences: 4",
        ),
    ]
    for warning, (previous_user, previous_date, current_user, current_date, count) in zip(
        notifications, expectations, strict=True
    ):
        first_seen_section = warning.split("Current:", maxsplit=1)[0]
        current_section = warning.split("Current:", maxsplit=1)[1]
        assert "First Seen:" in first_seen_section
        assert previous_user in first_seen_section
        assert previous_date in first_seen_section
        assert current_user in current_section
        assert current_date in current_section
        assert count in warning

    record = repository.find_by_id("rolling-123")
    assert record["user"] == "@userD"
    assert record["date"] == events[-1][1].replace(tzinfo=None)
    assert record["occurrence_count"] == 4
    assert set(record) == {"id", "user", "date", "occurrence_count"}


def test_timestamp_date_uses_configured_local_timezone(monkeypatch) -> None:
    from handlers import _format_timestamp

    monkeypatch.setenv("DISPLAY_TIMEZONE", "Asia/Bangkok")
    utc_timestamp = datetime(2026, 10, 29, 18, tzinfo=timezone.utc)

    # 18:00 UTC is already the next calendar day in Bangkok.
    assert _format_timestamp(utc_timestamp) == "2026-10-30"


def test_bot_authored_group_message_is_ignored() -> None:
    repository = _repository()
    context = _context(repository)
    bot_message = _update(
        message_id=10,
        update_id=100,
        value="123",
        is_bot=True,
    )

    asyncio.run(handle_group_message(bot_message, context))

    assert repository.id_records.count_documents({}) == 0
    assert repository.processed_messages.count_documents({}) == 0
    bot_message.effective_message.reply_text.assert_not_awaited()