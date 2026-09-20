"""Zuverlässiger Telegram-Versand mit Postausgang.

Fällt das Netz aus, während ein Alarm rausgehen soll, landet die Nachricht in
der Datenbank und wird beim nächsten Prüflauf erneut versucht. Ohne das wäre
ein Knock-out- oder Störungsalarm endgültig verloren, denn diese Meldungen
werden nur einmal erzeugt.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Optional

from .store import Store, utcnow
from .telegram import TelegramClient

log = logging.getLogger(__name__)

# Nach so vielen vergeblichen Versuchen bzw. so langer Zeit wird eine
# Nachricht verworfen: dann ist sie ohnehin veraltet.
MAX_ATTEMPTS = 60
MAX_AGE = timedelta(hours=24)


class Notifier:
    """Callable: `notifier(text)` sendet oder stellt in den Postausgang."""

    def __init__(self, telegram: TelegramClient, store: Store):
        self.telegram = telegram
        self.store = store

    def __call__(self, text: str) -> bool:
        # Wartet noch etwas im Postausgang, muss die neue Nachricht dahinter
        # einsortiert werden, damit die Reihenfolge stimmt.
        if self.store.count_pending():
            self.store.enqueue_message(text)
            self.flush()
            return False
        if self.telegram.send(text):
            return True
        log.warning("Nachricht in den Postausgang gelegt (Zustellung fehlgeschlagen)")
        self.store.enqueue_message(text)
        return False

    def flush(self, limit: int = 20) -> int:
        """Versucht wartende Nachrichten zuzustellen. Gibt die Anzahl zurück."""
        sent = 0
        now = utcnow()
        for message_id, text, created_at, attempts in self.store.pending_messages(limit):
            too_old = created_at is not None and now - created_at > MAX_AGE
            if attempts >= MAX_ATTEMPTS or too_old:
                log.error(
                    "Nachricht nach %d Versuchen verworfen: %s", attempts, _preview(text)
                )
                self.store.drop_message(message_id)
                continue
            if self.telegram.send(_with_delay_note(text, created_at, now)):
                self.store.drop_message(message_id)
                sent += 1
                continue
            self.store.mark_attempt(message_id)
            break  # Netz ist weiterhin gestört, Reihenfolge bleibt erhalten
        if sent:
            log.info("%d nachgeholte Nachricht(en) zugestellt", sent)
        return sent


def _with_delay_note(text: str, created_at, now) -> str:
    if created_at is None:
        return text
    minutes = (now - created_at).total_seconds() / 60
    if minutes < 2:
        return text
    return f"{text}\n\n⏱ <i>Verspätet zugestellt, erzeugt vor {minutes:.0f} min (Netzwerkstörung).</i>"


def _preview(text: str, length: int = 60) -> str:
    first = text.splitlines()[0] if text else ""
    return first[:length]
