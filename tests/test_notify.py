from datetime import timedelta
from unittest.mock import MagicMock

from hebelbot.notify import MAX_ATTEMPTS, Notifier
from hebelbot.store import utcnow


class FakeTelegram:
    def __init__(self):
        self.online = True
        self.sent = []

    def send(self, text: str) -> bool:
        if not self.online:
            return False
        self.sent.append(text)
        return True


def test_failed_message_is_queued_and_resent_later(store):
    tg = FakeTelegram()
    notify = Notifier(tg, store)

    tg.online = False
    assert notify("💥 KNOCK-OUT") is False
    assert store.count_pending() == 1
    assert tg.sent == []

    # Weitere Versuche scheitern, ohne dass die Nachricht verloren geht
    notify.flush()
    assert store.count_pending() == 1

    tg.online = True
    assert notify.flush() == 1
    assert store.count_pending() == 0
    assert "KNOCK-OUT" in tg.sent[0]


def test_order_is_preserved_while_outbox_has_entries(store):
    tg = FakeTelegram()
    notify = Notifier(tg, store)
    tg.online = False
    notify("erste")
    notify("zweite")
    assert store.count_pending() == 2

    tg.online = True
    notify("dritte")
    assert [t.split("\n")[0] for t in tg.sent] == ["erste", "zweite", "dritte"]
    assert store.count_pending() == 0


def test_delayed_message_gets_a_note(store):
    tg = FakeTelegram()
    notify = Notifier(tg, store)
    store.enqueue_message("🛑 STOP-LOSS", created_at=utcnow() - timedelta(minutes=12))
    notify.flush()
    assert "Verspätet zugestellt" in tg.sent[0]
    assert "12 min" in tg.sent[0]


def test_stale_message_is_dropped(store):
    tg = FakeTelegram()
    notify = Notifier(tg, store)
    store.enqueue_message("alt", created_at=utcnow() - timedelta(hours=30))
    notify.flush()
    assert tg.sent == []
    assert store.count_pending() == 0


def test_message_dropped_after_max_attempts(store):
    tg = FakeTelegram()
    tg.online = False
    notify = Notifier(tg, store)
    notify("dauerfehler")
    for _ in range(MAX_ATTEMPTS + 1):
        notify.flush()
    assert store.count_pending() == 0


def test_rejected_message_is_not_queued(store):
    # send() gibt True zurück, wenn Telegram die Nachricht inhaltlich ablehnt
    tg = MagicMock()
    tg.send.return_value = True
    assert Notifier(tg, store)("kaputtes <html") is True
    assert store.count_pending() == 0
