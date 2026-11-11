from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from typing import Callable
from typing import Any, cast

import requests
from openai import OpenAI

from .models import JevDecision, NewsItem, Outlook
from .scheduler import run_with_retries

BULLISH_SYSTEM_PROMPT = (
    "Focus on buying support levels. Strongly penalise selling unless a major liquidation wick occurs."
)
BEARISH_SYSTEM_PROMPT = "Focus on selling resistance. Do not chase breakout wicks."
OLLAMA_DEFAULT_HOST = "http://localhost:11434"
OLLAMA_DEFAULT_MODEL = "llama3.2:3b"
SYSTEM_ONE_DEFAULT_MODEL = "gemma4:e2b"
SYSTEM_TWO_DEFAULT_MODEL = "gemma4:12b"
SYSTEM_TWO_OLLAMA_OPTIONS: dict[str, int | bool] = {
    "num_gpu": -1,
    "num_ctx": 4096,
    "low_vram": False,
}


class OllamaClient:
    def __init__(
        self,
        *,
        host: str,
        model: str,
        timeout_seconds: int = 60,
        max_attempts: int = 3,
        base_delay_seconds: int = 1,
        sleep_func: Callable[[int], None] = time.sleep,
    ) -> None:
        self._host = self._normalize_host(host)
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._max_attempts = max_attempts
        self._base_delay_seconds = base_delay_seconds
        self._sleep_func = sleep_func
        self._is_warmed_up = False

    @staticmethod
    def _normalize_host(host: str) -> str:
        normalized = host.strip().rstrip("/")
        if normalized.startswith("http://") or normalized.startswith("https://"):
            return normalized
        return f"http://{normalized}"

    def _generate_once(
        self,
        *,
        prompt: str,
        system: str,
        options: dict[str, int | bool] | None = None,
    ) -> str:
        request_options = options or {"num_gpu": 0}
        response = requests.post(
            f"{self._host}/api/generate",
            json={
                "model": self._model,
                "prompt": prompt,
                "system": system,
                "stream": False,
                "options": request_options,
            },
            timeout=self._timeout_seconds,
        )
        response.raise_for_status()
        body = response.json()
        generated = body.get("response") if isinstance(body, dict) else None
        if not isinstance(generated, str) or not generated.strip():
            raise ValueError("Ollama response did not contain generated text")
        return generated

    def generate(
        self,
        *,
        prompt: str,
        system: str,
        options: dict[str, int | bool] | None = None,
    ) -> str:
        if not self._is_warmed_up:
            delay = self._base_delay_seconds
            while True:
                try:
                    generated = self._generate_once(prompt=prompt, system=system, options=options)
                    self._is_warmed_up = True
                    return generated
                except requests.RequestException:
                    self._sleep_func(delay)
                    delay *= 2

        def _work() -> str:
            return self._generate_once(prompt=prompt, system=system, options=options)

        return run_with_retries(
            _work,
            max_attempts=self._max_attempts,
            sleep_func=self._sleep_func,
            base_delay_seconds=self._base_delay_seconds,
            exception_types=(requests.RequestException, ValueError),
        )


class OutlookAnalyzer:
    def analyze(
        self,
        *,
        stories: list[NewsItem],
        startup_emas: dict[str, float],
        next_halving_at: datetime,
        now: datetime,
    ) -> Outlook:
        raise NotImplementedError


class HeuristicOutlookAnalyzer(OutlookAnalyzer):
    def analyze(
        self,
        *,
        stories: list[NewsItem],
        startup_emas: dict[str, float],
        next_halving_at: datetime,
        now: datetime,
    ) -> Outlook:
        short = startup_emas.get("1_day", 0.0)
        long = startup_emas.get("1_month", 0.0)
        score = 1 if short >= long else -1

        if (next_halving_at - now).days <= 365:
            score += 1

        for story in stories:
            text = f"{story.title} {story.summary}".lower()
            if any(word in text for word in ["rally", "surge", "record", "gain", "optimism"]):
                score += 1
            if any(word in text for word in ["risk", "fall", "decline", "lawsuit", "crash"]):
                score -= 1

        return "bullish" if score >= 0 else "bearish"


class OpenRouterOutlookAnalyzer(OutlookAnalyzer):
    def __init__(self, api_key: str, model: str = "openai/gpt-4.1-mini") -> None:
        self.model = model
        self._client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)

    def analyze(
        self,
        *,
        stories: list[NewsItem],
        startup_emas: dict[str, float],
        next_halving_at: datetime,
        now: datetime,
    ) -> Outlook:
        response_format: dict[str, Any] = {
            "type": "json_schema",
            "json_schema": {
                "name": "market_outlook",
                "schema": {
                    "type": "object",
                    "properties": {
                        "outlook": {"type": "string", "enum": ["bullish", "bearish"]},
                    },
                    "required": ["outlook"],
                    "additionalProperties": False,
                },
            },
        }

        story_block = "\n".join(f"- {item.title}: {item.summary}" for item in stories)
        prompt = (
            "Analyze BTC context and pick an outlook. "
            f"Today: {now.isoformat()}\n"
            f"Next halving estimate: {next_halving_at.isoformat()}\n"
            f"EMA summary: {startup_emas}\n"
            f"BBC Business stories:\n{story_block}"
        )

        completion = self._client.chat.completions.create(
            model=self.model,
            response_format=cast(Any, response_format),
            messages=[
                {"role": "system", "content": "Return strict JSON."},
                {"role": "user", "content": prompt},
            ],
        )
        payload = json.loads(completion.choices[0].message.content or "{}")
        return cast(Outlook, payload["outlook"])


class OllamaOutlookAnalyzer(OutlookAnalyzer):
    def __init__(
        self,
        host: str,
        model: str,
        sleep_func: Callable[[int], None] = time.sleep,
    ) -> None:
        self._client = OllamaClient(host=host, model=model, sleep_func=sleep_func)

    @staticmethod
    def _parse_outlook(generated: str) -> Outlook:
        cleaned = generated.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            if len(lines) >= 2 and lines[-1].strip().startswith("```"):
                cleaned = "\n".join(lines[1:-1]).strip()

        try:
            payload = json.loads(cleaned)
            if isinstance(payload, dict):
                outlook = payload.get("outlook")
                if outlook in {"bullish", "bearish"}:
                    return cast(Outlook, outlook)
        except json.JSONDecodeError:
            match = re.search(r"\{[\s\S]*\}", cleaned)
            if match is not None:
                try:
                    payload = json.loads(match.group(0))
                    if isinstance(payload, dict):
                        outlook = payload.get("outlook")
                        if outlook in {"bullish", "bearish"}:
                            return cast(Outlook, outlook)
                except json.JSONDecodeError:
                    pass

        lowered = cleaned.lower()
        if "bearish" in lowered:
            return "bearish"
        if "bullish" in lowered:
            return "bullish"
        return "bullish"

    def analyze(
        self,
        *,
        stories: list[NewsItem],
        startup_emas: dict[str, float],
        next_halving_at: datetime,
        now: datetime,
    ) -> Outlook:
        story_block = "\n".join(f"- {item.title}: {item.summary}" for item in stories)
        prompt = (
            "Analyze BTC context and choose market outlook. Return strict JSON with one key: outlook.\n"
            f"Allowed outlook values: bullish or bearish.\n"
            f"Today: {now.isoformat()}\n"
            f"Next halving estimate: {next_halving_at.isoformat()}\n"
            f"EMA summary: {startup_emas}\n"
            f"BBC Business stories:\n{story_block}"
        )
        generated = self._client.generate(
            system="Return only valid JSON with no prose.",
            prompt=prompt,
            options=SYSTEM_TWO_OLLAMA_OPTIONS,
        )
        return self._parse_outlook(generated)


class JevAdvisor:
    def decide(
        self,
        *,
        snapshot: dict[str, Any],
        outlook: Outlook,
        now: datetime,
    ) -> JevDecision:
        raise NotImplementedError


class HeuristicJevAdvisor(JevAdvisor):
    def decide(
        self,
        *,
        snapshot: dict[str, Any],
        outlook: Outlook,
        now: datetime,
    ) -> JevDecision:
        trend = snapshot.get("trend", {})
        momentum = snapshot.get("momentum", {})
        ema_cross = trend.get("ema_cross", "")
        rsi = float(momentum.get("rsi_14", 50.0))

        if ema_cross == "BULLISH_CROSS" and rsi < 75:
            action = "BUY"
            confidence = 0.75
            rationale = "ema_rsi_alignment"
        elif ema_cross == "BEARISH_CROSS" and rsi > 25:
            action = "SELL"
            confidence = 0.75
            rationale = "ema_rsi_alignment"
        else:
            action = "HOLD"
            confidence = 0.6
            rationale = "no_edge"

        if outlook == "bullish" and action == "SELL":
            action = "HOLD"
            confidence = 0.55
            rationale = "bullish_filter"
        if outlook == "bearish" and action == "BUY":
            action = "HOLD"
            confidence = 0.55
            rationale = "bearish_filter"

        return JevDecision(
            timestamp=now,
            action=cast(Any, action),
            confidence=confidence,
            rationale_token=rationale,
            outlook=outlook,
        )


class OpenRouterJevAdvisor(JevAdvisor):
    DECISIONS_ENDPOINT = "https://openrouter.ai/api/alpha/decisions"

    def __init__(self, api_key: str, model: str = "typesafe/jev-1.13") -> None:
        self.model = model
        self._api_key = api_key

    @staticmethod
    def _system_prompt(outlook: Outlook) -> str:
        return BULLISH_SYSTEM_PROMPT if outlook == "bullish" else BEARISH_SYSTEM_PROMPT

    def decide(
        self,
        *,
        snapshot: dict[str, Any],
        outlook: Outlook,
        now: datetime,
    ) -> JevDecision:
        payload: dict[str, Any] = {
            "model": self.model,
            "questions": {
                "action": {
                    "type": "choice",
                    "instructions": "Pick the best BTC trading action now.",
                    "criteria": {
                        "BUY": "Take a long-biased position now.",
                        "SELL": "Take a short-biased position now.",
                        "HOLD": "No clear edge right now.",
                    },
                },
                "confidence": {
                    "type": "score",
                    "instructions": "How strong is this action signal?",
                    "criteria": [
                        "Very low conviction",
                        "Low conviction",
                        "Moderate conviction",
                        "High conviction",
                        "Very high conviction",
                    ],
                },
                "rationale_token": {
                    "type": "choice",
                    "instructions": "Select a concise rationale token.",
                    "criteria": {
                        "trend_alignment": "Trend and structure align with the action.",
                        "rsi_exhaustion": "Momentum exhaustion drives the action.",
                        "volatility_risk": "Volatility/wick risk dominates decision.",
                        "ema_structure": "EMA positioning or crossover is decisive.",
                        "no_edge": "No meaningful probabilistic edge.",
                    },
                },
            },
            "state": {
                "system_prompt": self._system_prompt(outlook),
                "snapshot": snapshot,
            },
        }

        response = requests.post(
            self.DECISIONS_ENDPOINT,
            json=payload,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            timeout=30,
        )
        response.raise_for_status()
        body = response.json()
        answers = body.get("answers", {})

        action = answers.get("action", {}).get("choice", "HOLD")
        score_value = answers.get("confidence", {}).get("score", 2.0)
        score = float(score_value)
        confidence = max(0.0, min(1.0, score / 4.0 if score > 1 else score))
        rationale = answers.get("rationale_token", {}).get("choice", "no_edge")

        return JevDecision(
            timestamp=now,
            action=action,
            confidence=confidence,
            rationale_token=rationale,
            outlook=outlook,
        )


class OllamaJevAdvisor(JevAdvisor):
    def __init__(
        self,
        host: str,
        model: str,
        sleep_func: Callable[[int], None] = time.sleep,
    ) -> None:
        self._client = OllamaClient(host=host, model=model, sleep_func=sleep_func)

    @staticmethod
    def _system_prompt(outlook: Outlook) -> str:
        return BULLISH_SYSTEM_PROMPT if outlook == "bullish" else BEARISH_SYSTEM_PROMPT

    @staticmethod
    def _parse_payload(generated: str) -> dict[str, Any]:
        cleaned = generated.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            if len(lines) >= 2 and lines[-1].strip().startswith("```"):
                cleaned = "\n".join(lines[1:-1]).strip()

        try:
            parsed = json.loads(cleaned)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            match = re.search(r"\{[\s\S]*\}", cleaned)
            if match is None:
                return {}
            try:
                parsed = json.loads(match.group(0))
                return parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                return {}

    def decide(
        self,
        *,
        snapshot: dict[str, Any],
        outlook: Outlook,
        now: datetime,
    ) -> JevDecision:
        prompt = (
            "Pick the best BTC action now and return JSON only.\n"
            'Format: {"action":"BUY|SELL|HOLD","confidence":0_to_1_float,"rationale_token":"token"}.\n'
            "Use concise rationale tokens like trend_alignment, rsi_exhaustion, volatility_risk, "
            "ema_structure, or no_edge.\n"
            f"Snapshot: {json.dumps(snapshot, separators=(',', ':'))}"
        )
        generated = self._client.generate(
            system=self._system_prompt(outlook),
            prompt=prompt,
        )
        payload = self._parse_payload(generated)

        action = payload.get("action", "HOLD")
        if not payload:
            upper = generated.upper()
            if "BUY" in upper:
                action = "BUY"
            elif "SELL" in upper:
                action = "SELL"
        if action not in {"BUY", "SELL", "HOLD"}:
            action = "HOLD"
        try:
            confidence = float(payload.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        confidence = max(0.0, min(1.0, confidence))
        rationale = payload.get("rationale_token", "no_edge")
        rationale_token = rationale if isinstance(rationale, str) and rationale else "no_edge"

        return JevDecision(
            timestamp=now,
            action=cast(Any, action),
            confidence=confidence,
            rationale_token=rationale_token,
            outlook=outlook,
        )


def build_outlook_analyzer() -> OutlookAnalyzer:
    if os.getenv("USE_OLLAMA", "0") != "0":
        host = os.getenv("OLLAMA_HOST", OLLAMA_DEFAULT_HOST)
        model = (
            os.getenv("SYSTEM_TWO_MODEL")
            or os.getenv("OLLAMA_MODEL")
            or SYSTEM_TWO_DEFAULT_MODEL
        )
        return OllamaOutlookAnalyzer(host=host, model=model)

    api_key = os.getenv("OPENROUTER_API_KEY")
    if api_key:
        model = os.getenv("SYSTEM_TWO_MODEL") or "openai/gpt-4.1-mini"
        return OpenRouterOutlookAnalyzer(api_key=api_key, model=model)

    ollama_model = os.getenv("OLLAMA_MODEL")
    if ollama_model:
        host = os.getenv("OLLAMA_HOST", OLLAMA_DEFAULT_HOST)
        return OllamaOutlookAnalyzer(host=host, model=ollama_model)
    return HeuristicOutlookAnalyzer()


def build_jev_advisor() -> JevAdvisor:
    if os.getenv("USE_OLLAMA", "0") != "0":
        host = os.getenv("OLLAMA_HOST", OLLAMA_DEFAULT_HOST)
        model = (
            os.getenv("SYSTEM_ONE_MODEL")
            or os.getenv("OLLAMA_MODEL")
            or SYSTEM_ONE_DEFAULT_MODEL
        )
        return OllamaJevAdvisor(host=host, model=model)

    api_key = os.getenv("OPENROUTER_API_KEY")
    if api_key:
        model = os.getenv("SYSTEM_ONE_MODEL") or "typesafe/jev-1.13"
        return OpenRouterJevAdvisor(api_key=api_key, model=model)

    ollama_model = os.getenv("OLLAMA_MODEL")
    if ollama_model:
        host = os.getenv("OLLAMA_HOST", OLLAMA_DEFAULT_HOST)
        return OllamaJevAdvisor(host=host, model=ollama_model)
    return HeuristicJevAdvisor()
