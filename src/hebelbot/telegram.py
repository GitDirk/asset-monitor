"""Schlanker Telegram-Bot-API-Client (Long-Polling, ohne externe Bot-Library)."""
from __future__ import annotations

import logging
import time
from typing import List, Optional

import requests

log = logging.getLogger(__name__)

MAX_MESSAGE_LEN = 4000  # Telegram-Limit liegt bei 4096 Zeichen
# Wartezeiten zwischen den Sendeversuchen einer Nachricht
RETRY_DELAYS = (1.0, 3.0)


class TelegramError(Exception):
    """Fehler beim Aufruf der Bot-API.

    `retriable` unterscheidet Netzwerkprobleme (später erneut versuchen) von
    Antworten, die Telegram inhaltlich ablehnt (erneutes Senden hilft nie).
    """

    def __init__(self, message: str, retriable: bool = True):
        super().__init__(message)
        self.retriable = retriable


class TelegramClient:
    def __init__(self, token: str, chat_id: int, session: Optional[requests.Session] = None):
        self.base = f"https://api.telegram.org/bot{token}"
        self.chat_id = chat_id
        self.session = session or requests.Session()

    def _call(self, method: str, payload: dict, timeout: float = 20.0) -> dict:
        try:
            resp = self.session.post(f"{self.base}/{method}", json=payload, timeout=timeout)
            data = resp.json()
        except (requests.RequestException, ValueError) as exc:
            # Exception-Text enthält die URL mit Token -> nicht weiterreichen
            raise TelegramError(f"Telegram {method} fehlgeschlagen ({type(exc).__name__})") from None
        if not data.get("ok"):
            # 5xx und 429 sind vorübergehend, 4xx (z. B. ungültiges HTML) nicht
            code = data.get("error_code", 0)
            raise TelegramError(
                f"Telegram {method}: {data.get('description', 'unbekannter Fehler')}",
                retriable=code >= 500 or code == 429,
            )
        return data

    def send(self, text: str) -> bool:
        """Sendet an den konfigurierten Chat.

        Gibt False zurück, wenn die Nachricht wegen eines Netzwerkproblems
        nicht zugestellt wurde und ein späterer Versuch sinnvoll ist. Lehnt
        Telegram die Nachricht inhaltlich ab, wird das als Fehler geloggt und
        True zurückgegeben, damit sie nicht endlos wiederholt wird.
        """
        ok = True
        for chunk in _split(text):
            for attempt in range(len(RETRY_DELAYS) + 1):
                try:
                    self._call(
                        "sendMessage",
                        {
                            "chat_id": self.chat_id,
                            "text": chunk,
                            "parse_mode": "HTML",
                            "disable_web_page_preview": True,
                        },
                    )
                    break
                except TelegramError as exc:
                    if not exc.retriable:
                        log.error("Telegram lehnt die Nachricht ab (wird verworfen): %s", exc)
                        break
                    if attempt < len(RETRY_DELAYS):
                        time.sleep(RETRY_DELAYS[attempt])
                        continue
                    log.warning("Nachricht konnte nicht gesendet werden: %s", exc)
                    ok = False
        return ok

    def get_updates(self, offset: Optional[int], timeout: int) -> List[dict]:
        payload = {"timeout": timeout, "allowed_updates": ["message"]}
        if offset is not None:
            payload["offset"] = offset
        data = self._call("getUpdates", payload, timeout=timeout + 10)
        return data.get("result") or []

    def set_commands(self, commands: List[tuple]) -> None:
        self._call(
            "setMyCommands",
            {"commands": [{"command": c, "description": d} for c, d in commands]},
        )


def _split(text: str) -> List[str]:
    if len(text) <= MAX_MESSAGE_LEN:
        return [text]
    chunks, current = [], ""
    for block in text.split("\n\n"):
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) > MAX_MESSAGE_LEN and current:
            chunks.append(current)
            current = block
        else:
            current = candidate
    if current:
        chunks.append(current)
    return [c[:MAX_MESSAGE_LEN] for c in chunks]
