"""Parsen von SL/TP-Angaben und Zahlenformatierung."""
from __future__ import annotations

import html
from typing import Optional


class LevelError(ValueError):
    pass


def parse_number(text: str) -> float:
    """Akzeptiert deutsche und englische Schreibweise: 8,31 / 8.31 / 1.234,50."""
    raw = text.strip().replace("€", "").replace(" ", "")
    if "," in raw and "." in raw:
        raw = raw.replace(".", "").replace(",", ".")
    else:
        raw = raw.replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        raise LevelError(f"{text!r} ist keine gültige Zahl")


def parse_level(text: str, entry: float, kind: str) -> float:
    """Wandelt eine SL- oder TP-Angabe in einen absoluten Kurs um.

    - Prozent (`-15%`, `15%`, `+40%`) bezieht sich auf den Einstiegskurs. Beim SL
      zählt nur der Betrag (15% und -15% bedeuten beide 15 % unter Einstieg),
      beim TP ebenso (40% und +40% = 40 % über Einstieg).
    - Ohne %-Zeichen ist es ein absoluter Kurs in Euro.
    """
    if kind not in ("sl", "tp"):
        raise ValueError(kind)
    text = text.strip()
    if text.endswith("%"):
        pct = abs(parse_number(text[:-1]))
        if kind == "sl":
            if pct >= 100:
                raise LevelError("Ein Stop-Loss von 100 % oder mehr ergibt keinen Sinn")
            return entry * (1 - pct / 100)
        return entry * (1 + pct / 100)
    value = parse_number(text)
    if value <= 0:
        raise LevelError("Kurse müssen größer als 0 sein")
    return value


def validate_levels(entry: float, sl: float, tp: float) -> None:
    if entry <= 0:
        raise LevelError("Der Einstiegskurs muss größer als 0 sein")
    if sl >= tp:
        raise LevelError(f"Stop-Loss ({fmt_eur(sl)}) muss unter Take-Profit ({fmt_eur(tp)}) liegen")


def pct_change(price: float, reference: float) -> float:
    return (price / reference - 1) * 100


def fmt_eur(value: Optional[float]) -> str:
    if value is None:
        return "–"
    decimals = 3 if abs(value) < 1 else 2
    text = f"{value:,.{decimals}f}"
    return text.replace(",", "X").replace(".", ",").replace("X", ".") + " €"


def fmt_num(value: Optional[float], decimals: int = 2) -> str:
    if value is None:
        return "–"
    text = f"{value:,.{decimals}f}"
    return text.replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_pct(value: Optional[float], signed: bool = True) -> str:
    if value is None:
        return "–"
    sign = "+" if signed and value > 0 else ""
    return f"{sign}{fmt_num(value)} %"


def esc(text: str) -> str:
    return html.escape(text or "", quote=False)
