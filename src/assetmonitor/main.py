"""Einstiegspunkt: `python -m assetmonitor.main [--selftest [ISIN]]`."""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from typing import Optional

from .commands import COMMANDS, CommandHandler, short_name
from .config import ConfigError, Settings, load_settings
from .levels import fmt_eur, fmt_pct
from .monitor import Monitor
from .notify import Notifier
from .onvista import OnvistaClient, QuoteError
from .store import Store, utcnow
from .telegram import TelegramClient, TelegramError

log = logging.getLogger("assetmonitor")

OFFSET_KEY = "telegram_offset"
MAX_LONG_POLL = 25


class Bot:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = Store(settings.db_path)
        self.onvista = OnvistaClient()
        self.telegram = TelegramClient(settings.telegram_token, settings.telegram_chat_id)
        self.notifier = Notifier(self.telegram, self.store)
        self.monitor = Monitor(self.store, self.onvista, self.notifier, settings)
        self.commands = CommandHandler(self.store, self.onvista, settings, self.monitor.status_text)
        self.running = True
        # Zählt aufeinanderfolgende getUpdates-Fehler, damit ein Netzausfall
        # nicht alle 10 s eine gleiche Logzeile schreibt
        self.poll_failures = 0

    def stop(self, *_args) -> None:
        log.info("Stop-Signal empfangen, beende nach dem aktuellen Schritt")
        self.running = False

    def handle_update(self, update: dict) -> None:
        msg = update.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id")
        text = msg.get("text")
        if not text:
            return
        if chat_id != self.settings.telegram_chat_id:
            # Fremde Chats ignorieren: nur der eigene Chat darf Positionen verwalten
            log.warning("Nachricht aus fremdem Chat %s ignoriert", chat_id)
            return
        log.info("Befehl: %s", text.split()[0] if text.split() else text)
        try:
            reply = self.commands.handle(text)
        except Exception:  # noqa: BLE001
            log.exception("Fehler bei Befehl %r", text)
            reply = "❌ Interner Fehler bei der Verarbeitung. Details im Log."
        if reply:
            self.telegram.send(reply)

    def run(self) -> None:
        try:
            self.telegram.set_commands(COMMANDS)
        except TelegramError as exc:
            log.warning("Befehlsmenü konnte nicht gesetzt werden: %s", exc)

        positions = self.store.all()
        log.info("Bot gestartet, %d Position(en) in %s", len(positions), self.settings.db_path)
        self.notifier(f"🤖 Bot gestartet. {len(positions)} Position(en) werden überwacht. Hilfe: /help")

        raw_offset = self.store.get_meta(OFFSET_KEY)
        offset: Optional[int] = int(raw_offset) if raw_offset else None
        next_check = 0.0

        while self.running:
            if time.monotonic() >= next_check:
                try:
                    self.notifier.flush()
                    self.monitor.run_cycle()
                    self.monitor.maybe_heartbeat()
                except Exception:  # noqa: BLE001 - der Loop darf nie sterben
                    log.exception("Fehler im Prüflauf")
                next_check = time.monotonic() + self.monitor.poll_interval(utcnow())

            wait = int(max(1, min(MAX_LONG_POLL, next_check - time.monotonic())))
            try:
                updates = self.telegram.get_updates(offset, timeout=wait)
            except TelegramError as exc:
                self.poll_failures += 1
                if self.poll_failures == 1 or self.poll_failures % 30 == 0:
                    log.warning(
                        "getUpdates fehlgeschlagen (%d. Versuch in Folge): %s", self.poll_failures, exc
                    )
                time.sleep(5)
                continue
            if self.poll_failures:
                log.info("Telegram wieder erreichbar nach %d Fehlversuchen", self.poll_failures)
                self.poll_failures = 0
            for update in updates:
                offset = update["update_id"] + 1
                self.store.set_meta(OFFSET_KEY, str(offset))
                self.handle_update(update)

        self.store.close()
        log.info("Bot beendet")


def selftest(settings: Settings, isin: str) -> int:
    """Prüft Konfiguration, onvista-Zugriff und Telegram-Versand einmalig."""
    ok = True
    client = OnvistaClient()
    try:
        inst = client.resolve(isin)
        q = client.quote(inst.entity_id)
        print(f"onvista OK: {q.wkn} {short_name(q)} Geld {fmt_eur(q.bid)} KO-Abstand {fmt_pct(q.ko_distance_pct, signed=False)}")
    except QuoteError as exc:
        print(f"onvista FEHLER: {exc}")
        ok = False

    tg = TelegramClient(settings.telegram_token, settings.telegram_chat_id)
    if tg.send("🧪 Selbsttest von asset-monitor: Telegram-Versand funktioniert."):
        print("Telegram OK: Testnachricht gesendet")
    else:
        print("Telegram FEHLER: Nachricht konnte nicht gesendet werden (Token/Chat-ID prüfen)")
        ok = False
    return 0 if ok else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="SL/TP-Wächter für Hebelprodukte mit Telegram-Alarmen")
    parser.add_argument(
        "--selftest",
        nargs="?",
        const="DE000FC7CVG4",
        metavar="ISIN",
        help="onvista- und Telegram-Zugriff einmal testen und beenden",
    )
    parser.add_argument("--env", default=None, help="Pfad zur .env-Datei")
    args = parser.parse_args(argv)

    try:
        settings = load_settings(args.env)
    except ConfigError as exc:
        print(f"Konfigurationsfehler: {exc}", file=sys.stderr)
        return 2

    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # requests/urllib3 loggen sonst URLs inklusive Bot-Token
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    if args.selftest:
        return selftest(settings, args.selftest)

    bot = Bot(settings)
    signal.signal(signal.SIGTERM, bot.stop)
    signal.signal(signal.SIGINT, bot.stop)
    bot.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
