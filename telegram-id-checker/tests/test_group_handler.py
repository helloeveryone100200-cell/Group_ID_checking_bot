import asyncio
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
) -> SimpleNamespace:
    sender = SimpleNamespace(
        id=42,
        username="tester",
        first_name="Test",
        last_name="User",
        is_bot=is_bot,
    )
    message = SimpleNamespace(
        from_user=sender,
        message_id=message_id,
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