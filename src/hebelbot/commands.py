"""Verarbeitung der Telegram-Befehle."""
from __future__ import annotations

import logging
from typing import Callable, List, Optional, Tuple

from .config import Settings
from .levels import LevelError, esc, fmt_eur, fmt_num, fmt_pct, parse_level, parse_number, pct_change, validate_levels
from .onvista import OnvistaClient, Quote, QuoteError
from .store import ACTIVE, HIT_SL, HIT_TP, KNOCKED_OUT, Position, Store, utcnow

log = logging.getLogger(__name__)

# Für das Telegram-Menü (setMyCommands)
COMMANDS = [
    ("add", "Position überwachen: /add ISIN [SL] [TP] [Einstieg]"),
    ("list", "Alle Positionen mit aktuellem Kurs"),
    ("sl", "Stop-Loss ändern: /sl ISIN WERT"),
    ("tp", "Take-Profit ändern: /tp ISIN WERT"),
    ("remove", "Überwachung beenden: /remove ISIN"),
    ("kurs", "Kurs abfragen ohne zu speichern: /kurs ISIN"),
    ("status", "Zustand des Bots"),
    ("help", "Hilfe"),
]

STATUS_TEXT = {
    ACTIVE: "aktiv",
    HIT_SL: "🛑 SL ausgelöst",
    HIT_TP: "🎯 TP ausgelöst",
    KNOCKED_OUT: "💥 ausgeknockt",
}


def short_name(q: Quote) -> str:
    if q.underlying_name:
        return f"{q.underlying_name} {q.direction}"
    return q.name


def quote_lines(q: Quote) -> List[str]:
    lines = [f"Geld/Brief: {fmt_eur(q.bid)} / {fmt_eur(q.ask)}"]
    if q.underlying_name:
        lines.append(f"Basiswert {esc(q.underlying_name)}: {fmt_num(q.underlying_price)}")
    if q.ko_barrier is not None:
        lines.append(f"KO-Schwelle: {fmt_num(q.ko_barrier)} (Abstand {fmt_pct(q.ko_distance_pct, signed=False)})")
    return lines


def help_text(settings: Settings) -> str:
    return (
        "<b>SL/TP-Wächter für Hebelprodukte</b>\n\n"
        "<b>/add ISIN [SL] [TP] [Einstieg]</b>\n"
        f"Ohne Angaben: SL -{fmt_num(settings.default_sl_pct, 0)} %, TP +{fmt_num(settings.default_tp_pct, 0)} %, "
        "Einstieg = aktueller Briefkurs.\n"
        "Werte mit % beziehen sich auf den Einstieg, ohne % sind es Kurse in Euro.\n"
        "Beispiele:\n"
        "<code>/add DE000FC7CVG4</code>\n"
        "<code>/add DE000FC7CVG4 -15% +40%</code>\n"
        "<code>/add FC7CVG 6,90 11,00 8,31</code>\n"
        "<code>/add FC7CVG tp=50% einstieg=8,31</code>\n\n"
        "<b>/sl ISIN WERT</b>, <b>/tp ISIN WERT</b>: Level ändern (setzt einen ausgelösten Alarm zurück)\n"
        "<b>/remove ISIN</b>: Überwachung beenden, z. B. nach dem Verkauf\n"
        "<b>/list</b>: alle Positionen mit Live-Kurs\n"
        "<b>/kurs ISIN</b>: Kurs und KO-Abstand abfragen\n"
        "<b>/status</b>: läuft die Kursabfrage?\n\n"
        "Statt ISIN geht auch die WKN. Ausgelöst wird auf den Geldkurs "
        "(den Kurs, zu dem du verkaufen kannst)."
    )


class CommandHandler:
    def __init__(
        self,
        store: Store,
        client: OnvistaClient,
        settings: Settings,
        status_provider: Optional[Callable[[], str]] = None,
    ):
        self.store = store
        self.client = client
        self.settings = settings
        self.status_provider = status_provider

    def handle(self, text: str) -> Optional[str]:
        text = (text or "").strip()
        if not text.startswith("/"):
            return "Befehle beginnen mit /. Hilfe: /help"
        parts = text.split()
        cmd = parts[0][1:].split("@", 1)[0].lower()
        args = parts[1:]
        handlers = {
            "start": self._help,
            "help": self._help,
            "hilfe": self._help,
            "add": self._add,
            "list": self._list,
            "liste": self._list,
            "sl": lambda a: self._set_level(a, "sl"),
            "tp": lambda a: self._set_level(a, "tp"),
            "remove": self._remove,
            "del": self._remove,
            "entfernen": self._remove,
            "kurs": self._kurs,
            "status": self._status,
        }
        fn = handlers.get(cmd)
        if fn is None:
            return f"Unbekannter Befehl /{esc(cmd)}. Hilfe: /help"
        try:
            return fn(args)
        except LevelError as exc:
            return f"❌ {esc(str(exc))}"
        except QuoteError as exc:
            return f"❌ {esc(str(exc))}"

    # ---- einzelne Befehle ----

    def _help(self, args: List[str]) -> str:
        return help_text(self.settings)

    def _add(self, args: List[str]) -> str:
        if not args:
            return "Bitte ISIN oder WKN angeben, z. B. <code>/add DE000FC7CVG4 -15% +40%</code>"
        ident, rest = args[0], args[1:]
        existing = self.store.find(ident)
        if existing:
            return (
                f"{esc(existing.label)} wird schon überwacht. Level ändern mit /sl bzw. /tp, "
                f"oder erst <code>/remove {esc(existing.label)}</code>."
            )

        sl_raw, tp_raw, entry_raw = _split_add_args(rest)

        inst = self.client.resolve(ident)
        if self.store.find(inst.isin):
            return f"{esc(inst.isin)} wird schon überwacht."
        q = self.client.quote(inst.entity_id)
        if q.barrier_hit:
            return f"❌ {esc(inst.wkn or inst.isin)} ist bereits ausgeknockt."

        if entry_raw is not None:
            entry = parse_number(entry_raw)
        else:
            entry = q.ask if q.ask else q.bid
        sl = parse_level(sl_raw, entry, "sl") if sl_raw else entry * (1 - self.settings.default_sl_pct / 100)
        tp = parse_level(tp_raw, entry, "tp") if tp_raw else entry * (1 + self.settings.default_tp_pct / 100)
        validate_levels(entry, sl, tp)

        now = utcnow()
        pos = Position(
            isin=inst.isin,
            wkn=inst.wkn,
            name=short_name(q),
            entity_id=inst.entity_id,
            url=inst.url or q.url,
            entry=entry,
            sl=sl,
            tp=tp,
            status=ACTIVE,
            created_at=now,
            last_bid=q.bid,
            last_quote_at=q.quote_time,
        )
        self.store.save(pos)
        log.info("Position angelegt: %s Einstieg=%.4f SL=%.4f TP=%.4f", pos.isin, entry, sl, tp)

        lines = [
            f"✅ <b>Überwache {esc(pos.label)}</b> · {esc(pos.name)}",
            f"<code>{esc(pos.isin)}</code> · {esc(q.issuer)}",
            f"Einstieg: {fmt_eur(entry)}" + ("" if entry_raw else " (aktueller Briefkurs)"),
            f"SL: {fmt_eur(sl)} ({fmt_pct(pct_change(sl, entry))})",
            f"TP: {fmt_eur(tp)} ({fmt_pct(pct_change(tp, entry))})",
            *quote_lines(q),
        ]
        if q.bid <= sl:
            lines.append("⚠️ Der Geldkurs liegt schon auf/unter dem SL, der Alarm kommt beim nächsten Durchlauf.")
        elif q.bid >= tp:
            lines.append("⚠️ Der Geldkurs liegt schon auf/über dem TP, der Alarm kommt beim nächsten Durchlauf.")
        return "\n".join(lines)

    def _list(self, args: List[str]) -> str:
        positions = self.store.all()
        if not positions:
            return "Keine Positionen. Hinzufügen mit <code>/add ISIN</code>."
        blocks = []
        for p in positions:
            try:
                q = self.client.quote(p.entity_id)
                bid, ko = q.bid, q.ko_distance_pct
                ko_text = f" · KO-Abstand {fmt_pct(ko, signed=False)}" if ko is not None else ""
            except QuoteError as exc:
                log.warning("Kurs für /list nicht abrufbar (%s): %s", p.isin, exc)
                bid, ko_text = p.last_bid, " · ⚠️ Kurs nicht abrufbar, letzter bekannter"
            perf = fmt_pct(pct_change(bid, p.entry)) if bid else "–"
            blocks.append(
                "\n".join([
                    f"<b>{esc(p.label)}</b> · {esc(p.name)} · {STATUS_TEXT.get(p.status, p.status)}",
                    f"Geld {fmt_eur(bid)} ({perf}) · Einstieg {fmt_eur(p.entry)}{ko_text}",
                    f"SL {fmt_eur(p.sl)} ({fmt_pct(pct_change(p.sl, p.entry))}) · "
                    f"TP {fmt_eur(p.tp)} ({fmt_pct(pct_change(p.tp, p.entry))})",
                ])
            )
        return "\n\n".join(blocks)

    def _set_level(self, args: List[str], kind: str) -> str:
        if len(args) != 2:
            return f"Aufruf: <code>/{kind} ISIN WERT</code>, z. B. <code>/{kind} FC7CVG {'-10%' if kind == 'sl' else '+50%'}</code>"
        pos = self.store.find(args[0])
        if not pos:
            return f"{esc(args[0])} wird nicht überwacht. Übersicht: /list"
        value = parse_level(args[1], pos.entry, kind)
        sl = value if kind == "sl" else pos.sl
        tp = value if kind == "tp" else pos.tp
        validate_levels(pos.entry, sl, tp)
        pos.sl, pos.tp = sl, tp
        reset = pos.status in (HIT_SL, HIT_TP)
        if reset:
            pos.status = ACTIVE
            pos.triggered_at = None
            pos.last_alert_at = None
        self.store.save(pos)
        label = "SL" if kind == "sl" else "TP"
        msg = f"✅ {esc(pos.label)}: {label} jetzt {fmt_eur(value)} ({fmt_pct(pct_change(value, pos.entry))} zum Einstieg)"
        if reset:
            msg += "\nAlarm zurückgesetzt, Position wird wieder überwacht."
        if pos.status == KNOCKED_OUT:
            msg += "\n⚠️ Das Produkt ist ausgeknockt. Entfernen mit /remove."
        return msg

    def _remove(self, args: List[str]) -> str:
        if len(args) != 1:
            return "Aufruf: <code>/remove ISIN</code>"
        pos = self.store.find(args[0])
        if not pos:
            return f"{esc(args[0])} wird nicht überwacht. Übersicht: /list"
        self.store.delete(pos.isin)
        log.info("Position entfernt: %s", pos.isin)
        return f"🗑 {esc(pos.label)} ({esc(pos.name)}) wird nicht mehr überwacht."

    def _kurs(self, args: List[str]) -> str:
        if len(args) != 1:
            return "Aufruf: <code>/kurs ISIN</code>"
        pos = self.store.find(args[0])
        if pos:
            q = self.client.quote(pos.entity_id)
        else:
            inst = self.client.resolve(args[0])
            q = self.client.quote(inst.entity_id)
        lines = [
            f"<b>{esc(q.wkn or q.isin)}</b> · {esc(short_name(q))} · {esc(q.issuer)}",
            f"<code>{esc(q.isin)}</code>",
            *quote_lines(q),
        ]
        if q.quote_time:
            lines.append(f"Stand: {q.quote_time.astimezone(_tz(self.settings)).strftime('%d.%m. %H:%M:%S')} ({esc(q.market)})")
        if q.barrier_hit:
            lines.append("💥 Ausgeknockt")
        return "\n".join(lines)

    def _status(self, args: List[str]) -> str:
        if self.status_provider:
            return self.status_provider()
        return "Status nicht verfügbar."


def _tz(settings: Settings):
    from zoneinfo import ZoneInfo

    return ZoneInfo(settings.market_tz)


def _split_add_args(rest: List[str]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Positionsargumente (SL TP Einstieg) oder key=value (sl=, tp=, einstieg=/entry=)."""
    named = {}
    positional = []
    for arg in rest:
        if "=" in arg:
            key, value = arg.split("=", 1)
            key = key.lower()
            if key in ("einstieg", "entry", "ek"):
                key = "entry"
            if key not in ("sl", "tp", "entry"):
                raise LevelError(f"Unbekannter Parameter {key!r}. Erlaubt: sl=, tp=, einstieg=")
            named[key] = value
        else:
            positional.append(arg)
    if len(positional) > 3:
        raise LevelError("Zu viele Angaben. Aufruf: /add ISIN [SL] [TP] [Einstieg]")
    keys = ["sl", "tp", "entry"]
    for key, value in zip(keys, positional):
        if key in named:
            raise LevelError(f"{key} wurde doppelt angegeben")
        named[key] = value
    # "-" als Platzhalter: /add ISIN - +50% übernimmt den Default-SL
    values = [None if named.get(k) in (None, "-", "") else named[k] for k in keys]
    return values[0], values[1], values[2]
