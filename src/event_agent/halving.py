from __future__ import annotations

from datetime import UTC, datetime, timedelta

import requests

BLOCK_HEIGHT_ENDPOINT = "https://blockstream.info/api/blocks/tip/height"
HALVING_INTERVAL_BLOCKS = 210_000
AVERAGE_BLOCK_MINUTES = 10


def estimate_next_bitcoin_halving_datetime(
    now: datetime | None = None, timeout_seconds: int = 10
) -> datetime:
    anchor = now or datetime.now(UTC)
    response = requests.get(BLOCK_HEIGHT_ENDPOINT, timeout=timeout_seconds)
    response.raise_for_status()

    current_height = int(response.text.strip())
    next_halving_height = ((current_height // HALVING_INTERVAL_BLOCKS) + 1) * HALVING_INTERVAL_BLOCKS
    blocks_remaining = next_halving_height - current_height
    return anchor + timedelta(minutes=blocks_remaining * AVERAGE_BLOCK_MINUTES)
