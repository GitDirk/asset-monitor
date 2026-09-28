from unittest.mock import MagicMock

import pytest

from conftest import load_fixture
from assetmonitor.onvista import OnvistaClient, QuoteError, parse_snapshot


def test_parse_snapshot_reads_quote_and_ko_data(snapshot):
    q = parse_snapshot(snapshot)
    assert q.isin == "DE000FC7CVG4"
    assert q.wkn == "FC7CVG"
    assert q.issuer == "Société Générale"
    assert q.direction == "Long"
    assert q.bid == 8.28
    assert q.ask == 8.29
    assert q.underlying_name == "S&P 500"
    assert q.ko_barrier == pytest.approx(6685.06267)
    assert q.ratio == 0.01
    assert q.ko_distance_pct == pytest.approx(12.427, abs=0.01)
    assert q.leverage == pytest.approx(8.01, abs=0.01)
    assert q.barrier_hit is False
    assert q.quote_time is not None and q.quote_time.tzinfo is not None


def test_parse_snapshot_detects_short_and_barrier_hit(snapshot):
    snapshot["derivativesDetails"]["codeExerciseRight"] = "P"
    snapshot["derivativesDetails"]["hasBarrierBeenHit"] = True
    q = parse_snapshot(snapshot)
    assert q.direction == "Short"
    assert q.barrier_hit is True


def test_parse_snapshot_without_bid_raises(snapshot):
    snapshot["quote"].pop("bid")
    with pytest.raises(QuoteError):
        parse_snapshot(snapshot)


def _client_returning(payload, status=200):
    session = MagicMock()
    session.headers = {}
    resp = MagicMock(status_code=status, headers={})
    resp.json.return_value = payload
    session.get.return_value = resp
    return OnvistaClient(session=session, min_interval=0)


def test_resolve_by_isin_and_wkn():
    payload = load_fixture("onvista_query_FC7CVG.json")
    client = _client_returning(payload)
    assert client.resolve("de000fc7cvg4").entity_id == "325330631"
    assert client.resolve("FC7CVG").isin == "DE000FC7CVG4"


def test_resolve_rejects_non_derivative():
    payload = {"list": [{"isin": "US0378331005", "wkn": "865985", "entityType": "STOCK",
                         "displayType": "Aktie", "entityValue": "86627"}]}
    with pytest.raises(QuoteError, match="kein Hebelprodukt"):
        _client_returning(payload).resolve("US0378331005")


def test_http_error_becomes_quote_error():
    with pytest.raises(QuoteError, match="HTTP 503"):
        _client_returning({}, status=503).quote("1")


def test_http_429_pauses_further_requests():
    client = _client_returning({}, status=429)
    with pytest.raises(QuoteError, match="429"):
        client.quote("1")
    with pytest.raises(QuoteError, match="nächster Versuch"):
        client.quote("1")
    assert client.session.get.call_count == 1


def test_client_sends_browser_user_agent():
    client = OnvistaClient()
    assert client.session.headers["User-Agent"].startswith("Mozilla/")
