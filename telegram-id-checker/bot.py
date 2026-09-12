"""Standalone Telegram ID duplicate checker."""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from config import ConfigError, Settings
from database import IDRepository
from handlers import (
    check_id,
    duplicates,
    handle_error,
    handle_group_message,
    recent,
    start,
    stats,
)
from health_server import HealthServer

LOGGER = logging.getLogger(__name__)


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )


def main() -> None:
    configure_logging()
    try:
        settings = Settings.from_env()
    except ConfigError as exc:
        LOGGER.error("Configuration error: %s", exc)
        raise SystemExit(2) from exc

    LOGGER.info("Starting Telegram ID duplicate checker")
    repository = IDRepository(
        settings.mongodb_uri,
        server_selection_timeout_ms=settings.mongodb_server_selection_timeout_ms,
    )
    try:
        repository.connect()
    except Exception:
        repository.close()
        LOGGER.exception("MongoDB connection failed")
        raise SystemExit(1)

    started_at = datetime.now(timezone.utc)
    health = HealthServer(
        settings.health_host,
        settings.health_port,
        lambda: {
            "status": "ok",
            "service": "telegram-id-checker",
            "started_at": started_at.isoformat(),
        },
    )
    health.start()

    application = (
        Application.builder()
        .token(settings.bot_token)
        .build()
    )
    application.bot_data["repository"] = repository
    application.bot_data["admin_ids"] = settings.admin_ids

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("checkid", check_id))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("recent", recent))
    application.add_handler(CommandHandler("duplicates", duplicates))
    application.add_handler(
        MessageHandler(filters.ChatType.GROUPS & ~filters.COMMAND, handle_group_message)
    )
    application.add_error_handler(handle_error)

    try:
        LOGGER.info("Telegram bot is ready")
        application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            close_loop=False,
        )
    except Exception:
        LOGGER.exception("Telegram bot stopped unexpectedly")
        raise
    finally:
        health.stop()
        repository.close()
        LOGGER.info("Telegram bot shut down cleanly")


if __name__ == "__main__":
    main()