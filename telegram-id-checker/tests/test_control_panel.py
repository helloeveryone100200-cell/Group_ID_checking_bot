import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import mongomock
from telegram.constants import ChatType

from database import IDRepository
from handlers import _send_broadcast, clear_ids, control_panel, start


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
        "Welcome Message",
        "Duplicate Warning",
        "Control Panel Message",
        "Broadcast All",
        "Broadcast Single",
    ]
    assert [button.style for button in buttons] == [
        "primary",
        "success",
        "success",
        "primary",
        "primary",
        "primary",
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


def test_clear_ids_requires_admin_confirmation() -> None:
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
    assert [button.text for button in buttons] == ["Confirm", "Cancel"]
    assert [button.style for button in buttons] == ["success", "danger"]
