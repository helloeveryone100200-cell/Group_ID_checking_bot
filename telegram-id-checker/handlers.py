"""Telegram update handlers for monitoring and admin commands."""

from __future__ import annotations

import logging
from datetime import datetime, time, timezone
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType
from telegram.ext import ContextTypes

from database import IDRepository, OccurrenceMetadata, utc_now
from parser import parse_id

LOGGER = logging.getLogger(__name__)

WELCOME_MESSAGE = (
    "👋 မင်္ဂလာပါ။ Telegram ID Duplicate Checker မှ ကြိုဆိုပါတယ်။\n\n"
    "ဒီ bot က group message တွေထဲက ID Numbers တွေကို စောင့်ကြည့်ပြီး "
    "ID တစ်ခုကို ထပ်မံတွေ့ရှိရင် duplicate သတိပေးချက် ပို့ပေးပါတယ်။"
)

CONTROL_PANEL_MESSAGE = (
    "🔐 CONTROL PANEL\n\n"
    "အောက်ပါခလုတ်များမှ လိုအပ်သောလုပ်ဆောင်ချက်ကို ရွေးချယ်ပါ။"
)


def _control_panel_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Status",
                    callback_data="panel:status",
                    style="primary",
                )
            ],
            [
                InlineKeyboardButton(
                    "User Lists",
                    callback_data="panel:userlists",
                    style="success",
                ),
                InlineKeyboardButton(
                    "Group Lists",
                    callback_data="panel:grouplists",
                    style="success",
                ),
            ],
            [
                InlineKeyboardButton(
                    "Broadcast All",
                    callback_data="panel:broadcast:all",
                    style="danger",
                ),
                InlineKeyboardButton(
                    "Broadcast Single",
                    callback_data="panel:broadcast:single",
                    style="danger",
                ),
            ],
        ]
    )


def _panel_home_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Control Panel",
                    callback_data="panel:home",
                    style="primary",
                )
            ]
        ]
    )


def _broadcast_cancel_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Cancel",
                    callback_data="panel:broadcast:cancel",
                    style="danger",
                )
            ]
        ]
    )


def _broadcast_confirm_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Confirm Broadcast",
                    callback_data="panel:broadcast:confirm",
                    style="danger",
                ),
                InlineKeyboardButton(
                    "Cancel",
                    callback_data="panel:broadcast:cancel",
                    style="danger",
                ),
            ]
        ]
    )


def _user_details(update: Update) -> tuple[str, str | None, str]:
    user = update.effective_user
    if user is None:
        return "unknown", None, "Unknown user"
    display_name = " ".join(part for part in [user.first_name, user.last_name] if part).strip()
    return str(user.id), user.username, display_name or str(user.id)


def _chat_details(update: Update) -> tuple[str, str]:
    chat = update.effective_chat
    if chat is None:
        return "unknown", "Unknown chat"
    title = chat.title or chat.full_name or str(chat.id)
    return str(chat.id), title


def _message_id(update: Update) -> str:
    return str(update.effective_message.message_id) if update.effective_message else "unknown"


def _actor(username: str | None, display_name: str, user_id: str) -> str:
    return f"@{username}" if username else (display_name or user_id)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send the welcome message and a button for adding the bot to a group."""
    message = update.effective_message
    if message is None:
        return
    chat = update.effective_chat
    if chat is not None and chat.type in {ChatType.GROUP, ChatType.SUPERGROUP}:
        repository: IDRepository = context.application.bot_data["repository"]
        try:
            repository.record_group(
                str(chat.id),
                chat.title or chat.full_name or str(chat.id),
                utc_now(),
            )
        except Exception:
            LOGGER.exception("Database error while registering a group from /start")
    username = context.bot.username
    if not username:
        raise RuntimeError("Telegram bot username is unavailable")
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Add me to your chat!", url=f"https://t.me/{username}?startgroup=true")]]
    )
    await message.reply_text(WELCOME_MESSAGE, reply_markup=keyboard)
    if (
        update.effective_chat is not None
        and update.effective_chat.type == ChatType.PRIVATE
        and await _is_admin(update, context)
    ):
        await message.reply_text(CONTROL_PANEL_MESSAGE, reply_markup=_control_panel_markup())


def _format_timestamp(value: datetime | None) -> str:
    if value is None:
        return "unknown"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d")


def _format_record(record: dict[str, Any], *, heading: str, occurrences_label: str) -> str:
    return (
        f"{heading}\n"
        f"ID: {record.get('id', '')}\n"
        f"👤 {record.get('user', 'unknown')}\n"
        f"📅 {_format_timestamp(record.get('date'))}\n"
        f"{occurrences_label}: {record.get('occurrence_count', 1)}"
    )


async def _is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    admin_ids = context.application.bot_data["admin_ids"]
    return user is not None and user.id in admin_ids


async def _is_panel_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    chat = update.effective_chat
    return (
        chat is not None
        and chat.type == ChatType.PRIVATE
        and await _is_admin(update, context)
    )


def _status_text(repository: IDRepository) -> str:
    values = repository.control_status()
    return (
        "🔵 STATUS\n\n"
        "✅ Bot status: ONLINE\n"
        f"🆔 Unique IDs: {values['unique_ids']:,}\n"
        f"🔁 Duplicate occurrences: {values['duplicate_occurrences']:,}\n"
        f"👤 Current users: {values['current_users']:,}\n"
        f"💬 Registered groups: {values['groups']:,}"
    )


def _user_list_text(repository: IDRepository) -> str:
    rows = repository.current_user_list()
    if not rows:
        return "🟢 USER LISTS\n\nNo current users found."
    lines = ["🟢 USER LISTS", ""]
    for position, row in enumerate(rows, start=1):
        lines.append(f"{position}. {row.get('user', 'unknown')} — {row.get('id_count', 0)} IDs")
    return "\n".join(lines)


def _group_list_text(repository: IDRepository) -> str:
    rows = repository.list_groups()
    if not rows:
        return "🟢 GROUP LISTS\n\nNo groups registered yet."
    lines = ["🟢 GROUP LISTS", ""]
    for position, row in enumerate(rows, start=1):
        lines.append(
            f"{position}. {row.get('chat_title', 'Unknown group')}\n"
            f"   ID: {row.get('chat_id', 'unknown')}\n"
            f"   Last seen: {_format_timestamp(row.get('last_seen'))}"
        )
    return "\n".join(lines)


async def control_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _is_panel_admin(update, context):
        return
    message = update.effective_message
    if message is not None:
        await message.reply_text(
            CONTROL_PANEL_MESSAGE,
            reply_markup=_control_panel_markup(),
        )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _is_panel_admin(update, context):
        return
    repository: IDRepository = context.application.bot_data["repository"]
    try:
        text = _status_text(repository)
    except Exception:
        LOGGER.exception("Database error while loading control panel status")
        text = "🔵 STATUS\n\nUnable to load status right now."
    await update.effective_message.reply_text(text, reply_markup=_panel_home_markup())


async def userlists(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _is_panel_admin(update, context):
        return
    repository: IDRepository = context.application.bot_data["repository"]
    try:
        text = _user_list_text(repository)
    except Exception:
        LOGGER.exception("Database error while loading user list")
        text = "🟢 USER LISTS\n\nUnable to load users right now."
    await update.effective_message.reply_text(text, reply_markup=_panel_home_markup())


async def grouplists(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _is_panel_admin(update, context):
        return
    repository: IDRepository = context.application.bot_data["repository"]
    try:
        text = _group_list_text(repository)
    except Exception:
        LOGGER.exception("Database error while loading group list")
        text = "🟢 GROUP LISTS\n\nUnable to load groups right now."
    await update.effective_message.reply_text(text, reply_markup=_panel_home_markup())


async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _is_panel_admin(update, context):
        return
    await update.effective_message.reply_text(
        "🔴 BROADCAST\n\nChoose a broadcast target from the control panel.",
        reply_markup=_control_panel_markup(),
    )


async def _send_broadcast(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    mode: str,
    text: str,
    chat_id: str | None = None,
) -> str:
    repository: IDRepository = context.application.bot_data["repository"]
    if mode == "all":
        targets = repository.list_groups(limit=None)
    else:
        target = repository.find_group(chat_id or "")
        targets = [target] if target is not None else []

    if not targets:
        return "🔴 BROADCAST RESULT\n\nNo target groups found."

    sent = 0
    failed = 0
    for target in targets:
        try:
            await context.bot.send_message(chat_id=target["chat_id"], text=text)
            sent += 1
        except Exception:
            failed += 1
            LOGGER.exception("Broadcast failed for chat_id=%s", target.get("chat_id"))

    target_label = "all groups" if mode == "all" else "single group"
    return (
        "🔴 BROADCAST RESULT\n\n"
        f"Target: {target_label}\n"
        f"✅ Sent: {sent}\n"
        f"❌ Failed: {failed}"
    )


async def handle_admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Collect the next private text after an admin chooses a broadcast action."""
    if not await _is_panel_admin(update, context):
        return
    message = update.effective_message
    if message is None or not message.text:
        return

    mode = context.user_data.pop("broadcast_mode", None)
    if mode not in {"all", "single"}:
        return

    text = message.text.strip()
    if not text or len(text) > 4096:
        await message.reply_text(
            "Broadcast message must contain 1–4096 characters.",
            reply_markup=_broadcast_cancel_markup(),
        )
        context.user_data["broadcast_mode"] = mode
        return

    chat_id: str | None = None
    target_label = "ALL registered groups"
    if mode == "single":
        if "|" not in text:
            await message.reply_text(
                "Use this format:\nCHAT_ID | message",
                reply_markup=_broadcast_cancel_markup(),
            )
            context.user_data["broadcast_mode"] = mode
            return
        chat_id, text = [part.strip() for part in text.split("|", 1)]
        if not chat_id or not text:
            await message.reply_text(
                "Both CHAT_ID and message are required.",
                reply_markup=_broadcast_cancel_markup(),
            )
            context.user_data["broadcast_mode"] = mode
            return
        repository: IDRepository = context.application.bot_data["repository"]
        target = repository.find_group(chat_id)
        if target is None:
            await message.reply_text(
                "That chat is not in GROUP LISTS. Use a registered group chat ID.",
                reply_markup=_broadcast_cancel_markup(),
            )
            context.user_data["broadcast_mode"] = mode
            return
        target_label = f"{target.get('chat_title', 'Unknown group')} ({chat_id})"

    context.user_data["pending_broadcast"] = {
        "mode": mode,
        "chat_id": chat_id,
        "text": text,
    }
    await message.reply_text(
        "🔴 BROADCAST PREVIEW\n\n"
        f"Target: {target_label}\n\n"
        f"Message:\n{text}\n\n"
        "Press Confirm Broadcast to send it.",
        reply_markup=_broadcast_confirm_markup(),
    )


async def handle_panel_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    if query is None:
        return
    if not await _is_panel_admin(update, context):
        await query.answer("Admin access only.", show_alert=True)
        return

    await query.answer()
    data = query.data or ""
    repository: IDRepository = context.application.bot_data["repository"]

    if data == "panel:home":
        await query.edit_message_text(
            CONTROL_PANEL_MESSAGE,
            reply_markup=_control_panel_markup(),
        )
        return
    if data == "panel:status":
        await query.edit_message_text(
            _status_text(repository),
            reply_markup=_panel_home_markup(),
        )
        return
    if data == "panel:userlists":
        await query.edit_message_text(
            _user_list_text(repository),
            reply_markup=_panel_home_markup(),
        )
        return
    if data == "panel:grouplists":
        await query.edit_message_text(
            _group_list_text(repository),
            reply_markup=_panel_home_markup(),
        )
        return
    if data == "panel:broadcast:all":
        context.user_data.pop("pending_broadcast", None)
        context.user_data["broadcast_mode"] = "all"
        await query.edit_message_text(
            "🔴 BROADCAST ALL\n\n"
            "Send the message to broadcast to all registered groups.",
            reply_markup=_broadcast_cancel_markup(),
        )
        return
    if data == "panel:broadcast:single":
        context.user_data.pop("pending_broadcast", None)
        context.user_data["broadcast_mode"] = "single"
        await query.edit_message_text(
            "🔴 BROADCAST SINGLE\n\n"
            "Send the message in this format:\n"
            "CHAT_ID | message",
            reply_markup=_broadcast_cancel_markup(),
        )
        return
    if data == "panel:broadcast:cancel":
        context.user_data.pop("broadcast_mode", None)
        context.user_data.pop("pending_broadcast", None)
        await query.edit_message_text(
            CONTROL_PANEL_MESSAGE,
            reply_markup=_control_panel_markup(),
        )
        return
    if data == "panel:broadcast:confirm":
        pending = context.user_data.pop("pending_broadcast", None)
        if not pending:
            await query.edit_message_text(
                "No pending broadcast found.",
                reply_markup=_panel_home_markup(),
            )
            return
        await query.edit_message_text("⏳ Sending broadcast...")
        result = await _send_broadcast(
            context,
            mode=pending["mode"],
            text=pending["text"],
            chat_id=pending.get("chat_id"),
        )
        await query.edit_message_text(result, reply_markup=_panel_home_markup())


async def handle_group_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Process group text/captions and ignore all other message types."""
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None or chat.type not in {
        ChatType.GROUP,
        ChatType.SUPERGROUP,
    }:
        return

    repository: IDRepository = context.application.bot_data["repository"]
    timestamp = utc_now()
    try:
        repository.record_group(
            str(chat.id),
            chat.title or chat.full_name or str(chat.id),
            timestamp,
        )
    except Exception:
        LOGGER.exception("Database error while registering a group")

    text = message.text or message.caption
    parsed = parse_id(text)
    if parsed.ambiguous:
        LOGGER.warning(
            "Ambiguous ID headers ignored in chat_id=%s message_id=%s",
            chat.id,
            message.message_id,
        )
        return
    if parsed.value is None:
        return

    user_id, username, display_name = _user_details(update)
    chat_id, chat_title = _chat_details(update)
    metadata = OccurrenceMetadata(
        id=parsed.value,
        chat_id=chat_id,
        chat_title=chat_title,
        message_id=_message_id(update),
        user_id=user_id,
        username=username,
        display_name=display_name,
        timestamp=timestamp,
    )
    try:
        result = repository.record_occurrence(metadata)
    except Exception:
        LOGGER.exception("Database error while recording an ID")
        return

    if result.is_new:
        LOGGER.info("New ID detected in chat_id=%s", chat.id)
        return

    current_actor = _actor(username, display_name, user_id)
    previous = result.previous_record or {}
    warning = (
        "⚠️ DUPLICATE ID\n\n"
        f"ID: {parsed.value}\n\n"
        "First Seen:\n"
        f"👤 {previous.get('user', 'unknown')}\n"
        f"📅 {_format_timestamp(previous.get('date'))}\n\n"
        "Current:\n"
        f"👤 {current_actor}\n"
        f"📅 {_format_timestamp(metadata.timestamp)}\n\n"
        f"📊 Total occurrences: {result.record.get('occurrence_count', 2)}"
    )
    try:
        await message.reply_text(warning)
        LOGGER.info("Duplicate detected in chat_id=%s", chat.id)
    except Exception:
        LOGGER.exception("Telegram error while sending duplicate warning")


async def check_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _is_admin(update, context):
        return
    if len(context.args) != 1 or not context.args[0].strip():
        await update.effective_message.reply_text("Usage: /checkid <id>")
        return

    value = context.args[0].strip()
    repository: IDRepository = context.application.bot_data["repository"]
    try:
        record = repository.find_by_id(value)
    except Exception:
        LOGGER.exception("Database error during /checkid")
        await update.effective_message.reply_text("Unable to check that ID right now.")
        return

    if record is None:
        await update.effective_message.reply_text(
            f"🔎 ID CHECK\nID: {value}\nStatus: 🟢 NOT FOUND"
        )
        return
    await update.effective_message.reply_text(
        _format_record(record, heading="🔎 ID CHECK\nStatus: 🔴 DUPLICATE", occurrences_label="Occurrences")
    )


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _is_admin(update, context):
        return
    now = utc_now()
    start_of_day = datetime.combine(now.date(), time.min, tzinfo=timezone.utc)
    repository: IDRepository = context.application.bot_data["repository"]
    try:
        values = repository.stats(start_of_day)
    except Exception:
        LOGGER.exception("Database error during /stats")
        await update.effective_message.reply_text("Unable to load statistics right now.")
        return
    await update.effective_message.reply_text(
        "📊 ID CHECKER\n"
        f"Today (by current record)\n🟢 New IDs: {values['new_today']}\n"
        f"🔴 Duplicate IDs: {values['duplicates_today']}\n\n"
        f"All Time\nUnique IDs: {values['unique_ids']:,}\n"
        f"Duplicate Occurrences: {values['duplicate_occurrences']:,}"
    )


async def recent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _is_admin(update, context):
        return
    repository: IDRepository = context.application.bot_data["repository"]
    try:
        rows = repository.recent_occurrences()
    except Exception:
        LOGGER.exception("Database error during /recent")
        await update.effective_message.reply_text("Unable to load recent IDs right now.")
        return
    if not rows:
        await update.effective_message.reply_text("🕐 RECENT IDS\nNo IDs detected yet.")
        return
    lines = ["🕐 RECENT IDS"]
    for row in rows:
        lines.append(f"{row['id']} — {row.get('user', 'unknown')} — {_format_timestamp(row.get('date'))}")
    await update.effective_message.reply_text("\n".join(lines))


async def duplicates(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _is_admin(update, context):
        return
    repository: IDRepository = context.application.bot_data["repository"]
    try:
        rows = repository.recent_occurrences(duplicates_only=True)
    except Exception:
        LOGGER.exception("Database error during /duplicates")
        await update.effective_message.reply_text("Unable to load recent duplicates right now.")
        return
    if not rows:
        await update.effective_message.reply_text("🔴 RECENT DUPLICATES\nNo duplicates detected yet.")
        return
    lines = ["🔴 RECENT DUPLICATES"]
    for row in rows:
        lines.append(f"{row['id']} — {row.get('user', 'unknown')} — {_format_timestamp(row.get('date'))}")
    await update.effective_message.reply_text("\n".join(lines))


async def handle_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    LOGGER.error("Unhandled Telegram update error: %s", context.error, exc_info=context.error)