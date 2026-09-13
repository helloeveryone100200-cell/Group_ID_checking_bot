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
    username = context.bot.username
    if not username:
        raise RuntimeError("Telegram bot username is unavailable")
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Add me to your chat!", url=f"https://t.me/{username}?startgroup=true")]]
    )
    await message.reply_text(WELCOME_MESSAGE, reply_markup=keyboard)


def _format_timestamp(value: datetime | None) -> str:
    if value is None:
        return "unknown"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _format_record(record: dict[str, Any], *, heading: str, occurrences_label: str) -> str:
    first_actor = _actor(
        record.get("first_username"),
        record.get("first_display_name", ""),
        str(record.get("first_user_id", "")),
    )
    return (
        f"{heading}\n"
        f"ID: {record.get('id', '')}\n"
        f"First Seen:\n"
        f"👤 {first_actor}\n"
        f"📍 {record.get('first_chat_title', record.get('first_chat_id', 'unknown'))}\n"
        f"🕐 {_format_timestamp(record.get('first_seen'))}\n"
        f"{occurrences_label}: {record.get('occurrence_count', 1)}"
    )


async def _is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    admin_ids = context.application.bot_data["admin_ids"]
    return user is not None and user.id in admin_ids


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

    repository: IDRepository = context.application.bot_data["repository"]
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
        timestamp=utc_now(),
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
    warning = (
        "⚠️ DUPLICATE ID\n"
        f"ID: {parsed.value}\n"
        "First Seen:\n"
        f"👤 {_actor(result.record.get('first_username'), result.record.get('first_display_name', ''), str(result.record.get('first_user_id', '')))}\n"
        f"📍 {result.record.get('first_chat_title', result.record.get('first_chat_id', 'unknown'))}\n"
        f"🕐 {_format_timestamp(result.record.get('first_seen'))}\n"
        "Current:\n"
        f"👤 {current_actor}\n"
        f"📍 {chat_title}\n"
        f"🕐 {_format_timestamp(metadata.timestamp)}\n"
        f"Total occurrences: {result.record.get('occurrence_count', 2)}"
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
        f"Today\n🟢 New IDs: {values['new_today']}\n"
        f"🔴 Duplicates: {values['duplicates_today']}\n\n"
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
        actor = _actor(row.get("username"), row.get("display_name", ""), str(row.get("user_id", "")))
        lines.append(f"{row['id']} — {actor} — {_format_timestamp(row.get('timestamp'))}")
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
        actor = _actor(row.get("username"), row.get("display_name", ""), str(row.get("user_id", "")))
        lines.append(f"{row['id']} — {actor} — {_format_timestamp(row.get('timestamp'))}")
    await update.effective_message.reply_text("\n".join(lines))


async def handle_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    LOGGER.error("Unhandled Telegram update error: %s", context.error, exc_info=context.error)