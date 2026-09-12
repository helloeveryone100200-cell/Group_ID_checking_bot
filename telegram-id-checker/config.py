"""Environment-backed configuration for the Telegram ID checker."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


class ConfigError(ValueError):
    """Raised when required runtime configuration is missing or invalid."""


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigError(f"{name} is required")
    return value


def _parse_admin_ids(raw: str) -> frozenset[int]:
    values = [item.strip() for item in raw.split(",") if item.strip()]
    if not values:
        raise ConfigError("ADMIN_IDS must contain at least one Telegram user ID")

    try:
        admin_ids = frozenset(int(value) for value in values)
    except ValueError as exc:
        raise ConfigError("ADMIN_IDS must be a comma-separated list of integers") from exc

    if any(value <= 0 for value in admin_ids):
        raise ConfigError("ADMIN_IDS must contain positive Telegram user IDs")
    return admin_ids


@dataclass(frozen=True)
class Settings:
    bot_token: str
    mongodb_uri: str
    admin_ids: frozenset[int]
    mongodb_server_selection_timeout_ms: int = 10_000
    health_host: str = "0.0.0.0"
    health_port: int = 8080

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        try:
            health_port = int(os.getenv("PORT", "8080"))
        except ValueError as exc:
            raise ConfigError("PORT must be an integer") from exc
        if not 1 <= health_port <= 65_535:
            raise ConfigError("PORT must be between 1 and 65535")

        return cls(
            bot_token=_required("BOT_TOKEN"),
            mongodb_uri=_required("MONGODB_URI"),
            admin_ids=_parse_admin_ids(_required("ADMIN_IDS")),
            health_port=health_port,
        )