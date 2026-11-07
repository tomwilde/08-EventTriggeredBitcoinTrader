def moving_average_series(prices: list[float], window: int) -> list[float | None]:
    if window <= 0:
        raise ValueError("window must be > 0")

    values: list[float | None] = []
    for idx in range(len(prices)):
        if idx + 1 < window:
            values.append(None)
            continue

        sample = prices[idx - window + 1 : idx + 1]
        values.append(sum(sample) / window)

    return values


def exponential_moving_average(prices: list[float], period: int) -> list[float | None]:
    if period <= 0:
        raise ValueError("period must be > 0")
    if not prices:
        return []

    alpha = 2 / (period + 1)
    ema_values: list[float | None] = []
    ema: float | None = None
    for price in prices:
        if ema is None:
            ema = price
        else:
            ema = (price * alpha) + (ema * (1 - alpha))
        ema_values.append(ema)

    return ema_values


def relative_strength_index(prices: list[float], period: int = 14) -> float | None:
    if period <= 0:
        raise ValueError("period must be > 0")
    if len(prices) <= period:
        return None

    gains: list[float] = []
    losses: list[float] = []
    for idx in range(1, len(prices)):
        delta = prices[idx] - prices[idx - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for idx in range(period, len(gains)):
        avg_gain = ((avg_gain * (period - 1)) + gains[idx]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[idx]) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))
