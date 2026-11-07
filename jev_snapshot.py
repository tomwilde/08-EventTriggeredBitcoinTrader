import json

import pandas as pd
import ta


def generate_jev_snapshot(df: pd.DataFrame) -> str:
    """Takes a pandas DataFrame with columns:

    'open', 'high', 'low', 'close', 'volume'
    Returns a structured JSON string optimized for Jev's context window.
    """
    # Ensure we have enough data to calculate indicators
    if len(df) < 200:
        raise ValueError("DataFrame needs at least 200 rows for stable indicators.")

    # 1. Trend Indicators
    df["ema_20"] = ta.trend.ema_indicator(df["close"], window=20)
    df["ema_200"] = ta.trend.ema_indicator(df["close"], window=200)

    # 2. Momentum Indicators
    df["rsi"] = ta.momentum.rsi(df["close"], window=14)

    # 3. Volatility Indicators
    bb = ta.volatility.BollingerBands(df["close"], window=20, window_dev=2)
    df["bb_high"] = bb.bollinger_hband()
    df["bb_low"] = bb.bollinger_lband()
    df["atr"] = ta.volatility.average_true_range(
        df["high"], df["low"], df["close"], window=14
    )

    # 4. Volume Indicators
    df["obv"] = ta.volume.on_balance_volume(df["close"], df["volume"])

    # Grab the latest closed candle data (last row)
    latest = df.iloc[-1]
    prev = df.iloc[-2]

    current_price = float(latest["close"])

    # Structure qualitative descriptors for Jev
    trend_state = "ABOVE_EMA200" if current_price > latest["ema_200"] else "BELOW_EMA200"
    ema_cross = "BULLISH" if latest["ema_20"] > latest["ema_200"] else "BEARISH"

    rsi_val = float(latest["rsi"])
    if rsi_val >= 70:
        rsi_state = "OVERBOUGHT"
    elif rsi_val <= 30:
        rsi_state = "OVERSOLD"
    else:
        rsi_state = "NEUTRAL"

    # Bollinger Band relative positioning
    if current_price >= latest["bb_high"]:
        bb_state = "UPPER_BAND_BREAKOUT"
    elif current_price <= latest["bb_low"]:
        bb_state = "LOWER_BAND_BREAKOUT"
    else:
        bb_state = "INSIDE_BANDS"

    # Volume trend over the last two candles
    volume_trend = "RISING" if latest["obv"] > prev["obv"] else "FALLING"

    # Create the lightweight snapshot payload
    snapshot = {
        "market": "BTC/USDT",
        "current_price": round(current_price, 2),
        "trend": {"ema_alignment": trend_state, "ema_momentum": ema_cross},
        "momentum": {"rsi_14": round(rsi_val, 1), "rsi_state": rsi_state},
        "volatility": {
            "bollinger_state": bb_state,
            "atr_value": round(float(latest["atr"]), 2),
        },
        "volume": {"obv_momentum": volume_trend},
    }

    return json.dumps(snapshot, indent=2)


# # --- EXAMPLE USAGE ---
# if __name__ == "__main__":
#     # Simulate a mockup historical price dataset (e.g., fetched from an exchange API)
#     # In production, replace this with data from ccxt or an exchange API
#     import numpy as np

#     np.random.seed(42)

#     dummy_data = {
#         "open": np.random.uniform(90000, 95000, 250),
#         "high": np.random.uniform(95000, 96000, 250),
#         "low": np.random.uniform(89000, 90000, 250),
#         "close": np.random.uniform(90000, 95000, 250),
#         "volume": np.random.uniform(10, 500, 250),
#     }

#     df_market = pd.DataFrame(dummy_data)

#     # Convert raw price metrics into Jev payload
#     jev_input_payload = generate_jev_snapshot(df_market)
#     print("--- PAYLOAD TO SEND TO JEV API ---")
#     print(jev_input_payload)
