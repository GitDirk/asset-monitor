"""Prüft alle Positionen gegen SL/TP/KO und verschickt Alarme."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

from .config import Settings
from .levels import esc, fmt_eur, fmt_num, fmt_pct, pct_change
from .onvista import OnvistaClient, Quote, QuoteError
from .store import ACTIVE, HIT_SL, HIT_TP, KNOCKED_OUT, Position, Store, utcnow

log = logging.getLogger(__name__)

# Ab so vielen Fehlversuchen in Folge gibt es einen Telegram-Alarm
FAILURE_ALERT_THRESHOLD = 5


class Monitor:
    def __init__(
        self,
        store: Store,
        client: OnvistaClient,
        notify: Callable[[str], bool],
        settings: Settings,
    ):
        self.store = store
        self.client = client
        self.notify = notify
        self.settings = settings
        self.tz = ZoneInfo(settings.market_tz)
        self.failures: Dict[str, int] = {}
        self.failure_alerted: set = set()
        self.stale_alerted: set = set()
        self.last_cycle_at: Optional[datetime] = None
        self.last_error: Optional[str] = None
        self.started_at = utcnow()

    # ---- Handelszeiten ----

    def is_market_open(self, now: datetime) -> bool:
        local = now.astimezone(self.tz)
        if local.weekday() >= 5:
            return False
        return self.settings.market_open <= local.time() < self.settings.market_close

    def poll_interval(self, now: datetime) -> float:
        return self.settings.poll_seconds if self.is_market_open(now) else self.settings.poll_seconds_closed

    # ---- Hauptzyklus ----

    def run_cycle(self, now: Optional[datetime] = None) -> None:
        now = now or utcnow()
        failed_now: List[Position] = []
        recovered: List[Position] = []

        positions = self.store.all()
        for pos in positions:
            try:
                quote = self.client.quote(pos.entity_id)
            except QuoteError as exc:
                log.warning("Kursabfrage fehlgeschlagen (%s): %s", pos.isin, exc)
                self.last_error = str(exc)
                count = self.failures.get(pos.isin, 0) + 1
                self.failures[pos.isin] = count
                if count >= FAILURE_ALERT_THRESHOLD and pos.isin not in self.failure_alerted:
                    self.failure_alerted.add(pos.isin)
                    failed_now.append(pos)
                continue

            self.failures.pop(pos.isin, None)
            if pos.isin in self.failure_alerted:
                self.failure_alerted.discard(pos.isin)
                recovered.append(pos)
            try:
                self.check_position(pos, quote, now)
            except Exception:  # noqa: BLE001 - eine kaputte Position darf den Rest nicht blockieren
                log.exception("Fehler bei der Prüfung von %s", pos.isin)

        # Positionen, die inzwischen entfernt wurden, aus dem Speicher werfen
        known = {p.isin for p in positions}
        for isin in list(self.failures):
            if isin not in known:
                self.failures.pop(isin)
        self.failure_alerted &= known
        self.stale_alerted &= known

        if failed_now:
            labels = ", ".join(esc(p.label) for p in failed_now)
            self.notify(
                f"🔌 <b>Kursabfrage gestört</b> für {labels}\n"
                f"{FAILURE_ALERT_THRESHOLD} Versuche in Folge fehlgeschlagen. "
                f"SL/TP werden gerade <b>nicht</b> überwacht.\n"
                f"Letzter Fehler: {esc(self.last_error or '')}"
            )
        if recovered:
            labels = ", ".join(esc(p.label) for p in recovered)
            self.notify(f"✅ Kursabfrage läuft wieder für {labels}")

        self.last_cycle_at = now

    def check_position(self, pos: Position, q: Quote, now: datetime) -> None:
        pos.last_bid = q.bid
        pos.last_quote_at = q.quote_time

        if pos.status == KNOCKED_OUT:
            self.store.save(pos)
            return

        if q.barrier_hit:
            pos.status = KNOCKED_OUT
            pos.triggered_at = now
            pos.last_alert_at = now
            self.store.save(pos)
            log.warning("Knock-out: %s", pos.isin)
            self.notify(self._ko_text(pos, q))
            return

        if pos.status == ACTIVE:
            if q.bid <= pos.sl:
                self._trigger(pos, q, now, HIT_SL)
                return
            if q.bid >= pos.tp:
                self._trigger(pos, q, now, HIT_TP)
                return
        elif pos.status in (HIT_SL, HIT_TP):
            reminder = self.settings.reminder_minutes
            if reminder > 0 and (pos.last_alert_at is None or now - pos.last_alert_at >= timedelta(minutes=reminder)):
                pos.last_alert_at = now
                self.store.save(pos)
                self.notify(self._trigger_text(pos, q, pos.status, reminder=True))
                return

        self._check_ko_distance(pos, q, now)
        self._check_stale(pos, q, now)
        self.store.save(pos)

    # ---- Einzelprüfungen ----

    def _trigger(self, pos: Position, q: Quote, now: datetime, status: str) -> None:
        pos.status = status
        pos.triggered_at = now
        pos.last_alert_at = now
        self.store.save(pos)
        log.info("%s ausgelöst: %s Geld=%.4f", status.upper(), pos.isin, q.bid)
        self.notify(self._trigger_text(pos, q, status, reminder=False))

    def _check_ko_distance(self, pos: Position, q: Quote, now: datetime) -> None:
        warn = self.settings.ko_warn_pct
        if warn <= 0 or q.ko_distance_pct is None or q.ko_distance_pct > warn:
            return
        interval = timedelta(minutes=max(self.settings.reminder_minutes, 1))
        if pos.ko_warned_at and now - pos.ko_warned_at < interval:
            return
        pos.ko_warned_at = now
        self.notify(
            f"⚠️ <b>KO-Schwelle nah</b>: {esc(pos.label)} · {esc(pos.name)}\n"
            f"Abstand nur noch {fmt_pct(q.ko_distance_pct, signed=False)} "
            f"(Basiswert {fmt_num(q.underlying_price)}, KO {fmt_num(q.ko_barrier)})\n"
            f"Geldkurs {fmt_eur(q.bid)} ({fmt_pct(pct_change(q.bid, pos.entry))}) · SL {fmt_eur(pos.sl)}\n"
            f"<code>{esc(pos.isin)}</code>"
        )

    def _check_stale(self, pos: Position, q: Quote, now: datetime) -> None:
        if self.settings.stale_minutes <= 0 or q.quote_time is None:
            return
        age = now - q.quote_time
        stale = age > timedelta(minutes=self.settings.stale_minutes)
        # Kurz nach Handelsbeginn ist der letzte Kurs naturgemäß vom Vortag
        market_start = now - timedelta(minutes=self.settings.stale_minutes)
        if stale and self.is_market_open(now) and self.is_market_open(market_start):
            if pos.isin not in self.stale_alerted:
                self.stale_alerted.add(pos.isin)
                local = q.quote_time.astimezone(self.tz).strftime("%d.%m. %H:%M")
                self.notify(
                    f"⏸ <b>Kurs veraltet</b>: {esc(pos.label)} · letzter Kurs von {local} "
                    f"({esc(q.market)}). Der Emittent stellt eventuell gerade keine Kurse."
                )
        elif not stale and pos.isin in self.stale_alerted:
            self.stale_alerted.discard(pos.isin)
            self.notify(f"▶️ {esc(pos.label)}: Kurse kommen wieder.")

    # ---- Texte ----

    def _trigger_text(self, pos: Position, q: Quote, status: str, reminder: bool) -> str:
        if status == HIT_SL:
            head = "🛑 <b>STOP-LOSS erreicht</b>"
            level = f"SL {fmt_eur(pos.sl)}"
        else:
            head = "🎯 <b>TAKE-PROFIT erreicht</b>"
            level = f"TP {fmt_eur(pos.tp)}"
        if reminder:
            head = "🔁 Erinnerung: " + head
        lines = [
            f"{head}: {esc(pos.label)} · {esc(pos.name)}",
            f"<code>{esc(pos.isin)}</code>",
            f"Geldkurs {fmt_eur(q.bid)} ({level})",
            f"Ergebnis: {fmt_pct(pct_change(q.bid, pos.entry))} zum Einstieg {fmt_eur(pos.entry)}",
        ]
        if q.ko_barrier is not None:
            lines.append(
                f"Basiswert {esc(q.underlying_name)} {fmt_num(q.underlying_price)} · "
                f"KO {fmt_num(q.ko_barrier)} (Abstand {fmt_pct(q.ko_distance_pct, signed=False)})"
            )
        lines.append("")
        lines.append("👉 Position in Trade Republic schließen.")
        if self.settings.reminder_minutes > 0:
            lines.append(
                f"Danach <code>/remove {esc(pos.label)}</code>. Bis dahin erinnere ich alle "
                f"{fmt_num(self.settings.reminder_minutes, 0)} min (oder neue Level mit /sl bzw. /tp)."
            )
        else:
            lines.append(f"Danach <code>/remove {esc(pos.label)}</code>.")
        return "\n".join(lines)

    def _ko_text(self, pos: Position, q: Quote) -> str:
        return (
            f"💥 <b>KNOCK-OUT</b>: {esc(pos.label)} · {esc(pos.name)}\n"
            f"<code>{esc(pos.isin)}</code>\n"
            f"Die KO-Schwelle {fmt_num(q.ko_barrier)} wurde erreicht. Das Produkt ist verfallen "
            f"(Geldkurs {fmt_eur(q.bid)}).\n"
            f"Entfernen mit <code>/remove {esc(pos.label)}</code>."
        )

    # ---- Heartbeat & Status ----

    def maybe_heartbeat(self, now: Optional[datetime] = None) -> bool:
        hb = self.settings.heartbeat_time
        if hb is None:
            return False
        now = now or utcnow()
        local = now.astimezone(self.tz)
        if local.weekday() >= 5 or local.time() < hb:
            return False
        today = local.date().isoformat()
        if self.store.get_meta("heartbeat_date") == today:
            return False
        self.store.set_meta("heartbeat_date", today)
        positions = self.store.all()
        active = sum(1 for p in positions if p.status == ACTIVE)
        triggered = [p for p in positions if p.status != ACTIVE]
        text = f"☀️ Bot läuft. {active} Position(en) aktiv überwacht."
        if triggered:
            text += "\nNoch offen nach Alarm: " + ", ".join(
                f"{esc(p.label)} ({p.status.upper()})" for p in triggered
            )
        text += "\nDetails: /list"
        self.notify(text)
        return True

    def status_text(self) -> str:
        now = utcnow()
        positions = self.store.all()
        lines = [
            "<b>Bot-Status</b>",
            f"Läuft seit: {self.started_at.astimezone(self.tz).strftime('%d.%m. %H:%M')}",
            f"Letzter Prüflauf: "
            + (self.last_cycle_at.astimezone(self.tz).strftime("%H:%M:%S") if self.last_cycle_at else "noch keiner"),
            f"Handelszeit: {'ja' if self.is_market_open(now) else 'nein'} "
            f"(Abfrage alle {fmt_num(self.poll_interval(now), 0)} s)",
            f"Positionen: {len(positions)}",
        ]
        if self.failures:
            lines.append("Fehlerhafte Abfragen: " + ", ".join(f"{k} ({v}×)" for k, v in self.failures.items()))
            lines.append(f"Letzter Fehler: {esc(self.last_error or '')}")
        return "\n".join(lines)

