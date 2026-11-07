from __future__ import annotations

import json
from collections import defaultdict, deque
from dataclasses import asdict
from datetime import datetime
from typing import Any

import pika

from .models import Movement


class InMemoryBroker:
    def __init__(self) -> None:
        self._queues: dict[str, deque[str]] = defaultdict(deque)

    def publish_json(self, queue_name: str, payload: dict[str, Any]) -> None:
        self._queues[queue_name].append(json.dumps(payload, default=str))

    def consume_json(self, queue_name: str) -> dict[str, Any] | None:
        if not self._queues[queue_name]:
            return None
        raw = self._queues[queue_name].popleft()
        return json.loads(raw)


class RabbitMQBroker:
    def __init__(self, amqp_url: str) -> None:
        self._connection = pika.BlockingConnection(pika.URLParameters(amqp_url))
        self._channel = self._connection.channel()

    def publish_json(self, queue_name: str, payload: dict[str, Any]) -> None:
        self._channel.queue_declare(queue=queue_name, durable=True)
        self._channel.basic_publish(
            exchange="",
            routing_key=queue_name,
            body=json.dumps(payload, default=str).encode("utf-8"),
        )

    def consume_json(self, queue_name: str) -> dict[str, Any] | None:
        self._channel.queue_declare(queue=queue_name, durable=True)
        method_frame, _, body = self._channel.basic_get(queue=queue_name, auto_ack=True)
        if method_frame is None:
            return None
        assert body is not None
        return json.loads(body.decode("utf-8"))

    def close(self) -> None:
        self._connection.close()


def movement_to_json(movement: Movement) -> dict[str, Any]:
    payload = asdict(movement)
    payload["timestamp"] = movement.timestamp.isoformat()
    return payload


def movement_from_json(payload: dict[str, Any]) -> Movement:
    return Movement(
        timestamp=datetime.fromisoformat(payload["timestamp"]),
        price=float(payload["price"]),
    )
