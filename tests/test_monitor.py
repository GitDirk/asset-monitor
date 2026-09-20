from datetime import datetime, timedelta, timezone

import pytest

from conftest import MARKET_NOW
from hebelbot.monitor import FAILURE_ALERT_THRESHOLD, Monitor
from hebelbot.store import ACTIVE, HIT_SL, HIT_TP, KNOCKED_OUT, Position


@pytest.fixture
def monitor(store, client, outbox, settings):
    return Monitor(store, client, outbox, settings)


@pytest.fixture
def position(store, client):
    q = client.base
    p = Position(
        isin=q.isin, wkn=q.wkn, name="S&P 500 Long", entity_id=q.entity_id, url=q.url,
        entry=8.0, sl=6.4, tp=10.4, status=ACTIVE, created_at=MARKET_NOW,
    )
    store.save(p)
    return p


def fresh(client, **changes):
    changes.setdefault("quote_time", MARKET_NOW - timedelta(seconds=10))
    client.set(**changes)


def test_no_alert_inside_range(monitor, client, outbox, position, store):
    fresh(client, bid=8.28)
    monitor.run_cycle(MARKET_NOW)
    assert outbox == []
    assert store.find(position.isin).last_bid == 8.28


def test_stop_loss_alert_once_then_reminder(monitor, client, outbox, position, store):
    fresh(client, bid=6.39)
    monitor.run_cycle(MARKET_NOW)
    assert len(outbox) == 1 and "STOP-LOSS" in outbox[0]
    assert store.find(position.isin).status == HIT_SL

    monitor.run_cycle(MARKET_NOW + timedelta(minutes=10))
    assert len(outbox) == 1  # noch keine Erinnerung

    fresh(client, bid=6.2, quote_time=MARKET_NOW + timedelta(minutes=30))
    monitor.run_cycle(MARKET_NOW + timedelta(minutes=31))
    assert len(outbox) == 2 and "Erinnerung" in outbox[1]


def test_take_profit_alert(monitor, client, outbox, position, store):
    fresh(client, bid=10.4)
    monitor.run_cycle(MARKET_NOW)
    assert "TAKE-PROFIT" in outbox[0]
    assert "+30,00 %" in outbox[0]
    assert store.find(position.isin).status == HIT_TP


def test_knock_out_alert_only_once(monitor, client, outbox, position, store):
    fresh(client, bid=0.001, barrier_hit=True)
    monitor.run_cycle(MARKET_NOW)
    monitor.run_cycle(MARKET_NOW + timedelta(hours=2))
    assert len(outbox) == 1 and "KNOCK-OUT" in outbox[0]
    assert store.find(position.isin).status == KNOCKED_OUT


def test_ko_proximity_warning_throttled(monitor, client, outbox, position):
    fresh(client, bid=7.5, ko_distance_pct=2.5)
    monitor.run_cycle(MARKET_NOW)
    monitor.run_cycle(MARKET_NOW + timedelta(minutes=5))
    assert len(outbox) == 1 and "KO-Schwelle nah" in outbox[0]
    fresh(client, bid=7.5, ko_distance_pct=2.5, quote_time=MARKET_NOW + timedelta(minutes=31))
    monitor.run_cycle(MARKET_NOW + timedelta(minutes=31))
    assert len(outbox) == 2 and "KO-Schwelle nah" in outbox[1]


def test_failures_alert_after_threshold_and_recovery(monitor, client, outbox, position):
    client.fail = True
    for i in range(FAILURE_ALERT_THRESHOLD + 3):
        monitor.run_cycle(MARKET_NOW + timedelta(minutes=i))
    assert len(outbox) == 1 and "gestört" in outbox[0]
    client.fail = False
    fresh(client, bid=8.0, quote_time=MARKET_NOW + timedelta(minutes=20))
    monitor.run_cycle(MARKET_NOW + timedelta(minutes=20))
    assert "läuft wieder" in outbox[-1]


def test_stale_quote_warning_during_market_hours(monitor, client, outbox, position):
    fresh(client, bid=8.0, quote_time=MARKET_NOW - timedelta(minutes=40))
    monitor.run_cycle(MARKET_NOW)
    monitor.run_cycle(MARKET_NOW + timedelta(minutes=1))
    assert len(outbox) == 1 and "veraltet" in outbox[0]
    fresh(client, bid=8.0, quote_time=MARKET_NOW + timedelta(minutes=2))
    monitor.run_cycle(MARKET_NOW + timedelta(minutes=2))
    assert "wieder" in outbox[-1]


def test_no_stale_warning_right_after_open_or_weekend(monitor, client, outbox, position):
    # Montag 08:05 Berlin, letzter Kurs vom Freitagabend
    monday = datetime(2026, 9, 21, 6, 5, tzinfo=timezone.utc)
    fresh(client, bid=8.0, quote_time=monday - timedelta(days=2, hours=10))
    monitor.run_cycle(monday)
    saturday = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)
    monitor.run_cycle(saturday)
    assert outbox == []


def test_market_hours_and_interval(monitor, settings):
    assert monitor.is_market_open(MARKET_NOW)
    night = datetime(2026, 9, 17, 21, 30, tzinfo=timezone.utc)  # 23:30 Berlin
    assert not monitor.is_market_open(night)
    assert monitor.poll_interval(night) == settings.poll_seconds_closed


def test_heartbeat_once_per_weekday(monitor, outbox, position):
    morning = datetime(2026, 9, 17, 6, 30, tzinfo=timezone.utc)  # 08:30 Berlin
    assert monitor.maybe_heartbeat(morning)
    assert not monitor.maybe_heartbeat(morning + timedelta(hours=1))
    assert "Bot läuft" in outbox[0]
    saturday = datetime(2026, 9, 19, 7, 0, tzinfo=timezone.utc)
    assert not monitor.maybe_heartbeat(saturday)


def test_alert_during_network_outage_is_delivered_later(store, client, settings):
    """Knock-out-Alarm bei Netzausfall: die Nachricht darf nicht verloren gehen."""
    from hebelbot.notify import Notifier
    from test_notify import FakeTelegram

    tg = FakeTelegram()
    notifier = Notifier(tg, store)
    monitor = Monitor(store, client, notifier, settings)
    q = client.base
    store.save(Position(
        isin=q.isin, wkn=q.wkn, name="S&P 500 Long", entity_id=q.entity_id, url=q.url,
        entry=8.0, sl=6.4, tp=10.4, status=ACTIVE, created_at=MARKET_NOW,
    ))

    tg.online = False
    fresh(client, bid=0.001, barrier_hit=True)
    monitor.run_cycle(MARKET_NOW)
    assert tg.sent == []
    assert store.count_pending() == 1
    assert store.find(q.isin).status == KNOCKED_OUT

    tg.online = True
    notifier.flush()
    assert len(tg.sent) == 1 and "KNOCK-OUT" in tg.sent[0]
    assert store.count_pending() == 0
