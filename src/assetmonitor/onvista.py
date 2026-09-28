"""Kursabfrage über die (inoffizielle) JSON-API von onvista.de.

Die Endpunkte sind nicht dokumentiert und können sich ändern. Alle Zugriffe
laufen deshalb über diese eine Datei, und Parse-Fehler werden als
`QuoteError` gemeldet statt still falsche Werte zu liefern.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

import requests

log = logging.getLogger(__name__)

BASE_URL = "https://api.onvista.de/api/v1"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128 Safari/537.36"


class QuoteError(Exception):
    """Kurs konnte nicht abgerufen oder gelesen werden."""


@dataclass(frozen=True)
class Instrument:
    isin: str
    wkn: str
    name: str
    entity_id: str
    url: str


@dataclass(frozen=True)
class Quote:
    isin: str
    wkn: str
    name: str
    entity_id: str
    url: str
    issuer: str
    direction: str  # "Long" oder "Short"
    currency: str
    bid: float
    ask: Optional[float]
    quote_time: Optional[datetime]
    market: str
    underlying_name: str
    underlying_price: Optional[float]
    ko_barrier: Optional[float]
    ratio: Optional[float]
    ko_distance_pct: Optional[float]
    leverage: Optional[float]
    barrier_hit: bool


def _parse_ts(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        # Format: 2026-09-17T16:56:53.000+00:00
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _num(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# Pause nach HTTP 429, falls onvista keinen Retry-After-Header schickt
DEFAULT_BACKOFF_SECONDS = 120.0


class OnvistaClient:
    def __init__(
        self,
        session: Optional[requests.Session] = None,
        timeout: float = 15.0,
        min_interval: float = 1.0,
    ):
        self.session = session or requests.Session()
        # Browser-User-Agent ist nötig: den Standard-UA "python-requests/..." blockt onvista mit HTTP 429
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
        self.timeout = timeout
        # onvista drosselt schnelle Anfragefolgen mit HTTP 429 -> Abstand halten
        self.min_interval = min_interval
        self._last_request = 0.0
        self._blocked_until = 0.0

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        now = time.monotonic()
        if now < self._blocked_until:
            raise QuoteError(
                f"onvista drosselt Anfragen, nächster Versuch in {self._blocked_until - now:.0f} s"
            )
        wait = self._last_request + self.min_interval - now
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

        url = f"{BASE_URL}{path}"
        try:
            resp = self.session.get(url, params=params, timeout=self.timeout)
        except requests.RequestException as exc:
            raise QuoteError(f"onvista nicht erreichbar: {exc}") from exc
        if resp.status_code == 429:
            try:
                backoff = float(resp.headers.get("Retry-After", DEFAULT_BACKOFF_SECONDS))
            except (TypeError, ValueError):
                backoff = DEFAULT_BACKOFF_SECONDS
            self._blocked_until = time.monotonic() + backoff
            log.warning("onvista meldet HTTP 429, pausiere %.0f s", backoff)
            raise QuoteError(f"onvista drosselt Anfragen (HTTP 429), Pause {backoff:.0f} s")
        if resp.status_code != 200:
            raise QuoteError(f"onvista antwortet mit HTTP {resp.status_code} für {path}")
        try:
            return resp.json()
        except ValueError as exc:
            raise QuoteError(f"onvista lieferte kein JSON für {path}") from exc

    def resolve(self, isin_or_wkn: str) -> Instrument:
        """Sucht ein Derivat per ISIN oder WKN."""
        needle = isin_or_wkn.strip().upper()
        data = self._get("/instruments/query", {"searchValue": needle})
        items = data.get("list") or []
        exact = [
            it for it in items
            if needle in (str(it.get("isin", "")).upper(), str(it.get("wkn", "")).upper())
        ]
        if not exact:
            raise QuoteError(f"{needle} wurde bei onvista nicht gefunden")
        derivatives = [it for it in exact if it.get("entityType") == "DERIVATIVE"]
        if not derivatives:
            kind = exact[0].get("displayType") or exact[0].get("entityType")
            raise QuoteError(f"{needle} ist kein Hebelprodukt/Derivat (onvista: {kind})")
        it = derivatives[0]
        return Instrument(
            isin=it["isin"],
            wkn=str(it.get("wkn", "")),
            name=it.get("name", ""),
            entity_id=str(it["entityValue"]),
            url=(it.get("urls") or {}).get("WEBSITE", ""),
        )

    def quote(self, entity_id: str) -> Quote:
        data = self._get(f"/derivatives/{entity_id}/snapshot")
        return parse_snapshot(data)


def parse_snapshot(data: dict) -> Quote:
    inst = data.get("instrument") or {}
    quote = data.get("quote") or {}
    details = data.get("derivativesDetails") or {}
    figure = data.get("derivativesFigure") or {}
    underlyings = (data.get("derivativesUnderlyingList") or {}).get("list") or []
    underlying = underlyings[0] if underlyings else {}

    bid = _num(quote.get("bid"))
    if bid is None:
        bid = _num((data.get("bid") or {}).get("last"))
    if bid is None:
        raise QuoteError(f"Kein Geldkurs für {inst.get('isin', '?')} in der onvista-Antwort")

    ask = _num(quote.get("ask"))
    if ask is None:
        ask = _num((data.get("ask") or {}).get("last"))

    ko_barrier = None
    barrier_hit = bool(details.get("hasBarrierBeenHit"))
    for barrier in (underlying.get("derivativesBarrierList") or {}).get("list") or []:
        if barrier.get("typeBarrier") == "KNOCK_OUT":
            ko_barrier = _num(barrier.get("barrier"))
            barrier_hit = barrier_hit or bool(barrier.get("hasBeenHit"))

    underlying_price = _num(figure.get("priceUnderlying"))
    ko_distance = _num(figure.get("differenceKnockoutPct"))
    if ko_distance is None and ko_barrier and underlying_price:
        ko_distance = abs(underlying_price - ko_barrier) / underlying_price * 100

    right = details.get("codeExerciseRight") or (data.get("keywords") or {}).get("idExerciseRight")
    direction = "Short" if right == "P" else "Long"

    quote_time = _parse_ts(quote.get("datetimeBid")) or _parse_ts(quote.get("datetimeLast"))

    return Quote(
        isin=inst.get("isin", ""),
        wkn=str(inst.get("wkn", "")),
        name=inst.get("name", ""),
        entity_id=str(inst.get("entityValue", "")),
        url=(inst.get("urls") or {}).get("WEBSITE", ""),
        issuer=(data.get("derivativesIssuer") or {}).get("name", ""),
        direction=direction,
        currency=quote.get("isoCurrency") or "EUR",
        bid=bid,
        ask=ask,
        quote_time=quote_time,
        market=(quote.get("market") or {}).get("name", ""),
        underlying_name=(underlying.get("instrument") or {}).get("name", ""),
        underlying_price=underlying_price,
        ko_barrier=ko_barrier,
        ratio=_num(underlying.get("coverRatio")),
        ko_distance_pct=ko_distance,
        leverage=_num(figure.get("gearing")) or _num(figure.get("gearingBid")),
        barrier_hit=barrier_hit,
    )
