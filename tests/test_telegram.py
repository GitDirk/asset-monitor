from unittest.mock import MagicMock

from hebelbot.telegram import RETRY_DELAYS, TelegramClient, _split


def _client(responses):
    session = MagicMock()
    session.post.side_effect = [MagicMock(json=MagicMock(return_value=r)) for r in responses]
    return TelegramClient("token", 1, session=session)


def test_send_retries_on_server_error(monkeypatch):
    monkeypatch.setattr("hebelbot.telegram.time.sleep", lambda s: None)
    client = _client([{"ok": False, "error_code": 502, "description": "Bad Gateway"}, {"ok": True}])
    assert client.send("hallo") is True
    assert client.session.post.call_count == 2


def test_send_gives_up_after_retries(monkeypatch):
    monkeypatch.setattr("hebelbot.telegram.time.sleep", lambda s: None)
    client = _client([{"ok": False, "error_code": 502, "description": "x"}] * (len(RETRY_DELAYS) + 1))
    assert client.send("hallo") is False


def test_rejected_message_is_not_retried(monkeypatch):
    monkeypatch.setattr("hebelbot.telegram.time.sleep", lambda s: None)
    client = _client([{"ok": False, "error_code": 400, "description": "can not parse entities"}])
    # True = nicht erneut versuchen, sonst bliebe die Nachricht ewig im Postausgang
    assert client.send("<b>kaputt") is True
    assert client.session.post.call_count == 1


def test_long_text_is_split():
    parts = _split("\n\n".join(["x" * 1000] * 10))
    assert len(parts) > 1 and all(len(p) <= 4000 for p in parts)
