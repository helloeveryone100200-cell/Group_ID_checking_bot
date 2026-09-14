import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import mongomock
from telegram.constants import ChatType

from database import IDRepository
from handlers import (
    _send_broadcast,
    clear_ids,
    control_panel,
    handle_admin_text,
    handle_panel_callback,
    start,
)


def _context(repository: IDRepository, *, admin_ids: frozenset[int]) -> SimpleNamespace:
    return SimpleNamespace(
        application=SimpleNamespace(
            bot_data={"admin_ids": admin_ids, "repository": repository}
        ),
        bot=SimpleNamespace(send_message=AsyncMock()),
        user_data={},
    )


def _update(user_id: int, message: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        effective_chat=SimpleNamespace(type=ChatType.PRIVATE),
        effective_message=message,
    )


def _callback_update(user_id: int, data: str, query: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        effective_chat=SimpleNamespace(type=ChatType.PRIVATE),
        callback_query=SimpleNamespace(
            data=data,
            answer=query.answer,
            edit_message_text=query.edit_message_text,
        ),
    )


def _repository() -> IDRepository:
    client = mongomock.MongoClient()
    repository = IDRepository("mongodb://localhost/id_checker", client=client)
    repository.connect()
    return repository


def test_control_panel_is_only_sent_to_admins() -> None:
    repository = _repository()
    admin_message = SimpleNamespace(reply_text=AsyncMock())
    non_admin_message = SimpleNamespace(reply_text=AsyncMock())

    asyncio.run(
        control_panel(
            _update(100, admin_message),
            _context(repository, admin_ids=frozenset({100})),
        )
    )
    asyncio.run(
        control_panel(
            _update(200, non_admin_message),
            _context(repository, admin_ids=frozenset({100})),
        )
    )

    admin_message.reply_text.assert_awaited_once()
    non_admin_message.reply_text.assert_not_awaited()
    markup = admin_message.reply_text.await_args.kwargs["reply_markup"]
    buttons = [button for row in markup.inline_keyboard for button in row]
    assert [button.text for button in buttons] == [
        "Status",
        "User Lists",
        "Group Lists",
        "Check ID",
        "Recent",
        "Duplicates",
        "Welcome Message",
        "Duplicate Warning",
        "Control Panel Message",
        "Clear IDs",
        "Broadcast All",
        "Broadcast Users",
        "Broadcast Single",
    ]
    assert [button.style for button in buttons] == [
        "primary",
        "success",
        "success",
        "success",
        "success",
        "success",
        "primary",
        "primary",
        "primary",
        "danger",
        "danger",
        "danger",
        "danger",
    ]


def test_control_panel_uses_saved_message_template() -> None:
    repository = _repository()
    repository.save_message_template(
        "control_panel",
        "CUSTOM CONTROL PANEL",
        [],
    )
    admin_message = SimpleNamespace(reply_text=AsyncMock())

    asyncio.run(
        control_panel(
            _update(100, admin_message),
            _context(repository, admin_ids=frozenset({100})),
        )
    )

    admin_message.reply_text.assert_awaited_once()
    assert admin_message.reply_text.await_args.args[0] == "CUSTOM CONTROL PANEL"


def test_control_panel_quick_lookup_buttons_run_their_commands() -> None:
    repository = _repository()
    repository.id_records.insert_one(
        {
            "id": "123",
            "user": "alice",
            "date": datetime.now(timezone.utc),
            "occurrence_count": 2,
        }
    )
    context = _context(repository, admin_ids=frozenset({100}))

    recent_query = SimpleNamespace(answer=AsyncMock(), edit_message_text=AsyncMock())
    asyncio.run(
        handle_panel_callback(
            _callback_update(100, "panel:recent", recent_query),
            context,
        )
    )
    assert "🕐 RECENT IDS" in recent_query.edit_message_text.await_args.args[0]
    assert "123 — alice" in recent_query.edit_message_text.await_args.args[0]

    duplicate_query = SimpleNamespace(answer=AsyncMock(), edit_message_text=AsyncMock())
    asyncio.run(
        handle_panel_callback(
            _callback_update(100, "panel:duplicates", duplicate_query),
            context,
        )
    )
    assert "🔴 RECENT DUPLICATES" in duplicate_query.edit_message_text.await_args.args[0]
    assert "123 — alice" in duplicate_query.edit_message_text.await_args.args[0]

    check_query = SimpleNamespace(answer=AsyncMock(), edit_message_text=AsyncMock())
    asyncio.run(
        handle_panel_callback(
            _callback_update(100, "panel:checkid", check_query),
            context,
        )
    )
    assert context.user_data["checkid_mode"] is True
    assert "Send the ID" in check_query.edit_message_text.await_args.args[0]

    check_message = SimpleNamespace(reply_text=AsyncMock(), text="123")
    asyncio.run(handle_admin_text(_update(100, check_message), context))
    assert "🔎 ID CHECK" in check_message.reply_text.await_args.args[0]
    assert "Occurrences: 2" in check_message.reply_text.await_args.args[0]


def test_start_add_to_chat_button_is_primary() -> None:
    repository = _repository()
    message = SimpleNamespace(reply_text=AsyncMock())
    context = _context(repository, admin_ids=frozenset())
    context.bot.username = "example_bot"

    asyncio.run(
        start(
            _update(200, message),
            context,
        )
    )

    message.reply_text.assert_awaited_once()
    markup = message.reply_text.await_args.kwargs["reply_markup"]
    button = markup.inline_keyboard[0][0]
    assert button.text == "Add me to your chat!"
    assert button.style == "primary"
    assert button.url == "https://t.me/example_bot?startgroup=true"


def test_broadcast_all_sends_only_to_registered_groups() -> None:
    repository = _repository()
    now = datetime.now(timezone.utc)
    repository.record_group("-1001", "First Group", now)
    repository.record_group("-1002", "Second Group", now)
    context = _context(repository, admin_ids=frozenset({100}))

    result = asyncio.run(
        _send_broadcast(context, mode="all", text="System announcement")
    )

    assert "Sent: 2" in result
    assert context.bot.send_message.await_count == 2
    sent_chat_ids = {
        call.kwargs["chat_id"] for call in context.bot.send_message.await_args_list
    }
    assert sent_chat_ids == {"-1001", "-1002"}


def test_broadcast_users_sends_to_registered_users() -> None:
    repository = _repository()
    now = datetime.now(timezone.utc)
    repository.record_user("101", "first_user", "First User", now)
    repository.record_user("202", None, "Second User", now)
    context = _context(repository, admin_ids=frozenset({100}))

    result = asyncio.run(
        _send_broadcast(context, mode="users", text="System announcement")
    )

    assert "Target: all users" in result
    assert "Sent: 2" in result
    sent_chat_ids = {
        call.kwargs["chat_id"] for call in context.bot.send_message.await_args_list
    }
    assert sent_chat_ids == {"101", "202"}


def test_clear_ids_shows_two_primary_options() -> None:
    repository = _repository()
    admin_message = SimpleNamespace(reply_text=AsyncMock())

    asyncio.run(
        clear_ids(
            _update(100, admin_message),
            _context(repository, admin_ids=frozenset({100})),
        )
    )

    admin_message.reply_text.assert_awaited_once()
    markup = admin_message.reply_text.await_args.kwargs["reply_markup"]
    buttons = [button for row in markup.inline_keyboard for button in row]
    assert [button.text for button in buttons] == [
        "Clear ID Records",
        "Processed Message IDs",
    ]
    assert [button.style for button in buttons] == ["primary", "primary"]


def test_clear_id_records_requires_confirmation_and_preserves_processed_ids() -> None:
    repository = _repository()
    repository.id_records.insert_one({"id": "123", "occurrence_count": 1})
    repository.claim_group_message("-1001", "message-1")
    context = _context(repository, admin_ids=frozenset({100}))
    option_query = SimpleNamespace(answer=AsyncMock(), edit_message_text=AsyncMock())

    asyncio.run(
        handle_panel_callback(
            _callback_update(100, "panel:clear_ids:id_records", option_query),
            context,
        )
    )

    assert repository.id_records.count_documents({}) == 1
    assert repository.processed_messages.count_documents({}) == 1
    assert context.user_data["clear_ids_pending"] == "id_records"

    confirm_query = SimpleNamespace(answer=AsyncMock(), edit_message_text=AsyncMock())
    asyncio.run(
        handle_panel_callback(
            _callback_update(100, "panel:clear_ids:confirm", confirm_query),
            context,
        )
    )

    assert repository.id_records.count_documents({}) == 0
    assert repository.processed_messages.count_documents({}) == 1


def test_clear_processed_ids_requires_confirmation_and_preserves_id_records() -> None:
    repository = _repository()
    repository.id_records.insert_one({"id": "123", "occurrence_count": 1})
    repository.claim_group_message("-1001", "message-1")
    context = _context(repository, admin_ids=frozenset({100}))
    option_query = SimpleNamespace(answer=AsyncMock(), edit_message_text=AsyncMock())

    asyncio.run(
        handle_panel_callback(
            _callback_update(
                100,
                "panel:clear_ids:processed_messages",
                option_query,
            ),
            context,
        )
    )

    assert repository.id_records.count_documents({}) == 1
    assert repository.processed_messages.count_documents({}) == 1
    assert context.user_data["clear_ids_pending"] == "processed_messages"

    confirm_query = SimpleNamespace(answer=AsyncMock(), edit_message_text=AsyncMock())
    asyncio.run(
        handle_panel_callback(
            _callback_update(100, "panel:clear_ids:confirm", confirm_query),
            context,
        )
    )

    assert repository.id_records.count_documents({}) == 1
    assert repository.processed_messages.count_documents({}) == 0
