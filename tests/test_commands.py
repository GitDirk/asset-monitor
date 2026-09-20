import pytest

from hebelbot.commands import CommandHandler
from hebelbot.store import ACTIVE, HIT_SL


@pytest.fixture
def handler(store, client, settings):
    return CommandHandler(store, client, settings, lambda: "status ok")


def test_add_with_defaults_uses_ask_and_20_30(handler, store):
    reply = handler.handle("/add DE000FC7CVG4")
    assert "Überwache FC7CVG" in reply
    pos = store.find("DE000FC7CVG4")
    assert pos.entry == pytest.approx(8.29)  # Briefkurs
    assert pos.sl == pytest.approx(8.29 * 0.8)
    assert pos.tp == pytest.approx(8.29 * 1.3)
    assert pos.name == "S&P 500 Long"
    assert pos.status == ACTIVE


def test_add_positional_and_named(handler, store):
    handler.handle("/add fc7cvg -15% +40% 8,00")
    pos = store.find("FC7CVG")
    assert (pos.entry, pos.sl, pos.tp) == (pytest.approx(8.0), pytest.approx(6.8), pytest.approx(11.2))

    store.delete(pos.isin)
    handler.handle("/add FC7CVG tp=50% einstieg=8")
    pos = store.find("FC7CVG")
    assert pos.sl == pytest.approx(6.4)
    assert pos.tp == pytest.approx(12.0)


def test_add_placeholder_keeps_default_sl(handler, store):
    handler.handle("/add FC7CVG - 11 8")
    pos = store.find("FC7CVG")
    assert pos.sl == pytest.approx(6.4)
    assert pos.tp == pytest.approx(11.0)


def test_add_rejects_duplicates_and_bad_levels(handler, store):
    handler.handle("/add FC7CVG")
    assert "schon überwacht" in handler.handle("/add DE000FC7CVG4")
    store.delete("DE000FC7CVG4")
    assert "❌" in handler.handle("/add FC7CVG 12 10")
    assert store.find("FC7CVG") is None


def test_add_unknown_isin(handler):
    assert "nicht gefunden" in handler.handle("/add DE0000000000")


def test_add_already_knocked_out(handler, client):
    client.set(barrier_hit=True)
    assert "ausgeknockt" in handler.handle("/add FC7CVG")


def test_set_level_resets_triggered_alarm(handler, store):
    handler.handle("/add FC7CVG -20% +30% 8")
    pos = store.find("FC7CVG")
    pos.status = HIT_SL
    store.save(pos)
    reply = handler.handle("/sl FC7CVG 5")
    assert "zurückgesetzt" in reply
    pos = store.find("FC7CVG")
    assert pos.sl == 5 and pos.status == ACTIVE


def test_add_and_list_show_leverage(handler):
    assert "Hebel: 8,01" in handler.handle("/add FC7CVG")
    assert "Hebel 8,01" in handler.handle("/list")


def test_list_and_remove(handler, store):
    assert "Keine Positionen" in handler.handle("/list")
    handler.handle("/add FC7CVG")
    assert "FC7CVG" in handler.handle("/list")
    assert "nicht mehr überwacht" in handler.handle("/remove FC7CVG")
    assert store.all() == []


def test_list_survives_quote_error(handler, client):
    handler.handle("/add FC7CVG")
    client.fail = True
    assert "nicht abrufbar" in handler.handle("/list")


def test_misc_commands(handler):
    assert "SL/TP-Wächter" in handler.handle("/help")
    assert "SL/TP-Wächter" in handler.handle("/start@MeinBot")
    assert handler.handle("/status") == "status ok"
    assert "Unbekannter Befehl" in handler.handle("/foo")
    assert "KO-Schwelle" in handler.handle("/kurs FC7CVG")
