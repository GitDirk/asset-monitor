"""Lädt die Konfiguration aus Umgebungsvariablen bzw. `.env`."""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import time
from typing import Optional

from dotenv import load_dotenv


class ConfigError(Exception):
    pass


def _parse_hhmm(value: str, name: str) -> time:
    try:
        hh, mm = value.strip().split(":")
        return time(int(hh), int(mm))
    except (ValueError, TypeError):
        raise ConfigError(f"{name} muss im Format HH:MM angegeben werden, nicht {value!r}")


def _float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw.replace(",", "."))
    except ValueError:
        raise ConfigError(f"{name} muss eine Zahl sein, nicht {raw!r}")


@dataclass(frozen=True)
class Settings:
    telegram_token: str
    telegram_chat_id: int
    db_path: str = "state/positions.db"
    default_sl_pct: float = 20.0
    default_tp_pct: float = 30.0
    poll_seconds: float = 30.0
    poll_seconds_closed: float = 300.0
    market_tz: str = "Europe/Berlin"
    market_open: time = time(8, 0)
    market_close: time = time(22, 0)
    ko_warn_pct: float = 3.0
    reminder_minutes: float = 30.0
    stale_minutes: float = 15.0
    heartbeat_time: Optional[time] = time(8, 0)
    log_level: str = "INFO"


def load_settings(env_file: Optional[str] = None) -> Settings:
    load_dotenv(env_file)

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        raise ConfigError("TELEGRAM_BOT_TOKEN und TELEGRAM_CHAT_ID müssen gesetzt sein")
    try:
        chat_id_int = int(chat_id)
    except ValueError:
        raise ConfigError(f"TELEGRAM_CHAT_ID muss eine Zahl sein, nicht {chat_id!r}")

    heartbeat_raw = os.getenv("HEARTBEAT_TIME", "08:00").strip()
    heartbeat = _parse_hhmm(heartbeat_raw, "HEARTBEAT_TIME") if heartbeat_raw else None

    return Settings(
        telegram_token=token,
        telegram_chat_id=chat_id_int,
        db_path=os.getenv("DB_PATH", "state/positions.db").strip() or "state/positions.db",
        default_sl_pct=_float("DEFAULT_SL_PCT", 20.0),
        default_tp_pct=_float("DEFAULT_TP_PCT", 30.0),
        poll_seconds=_float("POLL_SECONDS", 30.0),
        poll_seconds_closed=_float("POLL_SECONDS_CLOSED", 300.0),
        market_tz=os.getenv("MARKET_TZ", "Europe/Berlin").strip() or "Europe/Berlin",
        market_open=_parse_hhmm(os.getenv("MARKET_OPEN", "08:00"), "MARKET_OPEN"),
        market_close=_parse_hhmm(os.getenv("MARKET_CLOSE", "22:00"), "MARKET_CLOSE"),
        ko_warn_pct=_float("KO_WARN_PCT", 3.0),
        reminder_minutes=_float("REMINDER_MINUTES", 30.0),
        stale_minutes=_float("STALE_MINUTES", 15.0),
        heartbeat_time=heartbeat,
        log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO",
    )
