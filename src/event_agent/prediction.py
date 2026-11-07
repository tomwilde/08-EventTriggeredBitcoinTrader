from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, cast

import requests
from openai import OpenAI

from .models import JevDecision, NewsItem, Outlook

BULLISH_SYSTEM_PROMPT = (
    "Focus on buying support levels. Strongly penalise selling unless a major liquidation wick occurs."
)
BEARISH_SYSTEM_PROMPT = "Focus on selling resistance. Do not chase breakout wicks."


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


def build_outlook_analyzer() -> OutlookAnalyzer:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if api_key:
        return OpenRouterOutlookAnalyzer(api_key=api_key)
    return HeuristicOutlookAnalyzer()


def build_jev_advisor() -> JevAdvisor:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if api_key:
        return OpenRouterJevAdvisor(api_key=api_key)
    return HeuristicJevAdvisor()
