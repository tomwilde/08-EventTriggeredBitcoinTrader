from __future__ import annotations

import os
from collections.abc import Iterable
from datetime import UTC, datetime

import requests

from .models import Movement

COINGECKO_ENDPOINT = "https://api.coingecko.com/api/v3/simple/price"
COINGECKO_MARKET_CHART_ENDPOINT = "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart"
BINANCE_TICKER_ENDPOINT = "https://api.binance.com/api/v3/ticker/price"
COINBASE_SPOT_ENDPOINT = "https://api.coinbase.com/v2/prices/BTC-USD/spot"
KRAKEN_TICKER_ENDPOINT = "https://api.kraken.com/0/public/Ticker"
BINANCE_KLINES_ENDPOINT = "https://api.binance.com/api/v3/klines"
KRAKEN_OHLC_ENDPOINT = "https://api.kraken.com/0/public/OHLC"


def _parse_price(provider: str, payload: dict[str, object]) -> float:
    if provider == "coingecko":
        return float(payload["bitcoin"]["usd"])
    if provider == "binance":
        return float(payload["price"])
    if provider == "coinbase":
        return float(payload["data"]["amount"])
    if provider == "kraken":
        result = payload["result"]
        pair = next(iter(result.values()))
        return float(pair["c"][0])
    raise ValueError(f"Unsupported provider: {provider}")


def _fetch_from_provider(provider: str, timeout_seconds: int) -> float:
    if provider == "coingecko":
        response = requests.get(
            COINGECKO_ENDPOINT,
            params={"ids": "bitcoin", "vs_currencies": "usd"},
            timeout=timeout_seconds,
        )
    elif provider == "binance":
        response = requests.get(
            BINANCE_TICKER_ENDPOINT,
            params={"symbol": "BTCUSDT"},
            timeout=timeout_seconds,
        )
    elif provider == "coinbase":
        response = requests.get(COINBASE_SPOT_ENDPOINT, timeout=timeout_seconds)
    elif provider == "kraken":
        response = requests.get(
            KRAKEN_TICKER_ENDPOINT,
            params={"pair": "XBTUSD"},
            timeout=timeout_seconds,
        )
    else:
        raise ValueError(f"Unsupported provider: {provider}")

    response.raise_for_status()
    payload = response.json()
    return _parse_price(provider, payload)


def _provider_order() -> list[str]:
    configured = os.getenv("BTC_PRICE_PROVIDERS", "").strip()
    if configured:
        return [value.strip().lower() for value in configured.split(",") if value.strip()]
    return ["coingecko", "binance", "coinbase", "kraken"]


def _history_provider_order() -> list[str]:
    configured = os.getenv("BTC_HISTORY_PROVIDERS", "").strip()
    if configured:
        return [value.strip().lower() for value in configured.split(",") if value.strip()]
    return ["coingecko", "binance", "kraken"]


def _movement_from_parts(timestamp: float, price: float) -> Movement:
    return Movement(
        timestamp=datetime.fromtimestamp(timestamp, tz=UTC),
        price=float(price),
    )


def _fetch_history_from_provider(provider: str, days: int, timeout_seconds: int) -> list[Movement]:
    if provider == "coingecko":
        response = requests.get(
            COINGECKO_MARKET_CHART_ENDPOINT,
            params={"vs_currency": "usd", "days": str(days)},
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        points = payload.get("prices", [])
        history: list[Movement] = []
        for point in points:
            if len(point) < 2:
                continue
            timestamp_ms, price = point
            history.append(_movement_from_parts(float(timestamp_ms) / 1000.0, float(price)))
        return history

    if provider == "binance":
        limit = max(24, min(1000, days * 24))
        response = requests.get(
            BINANCE_KLINES_ENDPOINT,
            params={"symbol": "BTCUSDT", "interval": "1h", "limit": str(limit)},
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        history = []
        for kline in payload:
            if len(kline) < 5:
                continue
            open_time_ms = float(kline[0])
            close_price = float(kline[4])
            history.append(_movement_from_parts(open_time_ms / 1000.0, close_price))
        return history

    if provider == "kraken":
        response = requests.get(
            KRAKEN_OHLC_ENDPOINT,
            params={"pair": "XBTUSD", "interval": "60"},
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        result = payload.get("result", {})
        if not isinstance(result, dict):
            return []

        series: Iterable[list[str | int | float]] | None = None
        for key, value in result.items():
            if key == "last":
                continue
            if isinstance(value, list):
                series = value
                break

        if series is None:
            return []

        rows = list(series)
        if days > 0 and len(rows) > days * 24:
            rows = rows[-(days * 24) :]

        history = []
        for row in rows:
            if len(row) < 5:
                continue
            timestamp_seconds = float(row[0])
            close_price = float(row[4])
            history.append(_movement_from_parts(timestamp_seconds, close_price))
        return history

    raise ValueError(f"Unsupported provider: {provider}")


def fetch_bitcoin_price(timeout_seconds: int = 10) -> Movement:
    last_error: Exception | None = None
    for provider in _provider_order():
        try:
            price = _fetch_from_provider(provider, timeout_seconds)
            return Movement(timestamp=datetime.now(UTC), price=price)
        except requests.HTTPError as error:
            if error.response is not None and error.response.status_code == 429:
                last_error = error
                continue
            raise
        except (requests.RequestException, KeyError, ValueError, TypeError) as error:
            last_error = error
            continue

    if last_error is None:
        raise RuntimeError("No price provider configured")
    raise last_error


def fetch_bitcoin_price_history(days: int = 30, timeout_seconds: int = 20) -> list[Movement]:
    last_error: Exception | None = None
    for provider in _history_provider_order():
        try:
            history = _fetch_history_from_provider(provider, days, timeout_seconds)
            if history:
                return history
        except requests.HTTPError as error:
            if error.response is not None and error.response.status_code == 429:
                last_error = error
                continue
            raise
        except (requests.RequestException, KeyError, ValueError, TypeError) as error:
            last_error = error
            continue

    if last_error is None:
        raise RuntimeError("No history provider configured")
    raise last_error
