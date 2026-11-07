from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

Direction = Literal["up", "down"]
Outlook = Literal["bullish", "bearish"]
TradeAction = Literal["BUY", "SELL", "HOLD"]


@dataclass(slots=True)
class Movement:
    timestamp: datetime
    price: float


@dataclass(slots=True)
class NewsItem:
    source: str
    story_id: str
    title: str
    summary: str
    published_at: datetime


@dataclass(slots=True)
class NewsUpdate:
    detected_at: datetime
    story_id: str
    title: str
    source: str


@dataclass(slots=True)
class Prediction:
    timestamp: datetime
    direction: Direction
    magnitude: int
    rationale: str


@dataclass(slots=True)
class JevDecision:
    timestamp: datetime
    action: TradeAction
    confidence: float
    rationale_token: str
    outlook: Outlook


@dataclass(slots=True)
class SettledTrade:
    prediction: Prediction
    entry: Movement
    exit: Movement
    notional: float
    pnl: float
    correct_direction: bool | None


@dataclass(slots=True)
class AppState:
    prices: list[Movement] = field(default_factory=list)
    moving_averages: dict[str, list[float | None]] = field(default_factory=dict)
    news: list[NewsItem] = field(default_factory=list)
    news_updates: list[NewsUpdate] = field(default_factory=list)
    predictions: list[Prediction] = field(default_factory=list)
    jev_decisions: list[JevDecision] = field(default_factory=list)
    settled_trades: list[SettledTrade] = field(default_factory=list)
    dead_letters: list[str] = field(default_factory=list)
    startup_emas: dict[str, float] = field(default_factory=dict)
    market_outlook: Outlook | None = None
    next_halving_at: datetime | None = None
    startup_complete: bool = False
