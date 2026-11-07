from __future__ import annotations

import os
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Literal

import requests
from pika.exceptions import AMQPConnectionError

from .halving import estimate_next_bitcoin_halving_datetime
from .indicators import exponential_moving_average
from .jev import build_jev_submission
from .models import AppState, Movement, Outlook, Prediction
from .prediction import (
    JevAdvisor,
    OutlookAnalyzer,
    build_jev_advisor,
    build_outlook_analyzer,
)
from .price import fetch_bitcoin_price, fetch_bitcoin_price_history
from .queueing import InMemoryBroker, RabbitMQBroker, movement_from_json, movement_to_json
from .scheduler import NonOverlappingScheduler, run_with_retries
from .trading import OpenTrade, PaperBook

STARTUP_EMA_WINDOWS: dict[str, timedelta] = {
    "1_month": timedelta(days=30),
    "3_weeks": timedelta(weeks=3),
    "2_weeks": timedelta(weeks=2),
    "1_week": timedelta(weeks=1),
    "3_day": timedelta(days=3),
    "1_day": timedelta(days=1),
    "12_hours": timedelta(hours=12),
    "6_hours": timedelta(hours=6),
    "1_hour": timedelta(hours=1),
    "30_mins": timedelta(minutes=30),
    "10_mins": timedelta(minutes=10),
    "5_mins": timedelta(minutes=5),
}


class AutomationEngine:
    def __init__(
        self,
        *,
        outlook_analyzer: OutlookAnalyzer | None = None,
        jev_advisor: JevAdvisor | None = None,
    ) -> None:
        self.state = AppState()
        self.book = PaperBook(starting_cash=100000.0)
        self.broker = self._build_broker()
        self.outlook_analyzer = outlook_analyzer or build_outlook_analyzer()
        self.jev_advisor = jev_advisor or build_jev_advisor()
        self.price_scheduler = NonOverlappingScheduler()
        self._open_trade: OpenTrade | None = None
        self._background_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._startup_lock = threading.Lock()
        self.jev_interval_seconds = 120.0
        self.max_backoff_seconds = 120.0
        self.current_poll_interval_seconds = 0.0
        self._last_jev_decision_at: datetime | None = None
        self._next_price_attempt_at: datetime | None = None

    def _record_dead_letter(self, *, kind: str, error: str) -> None:
        timestamp = datetime.now(UTC).isoformat(timespec="seconds")
        self.broker.publish_json(
            "DeadLetter",
            {
                "kind": kind,
                "error": error,
                "timestamp": timestamp,
            },
        )
        self.state.dead_letters.append(f"[{timestamp}] {kind}: {error}")

    @property
    def open_trade(self) -> OpenTrade | None:
        return self._open_trade

    @property
    def is_running(self) -> bool:
        return self._background_thread is not None and self._background_thread.is_alive()

    @property
    def ui_ready(self) -> bool:
        return self.state.startup_complete

    @property
    def current_outlook(self) -> Outlook | None:
        return self.state.market_outlook

    def initialize_startup_context(self) -> None:
        if self.state.startup_complete:
            return

        with self._startup_lock:
            if self.state.startup_complete:
                return

            now = datetime.now(UTC)
            try:
                history = fetch_bitcoin_price_history(days=30)
                if history:
                    self.state.prices = history
                    self._recompute_moving_averages()
                self.state.startup_emas = self._compute_startup_emas()

                next_halving_at = estimate_next_bitcoin_halving_datetime(now=now)
                self.state.next_halving_at = next_halving_at
                self.state.market_outlook = self.outlook_analyzer.analyze(
                    stories=[],
                    startup_emas=self.state.startup_emas,
                    next_halving_at=next_halving_at,
                    now=now,
                )
            except (requests.RequestException, ValueError, KeyError) as error:
                self._record_dead_letter(kind="startup_failure", error=str(error))
                if not self.state.prices:
                    try:
                        self.state.prices = [fetch_bitcoin_price()]
                    except requests.RequestException:
                        self.state.prices = [Movement(timestamp=now, price=0.0)]
                self._recompute_moving_averages()
                if not self.state.startup_emas:
                    self.state.startup_emas = self._compute_startup_emas()
                if self.state.next_halving_at is None:
                    self.state.next_halving_at = now + timedelta(days=365)
                if self.state.market_outlook is None:
                    self.state.market_outlook = "bullish"
            finally:
                self.state.startup_complete = True

    @staticmethod
    def _build_broker() -> InMemoryBroker | RabbitMQBroker:
        rabbitmq_url = os.getenv("RABBITMQ_URL", "").strip()
        if not rabbitmq_url:
            return InMemoryBroker()
        try:
            return RabbitMQBroker(rabbitmq_url)
        except (AMQPConnectionError, OSError):
            return InMemoryBroker()

    def _sample_seconds(self) -> float:
        history = self.state.prices
        if len(history) < 2:
            return 3600.0

        deltas = [
            (history[idx].timestamp - history[idx - 1].timestamp).total_seconds()
            for idx in range(1, len(history))
            if history[idx].timestamp >= history[idx - 1].timestamp
        ]
        if not deltas:
            return 3600.0
        return max(1.0, sum(deltas) / len(deltas))

    def _window_points(self) -> dict[str, int]:
        sample_seconds = self._sample_seconds()
        return {
            key: max(2, round(duration.total_seconds() / sample_seconds))
            for key, duration in STARTUP_EMA_WINDOWS.items()
        }

    def _recompute_moving_averages(self) -> None:
        prices = [item.price for item in self.state.prices]
        if not prices:
            self.state.moving_averages = {key: [] for key in STARTUP_EMA_WINDOWS}
            return

        windows = self._window_points()
        series: dict[str, list[float | None]] = {}
        for key, period in windows.items():
            series[key] = exponential_moving_average(prices, period=period)
        self.state.moving_averages = series

    def _compute_startup_emas(self) -> dict[str, float]:
        if not self.state.prices:
            return {key: 0.0 for key in STARTUP_EMA_WINDOWS}

        result: dict[str, float] = {}
        for key, values in self.state.moving_averages.items():
            latest = values[-1] if values else None
            fallback = self.state.prices[-1].price
            result[key] = float(latest if latest is not None else fallback)
        return result

    def adjust_poll_interval(self, outcome: Literal["success", "rate_limited", "error", "skipped"]) -> None:
        now = datetime.now(UTC)
        if outcome == "rate_limited":
            if self.current_poll_interval_seconds <= 0:
                self.current_poll_interval_seconds = 1.0
            else:
                self.current_poll_interval_seconds = min(
                    self.max_backoff_seconds,
                    self.current_poll_interval_seconds * 2,
                )
            self._next_price_attempt_at = now + timedelta(seconds=self.current_poll_interval_seconds)
            return

        if outcome == "success":
            self.current_poll_interval_seconds = 0.0
            self._next_price_attempt_at = now
            return

        self._next_price_attempt_at = now

    def start_background(self) -> None:
        if self.is_running:
            return

        if not self.state.startup_complete:
            self.initialize_startup_context()

        self._stop_event.clear()

        def _loop() -> None:
            while not self._stop_event.is_set():
                price_outcome = self.run_price_cycle()
                self.adjust_poll_interval(price_outcome)
                if self.current_poll_interval_seconds > 0:
                    time.sleep(self.current_poll_interval_seconds)

        self._background_thread = threading.Thread(target=_loop, daemon=True)
        self._background_thread.start()

    def stop_background(self) -> None:
        self._stop_event.set()
        if self._background_thread and self._background_thread.is_alive():
            self._background_thread.join(timeout=1.0)

    def run_price_cycle(self) -> Literal["success", "rate_limited", "error", "skipped"]:
        def _work() -> Movement:
            return fetch_bitcoin_price()

        try:
            movement = self.price_scheduler.run_once(
                lambda: run_with_retries(_work, max_attempts=3, base_delay_seconds=1)
            )
        except requests.HTTPError as error:
            status_code = error.response.status_code if error.response is not None else None
            if status_code == 429:
                self._record_dead_letter(kind="price_rate_limited", error=str(error))
                return "rate_limited"

            self._record_dead_letter(kind="price_failure", error=str(error))
            return "error"
        except (requests.RequestException, KeyError, ValueError) as error:
            self._record_dead_letter(kind="price_failure", error=str(error))
            return "error"

        if movement is None:
            return "skipped"

        self.broker.publish_json("Prices", movement_to_json(movement))
        self.consume_prices()
        return "success"

    def _execute_jev_trade_decision(self, latest: Movement) -> None:
        outlook = self.state.market_outlook
        if not self.state.startup_complete or outlook is None:
            return

        if self._last_jev_decision_at is not None:
            elapsed = (datetime.now(UTC) - self._last_jev_decision_at).total_seconds()
            if elapsed < self.jev_interval_seconds:
                return

        decision_time = datetime.now(UTC)
        snapshot = build_jev_submission(self.state.prices)
        decision = self.jev_advisor.decide(
            snapshot=snapshot,
            outlook=outlook,
            now=decision_time,
        )
        self.state.jev_decisions.append(decision)
        self._last_jev_decision_at = decision_time

        if decision.action == "HOLD":
            return

        direction = "up" if decision.action == "BUY" else "down"
        magnitude = max(1, min(5, round(decision.confidence * 5)))
        prediction = Prediction(
            timestamp=decision.timestamp,
            direction=direction,
            magnitude=magnitude,
            rationale=decision.rationale_token,
        )
        self.state.predictions.append(prediction)
        self._open_trade = self.book.open_trade(prediction, latest)

    def consume_prices(self) -> None:
        new_movements: list[Movement] = []
        while True:
            payload = self.broker.consume_json("Prices")
            if payload is None:
                break
            movement = movement_from_json(payload)
            self.state.prices.append(movement)
            new_movements.append(movement)

        self._recompute_moving_averages()

        if not new_movements:
            return

        latest = new_movements[-1]
        if self._open_trade:
            settled = self.book.settle_trade(self._open_trade, latest)
            self.state.settled_trades.append(settled)
            self._open_trade = None

        self._execute_jev_trade_decision(latest)

    def get_dead_letters_page(self, page: int, page_size: int = 25) -> list[str]:
        if page_size <= 0:
            raise ValueError("page_size must be > 0")

        safe_page = max(page, 1)
        start = (safe_page - 1) * page_size
        end = start + page_size
        return self.state.dead_letters[start:end]

    def chart_points(self) -> dict[str, list[float] | list[float | None]]:
        prices = [point.price for point in self.state.prices]
        payload: dict[str, list[float] | list[float | None]] = {"price": prices}
        for window, series in self.state.moving_averages.items():
            payload[f"ma_{window}"] = series

        baseline = min(prices) * 0.98 if prices else 0.0
        marker_points: list[float | None] = [None] * len(prices)
        for trade in self.state.settled_trades:
            if trade.exit in self.state.prices:
                marker_points[self.state.prices.index(trade.exit)] = baseline
        payload["prediction_markers"] = marker_points
        return payload

    @property
    def paper_book_summary(self) -> dict[str, float]:
        return {
            "starting_cash": self.book.starting_cash,
            "cash_balance": self.book.cash_balance,
            "realized_pnl": self.book.realized_pnl,
        }

    def seconds_until_next_price_update(self) -> int:
        if self.current_poll_interval_seconds <= 0:
            return 0
        if self._next_price_attempt_at is None:
            return round(self.current_poll_interval_seconds)
        remaining = (self._next_price_attempt_at - datetime.now(UTC)).total_seconds()
        return max(0, int(remaining))

    def seconds_until_next_decision(self) -> int:
        if self._last_jev_decision_at is None:
            return 0
        elapsed = (datetime.now(UTC) - self._last_jev_decision_at).total_seconds()
        remaining = self.jev_interval_seconds - elapsed
        return max(0, int(remaining))
