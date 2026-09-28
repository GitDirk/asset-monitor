from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, time, timezone
from pathlib import Path

import pytest

from assetmonitor.config import Settings
from assetmonitor.onvista import Instrument, QuoteError, parse_snapshot
from assetmonitor.store import Store

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def snapshot() -> dict:
    return load_fixture("onvista_snapshot_FC7CVG.json")


@pytest.fixture
def settings() -> Settings:
    return Settings(
        telegram_token="t",
        telegram_chat_id=42,
        db_path=":memory:",
        market_open=time(8, 0),
        market_close=time(22, 0),
        heartbeat_time=time(8, 0),
    )


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()


class FakeClient:
    """Ersetzt OnvistaClient: liefert steuerbare Kurse ohne Netzwerk."""

    def __init__(self, snapshot: dict):
        self.base = parse_snapshot(snapshot)
        self.quotes = {}
        self.fail = False

    def set(self, **changes):
        self.quotes[self.base.entity_id] = replace(self.base, **changes)

    def resolve(self, isin_or_wkn: str) -> Instrument:
        needle = isin_or_wkn.upper()
        if needle not in (self.base.isin, self.base.wkn):
            raise QuoteError(f"{needle} wurde bei onvista nicht gefunden")
        return Instrument(self.base.isin, self.base.wkn, self.base.name, self.base.entity_id, self.base.url)

    def quote(self, entity_id: str):
        if self.fail:
            raise QuoteError("Testfehler")
        return self.quotes.get(entity_id, self.base)


@pytest.fixture
def client(snapshot) -> FakeClient:
    return FakeClient(snapshot)


class Outbox(list):
    def __call__(self, text: str) -> bool:
        self.append(text)
        return True


@pytest.fixture
def outbox() -> Outbox:
    return Outbox()


# Donnerstag, 17.09.2026, 18:57 Uhr Berlin (Handelszeit)
MARKET_NOW = datetime(2026, 9, 17, 16, 57, tzinfo=timezone.utc)
