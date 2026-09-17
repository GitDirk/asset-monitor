"""SQLite-Persistenz für überwachte Positionen."""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

# Status einer Position
ACTIVE = "aktiv"
HIT_SL = "sl"
HIT_TP = "tp"
KNOCKED_OUT = "ko"

SCHEMA = """
CREATE TABLE IF NOT EXISTS positions (
    isin            TEXT PRIMARY KEY,
    wkn             TEXT NOT NULL DEFAULT '',
    name            TEXT NOT NULL DEFAULT '',
    entity_id       TEXT NOT NULL,
    url             TEXT NOT NULL DEFAULT '',
    entry           REAL NOT NULL,
    sl              REAL NOT NULL,
    tp              REAL NOT NULL,
    status          TEXT NOT NULL DEFAULT 'aktiv',
    created_at      TEXT NOT NULL,
    triggered_at    TEXT,
    last_alert_at   TEXT,
    ko_warned_at    TEXT,
    last_bid        REAL,
    last_quote_at   TEXT
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ts(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _dt(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value) if value else None


@dataclass
class Position:
    isin: str
    wkn: str
    name: str
    entity_id: str
    url: str
    entry: float
    sl: float
    tp: float
    status: str
    created_at: datetime
    triggered_at: Optional[datetime] = None
    last_alert_at: Optional[datetime] = None
    ko_warned_at: Optional[datetime] = None
    last_bid: Optional[float] = None
    last_quote_at: Optional[datetime] = None

    @property
    def label(self) -> str:
        return self.wkn or self.isin


class Store:
    def __init__(self, path: str):
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    @staticmethod
    def _row(row: sqlite3.Row) -> Position:
        return Position(
            isin=row["isin"],
            wkn=row["wkn"],
            name=row["name"],
            entity_id=row["entity_id"],
            url=row["url"],
            entry=row["entry"],
            sl=row["sl"],
            tp=row["tp"],
            status=row["status"],
            created_at=_dt(row["created_at"]),
            triggered_at=_dt(row["triggered_at"]),
            last_alert_at=_dt(row["last_alert_at"]),
            ko_warned_at=_dt(row["ko_warned_at"]),
            last_bid=row["last_bid"],
            last_quote_at=_dt(row["last_quote_at"]),
        )

    def all(self) -> List[Position]:
        rows = self.conn.execute("SELECT * FROM positions ORDER BY created_at").fetchall()
        return [self._row(r) for r in rows]

    def find(self, isin_or_wkn: str) -> Optional[Position]:
        needle = isin_or_wkn.strip().upper()
        row = self.conn.execute(
            "SELECT * FROM positions WHERE upper(isin) = ? OR upper(wkn) = ?", (needle, needle)
        ).fetchone()
        return self._row(row) if row else None

    def save(self, p: Position) -> None:
        self.conn.execute(
            """
            INSERT INTO positions (isin, wkn, name, entity_id, url, entry, sl, tp, status,
                                   created_at, triggered_at, last_alert_at, ko_warned_at,
                                   last_bid, last_quote_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(isin) DO UPDATE SET
                wkn=excluded.wkn, name=excluded.name, entity_id=excluded.entity_id,
                url=excluded.url, entry=excluded.entry, sl=excluded.sl, tp=excluded.tp,
                status=excluded.status, created_at=excluded.created_at,
                triggered_at=excluded.triggered_at, last_alert_at=excluded.last_alert_at,
                ko_warned_at=excluded.ko_warned_at, last_bid=excluded.last_bid,
                last_quote_at=excluded.last_quote_at
            """,
            (
                p.isin, p.wkn, p.name, p.entity_id, p.url, p.entry, p.sl, p.tp, p.status,
                _ts(p.created_at), _ts(p.triggered_at), _ts(p.last_alert_at),
                _ts(p.ko_warned_at), p.last_bid, _ts(p.last_quote_at),
            ),
        )
        self.conn.commit()

    def delete(self, isin: str) -> bool:
        cur = self.conn.execute("DELETE FROM positions WHERE isin = ?", (isin,))
        self.conn.commit()
        return cur.rowcount > 0

    def get_meta(self, key: str) -> Optional[str]:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.conn.commit()
