from datetime import UTC, datetime, timedelta

import pytest
import requests

from event_agent.indicators import moving_average_series
from event_agent.models import Movement, Prediction
from event_agent.scheduler import run_with_retries
from event_agent.trading import PaperBook


class _RetryError(RuntimeError):
    pass


def test_moving_average_series_for_window():
    prices = [100.0, 110.0, 120.0, 130.0]
    values = moving_average_series(prices, window=3)

    assert values == [None, None, 110.0, 120.0]


@pytest.mark.parametrize(
    ("magnitude", "expected_notional"),
    [
        (1, 100.0),
        (2, 1000.0),
        (3, 10000.0),
        (4, 100000.0),
        (5, 1000000.0),
    ],
)
def test_trade_notional_matches_exponential_scale(magnitude: int, expected_notional: float):
    prediction = Prediction(
        timestamp=datetime.now(UTC),
        direction="up",
        magnitude=magnitude,
        rationale="x",
    )
    assert PaperBook.trade_notional(prediction) == expected_notional


def test_settlement_updates_pnl_and_balance():
    book = PaperBook(starting_cash=100000.0)
    now = datetime.now(UTC)
    prediction = Prediction(timestamp=now, direction="up", magnitude=1, rationale="")
    entry = Movement(timestamp=now, price=100.0)
    exit_move = Movement(timestamp=now + timedelta(seconds=10), price=110.0)

    trade = book.open_trade(prediction, entry)
    settled = book.settle_trade(trade, exit_move)

    assert settled.pnl == pytest.approx(10.0)
    assert settled.correct_direction is True
    assert book.realized_pnl == pytest.approx(10.0)
    assert book.cash_balance == pytest.approx(100010.0)


def test_settlement_marks_flat_when_entry_and_exit_are_equal():
    book = PaperBook(starting_cash=100000.0)
    now = datetime.now(UTC)
    prediction = Prediction(timestamp=now, direction="down", magnitude=1, rationale="")
    entry = Movement(timestamp=now, price=100.0)
    exit_move = Movement(timestamp=now + timedelta(seconds=10), price=100.0)

    trade = book.open_trade(prediction, entry)
    settled = book.settle_trade(trade, exit_move)

    assert settled.pnl == pytest.approx(0.0)
    assert settled.correct_direction is None


def test_retry_uses_exponential_backoff_and_max_attempts():
    attempts = {"count": 0}
    waits: list[int] = []

    def _work() -> int:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise _RetryError("nope")
        return 42

    result = run_with_retries(
        operation=_work,
        max_attempts=3,
        sleep_func=waits.append,
        base_delay_seconds=1,
        exception_types=(_RetryError,),
    )

    assert result == 42
    assert attempts["count"] == 3
    assert waits == [1, 2]


def test_engine_background_loop_can_start_and_stop(monkeypatch: pytest.MonkeyPatch):
    from event_agent.engine import AutomationEngine

    engine = AutomationEngine()
    calls = {"price": 0}

    monkeypatch.setattr(engine, "initialize_startup_context", lambda: None)

    def _cycle() -> str:
        calls["price"] += 1
        return "success"

    monkeypatch.setattr(engine, "run_price_cycle", _cycle)

    engine.start_background()

    import time

    time.sleep(0.12)
    engine.stop_background()

    assert calls["price"] >= 1
    assert engine.is_running is False


def test_poll_interval_backs_off_on_rate_limit_and_recovers():
    from event_agent.engine import AutomationEngine

    engine = AutomationEngine()

    assert engine.current_poll_interval_seconds == 0.0

    engine.adjust_poll_interval("rate_limited")
    assert engine.current_poll_interval_seconds == 1.0
    assert engine.seconds_until_next_price_update() <= 1

    engine.adjust_poll_interval("rate_limited")
    assert engine.current_poll_interval_seconds == 2.0
    assert engine.seconds_until_next_price_update() <= 2

    engine.adjust_poll_interval("success")
    assert engine.current_poll_interval_seconds == 0.0
    assert engine.seconds_until_next_price_update() == 0


def test_dead_letter_pagination_uses_25_items_per_page():
    from event_agent.engine import AutomationEngine

    engine = AutomationEngine()
    engine.state.dead_letters = [f"error-{i}" for i in range(1, 61)]

    page_1 = engine.get_dead_letters_page(page=1, page_size=25)
    page_2 = engine.get_dead_letters_page(page=2, page_size=25)
    page_3 = engine.get_dead_letters_page(page=3, page_size=25)

    assert len(page_1) == 25
    assert page_1[0] == "error-1"
    assert page_1[-1] == "error-25"

    assert len(page_2) == 25
    assert page_2[0] == "error-26"
    assert page_2[-1] == "error-50"

    assert len(page_3) == 10
    assert page_3[0] == "error-51"
    assert page_3[-1] == "error-60"


def test_jev_decision_runs_at_most_once_per_two_minutes(monkeypatch: pytest.MonkeyPatch):
    from event_agent.engine import AutomationEngine
    from event_agent.models import JevDecision

    class _OutlookAnalyzer:
        def analyze(self, *, stories, startup_emas, next_halving_at, now):
            return "bullish"

    class _JevAdvisor:
        def __init__(self):
            self.calls = 0

        def decide(self, *, snapshot, outlook, now):
            self.calls += 1
            return JevDecision(
                timestamp=now,
                action="HOLD",
                confidence=0.5,
                rationale_token="interval_gate",
                outlook=outlook,
            )

    advisor = _JevAdvisor()
    engine = AutomationEngine(outlook_analyzer=_OutlookAnalyzer(), jev_advisor=advisor)
    engine.state.startup_complete = True
    engine.state.market_outlook = "bullish"
    engine.state.prices = [
        Movement(timestamp=datetime(2026, 10, 1, 0, 0, tzinfo=UTC), price=50000.0),
    ]

    first = Movement(timestamp=datetime(2026, 10, 1, 0, 1, tzinfo=UTC), price=50010.0)
    second = Movement(timestamp=datetime(2026, 10, 1, 0, 2, 30, tzinfo=UTC), price=50020.0)

    engine._execute_jev_trade_decision(first)
    engine._execute_jev_trade_decision(second)

    assert advisor.calls == 1
    assert 0 <= engine.seconds_until_next_decision() <= int(engine.jev_interval_seconds)


def test_phase2_startup_initialization_builds_outlook_and_ema_windows(monkeypatch: pytest.MonkeyPatch):
    from event_agent.engine import AutomationEngine

    class _OutlookAnalyzer:
        def analyze(self, *, stories, startup_emas, next_halving_at, now):
            return "bullish"

    class _JevAdvisor:
        def decide(self, *, snapshot, outlook, now):
            raise AssertionError("not used in startup test")

    base = datetime(2026, 10, 1, tzinfo=UTC)
    history = [
        Movement(timestamp=base + timedelta(hours=idx), price=50000.0 + idx)
        for idx in range(24 * 30)
    ]

    monkeypatch.setattr("event_agent.engine.fetch_bitcoin_price_history", lambda days=30: history)
    monkeypatch.setattr(
        "event_agent.engine.estimate_next_bitcoin_halving_datetime",
        lambda now=None: datetime(2028, 4, 20, tzinfo=UTC),
    )

    engine = AutomationEngine(outlook_analyzer=_OutlookAnalyzer(), jev_advisor=_JevAdvisor())
    engine.initialize_startup_context()

    assert engine.state.startup_complete is True
    assert engine.state.market_outlook == "bullish"
    assert set(engine.state.startup_emas) == {
        "12_hours",
        "6_hours",
        "1_hour",
        "30_mins",
        "10_mins",
        "5_mins",
    }


def test_phase2_new_price_uses_jev_decision_for_paper_trade(monkeypatch: pytest.MonkeyPatch):
    from event_agent.engine import AutomationEngine
    from event_agent.models import JevDecision

    class _OutlookAnalyzer:
        def analyze(self, *, stories, startup_emas, next_halving_at, now):
            return "bullish"

    class _JevAdvisor:
        def decide(self, *, snapshot, outlook, now):
            assert outlook == "bullish"
            return JevDecision(
                timestamp=now,
                action="BUY",
                confidence=0.89,
                rationale_token="rsi_exhaustion",
                outlook=outlook,
            )

    base = datetime(2026, 10, 1, tzinfo=UTC)
    history = [
        Movement(timestamp=base + timedelta(hours=idx), price=50000.0 + idx)
        for idx in range(24 * 30)
    ]

    monkeypatch.setattr("event_agent.engine.fetch_bitcoin_price_history", lambda days=30: history)
    monkeypatch.setattr(
        "event_agent.engine.estimate_next_bitcoin_halving_datetime",
        lambda now=None: datetime(2028, 4, 20, tzinfo=UTC),
    )

    engine = AutomationEngine(outlook_analyzer=_OutlookAnalyzer(), jev_advisor=_JevAdvisor())
    engine.initialize_startup_context()

    next_movement = Movement(timestamp=base + timedelta(hours=(24 * 30) + 1), price=55000.0)
    monkeypatch.setattr("event_agent.engine.fetch_bitcoin_price", lambda: next_movement)

    outcome = engine.run_price_cycle()

    assert outcome == "success"
    assert engine.state.jev_decisions[-1].action == "BUY"
    assert engine.state.predictions[-1].direction == "up"
    assert engine.state.predictions[-1].magnitude == 4
    assert engine.open_trade is not None


def test_openrouter_jev_advisor_uses_decisions_endpoint(monkeypatch: pytest.MonkeyPatch):
    from event_agent.prediction import OpenRouterJevAdvisor

    captured: dict[str, object] = {}

    class _Response:
        def raise_for_status(self) -> None:
            return

        def json(self) -> dict[str, object]:
            return {
                "answers": {
                    "action": {"choice": "BUY"},
                    "confidence": {"score": 3},
                    "rationale_token": {"choice": "ema_structure"},
                }
            }

    def _fake_post(url: str, *, json, headers, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr("event_agent.prediction.requests.post", _fake_post)

    advisor = OpenRouterJevAdvisor(api_key="dummy-key", model="typesafe/jev-1.13")
    decision = advisor.decide(
        snapshot={"trend": {"ema_cross": "BULLISH_CROSS"}},
        outlook="bullish",
        now=datetime(2026, 11, 7, tzinfo=UTC),
    )

    assert captured["url"] == "https://openrouter.ai/api/alpha/decisions"
    assert captured["json"]["model"] == "typesafe/jev-1.13"
    assert captured["headers"]["Authorization"] == "Bearer dummy-key"
    assert decision.action == "BUY"
    assert decision.confidence == pytest.approx(0.75)
    assert decision.rationale_token == "ema_structure"


def test_ollama_jev_advisor_normalizes_host_and_retries(monkeypatch: pytest.MonkeyPatch):
    from event_agent.prediction import OllamaJevAdvisor

    captured: dict[str, object] = {}
    attempts = {"count": 0}
    sleeps: list[int] = []

    class _Response:
        def raise_for_status(self) -> None:
            return

        def json(self) -> dict[str, object]:
            return {
                "response": '{"action":"BUY","confidence":0.8,"rationale_token":"ema_structure"}'
            }

    def _fake_post(url: str, *, json, timeout):
        attempts["count"] += 1
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        if attempts["count"] < 3:
            raise requests.ConnectionError("ollama not ready")
        return _Response()

    monkeypatch.setattr("event_agent.prediction.requests.post", _fake_post)

    advisor = OllamaJevAdvisor(
        host="0.0.0.0:11434",
        model="llama3.2:3b",
        sleep_func=sleeps.append,
    )

    decision = advisor.decide(
        snapshot={"trend": {"ema_cross": "BULLISH_CROSS"}},
        outlook="bullish",
        now=datetime(2026, 11, 7, tzinfo=UTC),
    )

    assert attempts["count"] == 3
    assert sleeps == [1, 2]
    assert captured["url"] == "http://0.0.0.0:11434/api/generate"
    assert captured["json"]["model"] == "llama3.2:3b"
    assert captured["timeout"] == 60
    assert decision.action == "BUY"
    assert decision.confidence == pytest.approx(0.8)
    assert decision.rationale_token == "ema_structure"


def test_ollama_outlook_analyzer_uses_same_retry_wrapper(monkeypatch: pytest.MonkeyPatch):
    from event_agent.models import NewsItem
    from event_agent.prediction import OllamaOutlookAnalyzer

    attempts = {"count": 0}
    sleeps: list[int] = []
    captured: dict[str, object] = {}

    class _Response:
        def raise_for_status(self) -> None:
            return

        def json(self) -> dict[str, object]:
            return {"response": '{"outlook":"bearish"}'}

    def _fake_post(url: str, *, json, timeout):
        attempts["count"] += 1
        captured["json"] = json
        captured["timeout"] = timeout
        if attempts["count"] == 1:
            raise requests.Timeout("slow startup")
        return _Response()

    monkeypatch.setattr("event_agent.prediction.requests.post", _fake_post)

    analyzer = OllamaOutlookAnalyzer(
        host="http://localhost:11434",
        model="llama3.2:3b",
        sleep_func=sleeps.append,
    )

    outlook = analyzer.analyze(
        stories=[
            NewsItem(
                source="bbc",
                story_id="story-1",
                title="Risk rises",
                summary="decline signs",
                published_at=datetime(2026, 11, 7, tzinfo=UTC),
            )
        ],
        startup_emas={"1_day": 1.0, "1_month": 2.0},
        next_halving_at=datetime(2028, 4, 20, tzinfo=UTC),
        now=datetime(2026, 11, 7, tzinfo=UTC),
    )

    assert attempts["count"] == 2
    assert sleeps == [1]
    assert captured["timeout"] == 60
    assert captured["json"]["options"] == {
        "num_gpu": -1,
        "num_ctx": 4096,
        "low_vram": False,
    }
    assert outlook == "bearish"


def test_ollama_first_call_retries_until_warm(monkeypatch: pytest.MonkeyPatch):
    from event_agent.prediction import OllamaJevAdvisor

    attempts = {"count": 0}
    sleeps: list[int] = []

    class _Response:
        def raise_for_status(self) -> None:
            return

        def json(self) -> dict[str, object]:
            return {
                "response": '{"action":"HOLD","confidence":0.5,"rationale_token":"no_edge"}'
            }

    def _fake_post(url: str, *, json, timeout):
        attempts["count"] += 1
        if attempts["count"] < 5:
            raise requests.Timeout("model still loading")
        return _Response()

    monkeypatch.setattr("event_agent.prediction.requests.post", _fake_post)

    advisor = OllamaJevAdvisor(
        host="http://localhost:11434",
        model="gemma4:12b",
        sleep_func=sleeps.append,
    )
    decision = advisor.decide(
        snapshot={"trend": {"ema_cross": "BULLISH_CROSS"}},
        outlook="bullish",
        now=datetime(2026, 11, 7, tzinfo=UTC),
    )

    assert attempts["count"] == 5
    assert sleeps == [1, 2, 4, 8]
    assert decision.action == "HOLD"


def test_ollama_jev_advisor_handles_non_json_output(monkeypatch: pytest.MonkeyPatch):
    from event_agent.prediction import OllamaJevAdvisor

    class _Response:
        def raise_for_status(self) -> None:
            return

        def json(self) -> dict[str, object]:
            return {
                "response": "I think BUY here. momentum is strong"
            }

    def _fake_post(url: str, *, json, timeout):
        return _Response()

    monkeypatch.setattr("event_agent.prediction.requests.post", _fake_post)

    advisor = OllamaJevAdvisor(
        host="http://localhost:11434",
        model="gemma4:e2b",
        sleep_func=lambda _: None,
    )
    decision = advisor.decide(
        snapshot={"trend": {"ema_cross": "BULLISH_CROSS"}},
        outlook="bullish",
        now=datetime(2026, 11, 7, tzinfo=UTC),
    )

    assert decision.action == "BUY"
    assert decision.confidence == pytest.approx(0.5)
    assert decision.rationale_token == "no_edge"


def test_ollama_outlook_analyzer_handles_non_json_output(monkeypatch: pytest.MonkeyPatch):
    from event_agent.models import NewsItem
    from event_agent.prediction import OllamaOutlookAnalyzer

    class _Response:
        def raise_for_status(self) -> None:
            return

        def json(self) -> dict[str, object]:
            return {"response": "Market sentiment looks bearish overall today."}

    def _fake_post(url: str, *, json, timeout):
        return _Response()

    monkeypatch.setattr("event_agent.prediction.requests.post", _fake_post)

    analyzer = OllamaOutlookAnalyzer(
        host="http://localhost:11434",
        model="gemma4:12b",
        sleep_func=lambda _: None,
    )

    outlook = analyzer.analyze(
        stories=[
            NewsItem(
                source="bbc",
                story_id="story-1",
                title="Risk rises",
                summary="decline signs",
                published_at=datetime(2026, 11, 7, tzinfo=UTC),
            )
        ],
        startup_emas={"1_day": 1.0, "1_month": 2.0},
        next_halving_at=datetime(2028, 4, 20, tzinfo=UTC),
        now=datetime(2026, 11, 7, tzinfo=UTC),
    )

    assert outlook == "bearish"


def test_build_outlook_analyzer_uses_system_two_model(monkeypatch: pytest.MonkeyPatch):
    from event_agent.prediction import OllamaOutlookAnalyzer, build_outlook_analyzer

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("USE_OLLAMA", "1")
    monkeypatch.setenv("OLLAMA_HOST", "host.docker.internal:11434")
    monkeypatch.setenv("SYSTEM_TWO_MODEL", "gemma4:12b")
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)

    analyzer = build_outlook_analyzer()

    assert isinstance(analyzer, OllamaOutlookAnalyzer)
    assert analyzer._client._host == "http://host.docker.internal:11434"
    assert analyzer._client._model == "gemma4:12b"


def test_build_jev_advisor_uses_system_one_model(monkeypatch: pytest.MonkeyPatch):
    from event_agent.prediction import OllamaJevAdvisor, build_jev_advisor

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("USE_OLLAMA", "1")
    monkeypatch.setenv("OLLAMA_HOST", "host.docker.internal:11434")
    monkeypatch.setenv("SYSTEM_ONE_MODEL", "gemma4:e2b")
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)

    advisor = build_jev_advisor()

    assert isinstance(advisor, OllamaJevAdvisor)
    assert advisor._client._host == "http://host.docker.internal:11434"
    assert advisor._client._model == "gemma4:e2b"


def test_build_outlook_analyzer_openrouter_uses_system_two_model(monkeypatch: pytest.MonkeyPatch):
    from event_agent.prediction import OpenRouterOutlookAnalyzer, build_outlook_analyzer

    monkeypatch.setenv("USE_OLLAMA", "0")
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy-key")
    monkeypatch.setenv("SYSTEM_TWO_MODEL", "openai/gpt-5-mini")

    analyzer = build_outlook_analyzer()

    assert isinstance(analyzer, OpenRouterOutlookAnalyzer)
    assert analyzer.model == "openai/gpt-5-mini"


def test_build_jev_advisor_openrouter_uses_system_one_model(monkeypatch: pytest.MonkeyPatch):
    from event_agent.prediction import OpenRouterJevAdvisor, build_jev_advisor

    monkeypatch.setenv("USE_OLLAMA", "0")
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy-key")
    monkeypatch.setenv("SYSTEM_ONE_MODEL", "typesafe/jev-2")

    advisor = build_jev_advisor()

    assert isinstance(advisor, OpenRouterJevAdvisor)
    assert advisor.model == "typesafe/jev-2"
