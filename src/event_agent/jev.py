from __future__ import annotations

from statistics import mean, pstdev
from typing import Any

from .indicators import exponential_moving_average, relative_strength_index
from .models import Movement


def _safe_last(values: list[float | None]) -> float | None:
    return values[-1] if values else None


def _calc_atr_percentage(prices: list[float], period: int = 14) -> float:
    if len(prices) < 2:
        return 0.0
    changes: list[float] = []
    start = max(1, len(prices) - period)
    for idx in range(start, len(prices)):
        prev = prices[idx - 1]
        if prev == 0:
            continue
        changes.append(abs(prices[idx] - prev) / prev)
    if not changes:
        return 0.0
    return mean(changes) * 100


def build_jev_submission(movements: list[Movement]) -> dict[str, Any]:
    prices = [item.price for item in movements]
    if not prices:
        return {
            "asset": "BTC/USDT",
            "timeframe": "15m",
            "trend": {"price_vs_ema200": "UNKNOWN", "ema_cross": "UNKNOWN"},
            "momentum": {"rsi_14": 50.0, "rsi_state": "NEUTRAL"},
            "volatility": {"bb_position": "MID_BAND", "atr_percentage": "0.0%"},
            "volume": {"obv_trend": "FLAT"},
        }

    ema_20 = _safe_last(exponential_moving_average(prices, period=20))
    ema_200 = _safe_last(exponential_moving_average(prices, period=200))
    prev_ema_20 = _safe_last(exponential_moving_average(prices[:-1], period=20)) if len(prices) > 1 else ema_20
    prev_ema_200 = _safe_last(exponential_moving_average(prices[:-1], period=200)) if len(prices) > 1 else ema_200

    latest_price = prices[-1]
    price_vs_ema200 = "ABOVE" if ema_200 is not None and latest_price >= ema_200 else "BELOW"

    if (
        ema_20 is None
        or ema_200 is None
        or prev_ema_20 is None
        or prev_ema_200 is None
        or (prev_ema_20 <= prev_ema_200 and ema_20 > ema_200)
    ):
        ema_cross = "BULLISH_CROSS"
    elif prev_ema_20 >= prev_ema_200 and ema_20 < ema_200:
        ema_cross = "BEARISH_CROSS"
    else:
        ema_cross = "BULLISH_CROSS" if ema_20 >= ema_200 else "BEARISH_CROSS"

    rsi_value = relative_strength_index(prices, period=14)
    if rsi_value is None:
        rsi_value = 50.0
    if rsi_value >= 65:
        rsi_state = "NEARING_OVERBOUGHT"
    elif rsi_value <= 35:
        rsi_state = "NEARING_OVERSOLD"
    else:
        rsi_state = "NEUTRAL"

    sample = prices[-20:] if len(prices) >= 20 else prices
    mid_band = mean(sample)
    band_dev = pstdev(sample) if len(sample) > 1 else 0.0
    upper_band = mid_band + (2 * band_dev)
    lower_band = mid_band - (2 * band_dev)

    if latest_price >= upper_band:
        bb_position = "UPPER_BAND_TOUCH"
    elif latest_price <= lower_band:
        bb_position = "LOWER_BAND_TOUCH"
    else:
        bb_position = "MID_BAND"

    atr_percentage = _calc_atr_percentage(prices)
    if len(prices) < 2:
        obv_trend = "FLAT"
    else:
        obv_trend = "RISING" if prices[-1] >= prices[-2] else "FALLING"

    return {
        "asset": "BTC/USDT",
        "timeframe": "15m",
        "trend": {
            "price_vs_ema200": price_vs_ema200,
            "ema_cross": ema_cross,
        },
        "momentum": {
            "rsi_14": round(rsi_value, 1),
            "rsi_state": rsi_state,
        },
        "volatility": {
            "bb_position": bb_position,
            "atr_percentage": f"{atr_percentage:.1f}%",
        },
        "volume": {
            "obv_trend": obv_trend,
        },
    }
