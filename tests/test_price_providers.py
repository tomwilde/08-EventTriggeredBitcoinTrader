from __future__ import annotations

from datetime import UTC

import pytest
import requests

from event_agent.price import fetch_bitcoin_price, fetch_bitcoin_price_history


class _MockResponse:
    def __init__(self, payload: dict[str, object] | None = None, status_code: int = 200):
        self._payload = payload or {}
        self.status_code = status_code

    def json(self) -> dict[str, object]:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            error = requests.HTTPError(f"HTTP {self.status_code}")
            error.response = self
            raise error


def test_price_falls_back_when_primary_is_rate_limited(monkeypatch: pytest.MonkeyPatch):
    calls: list[str] = []

    def _fake_get(url: str, **kwargs):
        calls.append(url)
        if "coingecko" in url:
            return _MockResponse(status_code=429)
        if "binance" in url:
            return _MockResponse(payload={"price": "65000.12"})
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setenv("BTC_PRICE_PROVIDERS", "coingecko,binance")
    monkeypatch.setattr("event_agent.price.requests.get", _fake_get)

    movement = fetch_bitcoin_price()

    assert movement.price == pytest.approx(65000.12)
    assert movement.timestamp.tzinfo is UTC
    assert any("coingecko" in item for item in calls)
    assert any("binance" in item for item in calls)


def test_price_uses_configured_provider_order(monkeypatch: pytest.MonkeyPatch):
    calls: list[str] = []

    def _fake_get(url: str, **kwargs):
        calls.append(url)
        if "coinbase" in url:
            return _MockResponse(payload={"data": {"amount": "64000.0"}})
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setenv("BTC_PRICE_PROVIDERS", "coinbase")
    monkeypatch.setattr("event_agent.price.requests.get", _fake_get)

    movement = fetch_bitcoin_price()

    assert movement.price == pytest.approx(64000.0)
    assert len(calls) == 1
    assert "coinbase" in calls[0]


def test_history_falls_back_when_coingecko_is_rate_limited(monkeypatch: pytest.MonkeyPatch):
    calls: list[str] = []

    def _fake_get(url: str, **kwargs):
        calls.append(url)
        if "market_chart" in url:
            return _MockResponse(status_code=429)
        if "klines" in url:
            return _MockResponse(
                payload=[
                    [1731000000000, "64000", "64500", "63900", "64400", "100", 0, 0, 0, 0, 0, 0],
                    [1731003600000, "64400", "64600", "64300", "64500", "90", 0, 0, 0, 0, 0, 0],
                ]
            )
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setenv("BTC_HISTORY_PROVIDERS", "coingecko,binance")
    monkeypatch.setattr("event_agent.price.requests.get", _fake_get)

    history = fetch_bitcoin_price_history(days=30)

    assert len(history) == 2
    assert history[-1].price == pytest.approx(64500.0)
    assert history[-1].timestamp.tzinfo is UTC
    assert any("market_chart" in item for item in calls)
    assert any("klines" in item for item in calls)
