from __future__ import annotations

import os
import logging
import sys
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
    HeuristicJevAdvisor,
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
    "12_hours": timedelta(hours=12),
    "6_hours": timedelta(hours=6),
    "1_hour": timedelta(hours=1),
    "30_mins": timedelta(minutes=30),
    "10_mins": timedelta(minutes=10),
    "5_mins": timedelta(minutes=5),
}
STARTUP_VISIBLE_HISTORY_DAYS = 30
STARTUP_PRELOAD_MULTIPLIER = 2

logger = logging.getLogger(__name__)
if not logger.handlers:
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    logger.addHandler(stdout_handler)
logger.setLevel(logging.INFO)
logger.propagate = False


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
        self._startup_replay_advisor = HeuristicJevAdvisor()
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
        self._visible_price_start_index = 0

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
                logger.info("startup step=1 message='startup begin'")
                history: list[Movement] = []
                logger.info(
                    "startup step=2 message='history preload begin' days=%s",
                    STARTUP_VISIBLE_HISTORY_DAYS * STARTUP_PRELOAD_MULTIPLIER,
                )
                history = fetch_bitcoin_price_history(
                    days=STARTUP_VISIBLE_HISTORY_DAYS * STARTUP_PRELOAD_MULTIPLIER
                )
                if history:
                    logger.info("startup step=2 message='history preload result count' points=%s", len(history))
                    self.state.prices = list(history)
                    self._visible_price_start_index = 0
                    self._recompute_moving_averages()
                else:
                    logger.warning("startup step=7 message='failure/fallback' detail='no history returned'")
                self.state.startup_emas = self._compute_startup_emas()
                logger.info("startup step=3 message='EMA computed'")

                next_halving_at = estimate_next_bitcoin_halving_datetime(now=now)
                self.state.next_halving_at = next_halving_at
                logger.info("startup step=4 message='halving estimate computed'")
                self.state.market_outlook = self.outlook_analyzer.analyze(
                    stories=[],
                    startup_emas=self.state.startup_emas,
                    next_halving_at=next_halving_at,
                    now=now,
                )
                logger.info("startup step=5 message='outlook result' value=%s", self.state.market_outlook)

                if history:
                    replay_started_at = time.perf_counter()
                    logger.info(
                        "startup step=6 message='replay begin' visible_points=%s",
                        len(history) - max(1, len(history) // 2),
                    )
                    self._replay_startup_history(history)
                    replay_elapsed_seconds = time.perf_counter() - replay_started_at
                    logger.info(
                        "startup step=6 message='replay complete' elapsed_seconds=%.3f",
                        replay_elapsed_seconds,
                    )
            except (requests.RequestException, ValueError, KeyError) as error:
                logger.exception("startup step=7 message='failure/fallback' error=%s", error)
                self._record_dead_letter(kind="startup_failure", error=str(error))
                if not self.state.prices:
                    try:
                        logger.warning("startup step=7 message='failure/fallback' detail='fallback live fetch'")
                        self.state.prices = [fetch_bitcoin_price()]
                    except requests.RequestException:
                        logger.error("startup step=7 message='failure/fallback' detail='fallback zero price'")
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
                logger.info("startup step=8 message='startup complete'")

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

    def _replay_startup_history(self, history: list[Movement]) -> None:
        if len(history) < 2:
            self.state.prices = list(history)
            self._visible_price_start_index = 0
            self._recompute_moving_averages()
            return

        split_index = max(1, len(history) // 2)
        warmup = history[:split_index]
        visible = history[split_index:]

        self.state.prices = list(warmup)
        self._visible_price_start_index = len(warmup)
        self._recompute_moving_averages()

        for movement in visible:
            self.state.prices.append(movement)
            self._recompute_moving_averages()

            if self._open_trade:
                settled = self.book.settle_trade(self._open_trade, movement)
                self.state.settled_trades.append(settled)
                self._open_trade = None

            self._execute_jev_trade_decision(
                movement,
                decision_time=movement.timestamp,
                enforce_interval=False,
                allow_during_startup=True,
                advisor_override=self._startup_replay_advisor,
            )

        if visible:
            self._last_jev_decision_at = visible[-1].timestamp

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

    def _execute_jev_trade_decision(
        self,
        latest: Movement,
        *,
        decision_time: datetime | None = None,
        enforce_interval: bool = True,
        allow_during_startup: bool = False,
        advisor_override: JevAdvisor | None = None,
    ) -> None:
        outlook = self.state.market_outlook
        if ((not self.state.startup_complete) and (not allow_during_startup)) or outlook is None:
            return

        timestamp = decision_time or datetime.now(UTC)

        if enforce_interval and self._last_jev_decision_at is not None:
            elapsed = (timestamp - self._last_jev_decision_at).total_seconds()
            if elapsed < self.jev_interval_seconds:
                return

        snapshot = build_jev_submission(self.state.prices)
        advisor = advisor_override or self.jev_advisor
        decision = advisor.decide(
            snapshot=snapshot,
            outlook=outlook,
            now=timestamp,
        )
        self.state.jev_decisions.append(decision)
        self._last_jev_decision_at = timestamp

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

        self._execute_jev_trade_decision(latest, decision_time=latest.timestamp)

    def visible_prices(self) -> list[Movement]:
        return self.state.prices[self._visible_price_start_index :]

    def get_dead_letters_page(self, page: int, page_size: int = 25) -> list[str]:
        if page_size <= 0:
            raise ValueError("page_size must be > 0")

        safe_page = max(page, 1)
        start = (safe_page - 1) * page_size
        end = start + page_size
        return self.state.dead_letters[start:end]

    def chart_points(self) -> dict[str, list[float] | list[float | None]]:
        visible_prices = self.visible_prices()
        prices = [point.price for point in visible_prices]
        payload: dict[str, list[float] | list[float | None]] = {"price": prices}
        for window, series in self.state.moving_averages.items():
            payload[f"ma_{window}"] = series[self._visible_price_start_index :]

        baseline = min(prices) * 0.98 if prices else 0.0
        marker_points: list[float | None] = [None] * len(prices)
        index_by_timestamp = {point.timestamp: idx for idx, point in enumerate(visible_prices)}
        for trade in self.state.settled_trades:
            idx = index_by_timestamp.get(trade.exit.timestamp)
            if idx is not None:
                marker_points[idx] = baseline
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
