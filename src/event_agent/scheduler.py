from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import TypeVar

ResultT = TypeVar("ResultT")


class NonOverlappingScheduler:
    def __init__(self) -> None:
        self._lock = threading.Lock()

    def run_once(self, operation: Callable[[], ResultT]) -> ResultT | None:
        if not self._lock.acquire(blocking=False):
            return None

        try:
            return operation()
        finally:
            self._lock.release()


def run_with_retries(
    operation: Callable[[], ResultT],
    max_attempts: int,
    sleep_func: Callable[[int], None] = time.sleep,
    base_delay_seconds: int = 1,
    exception_types: tuple[type[Exception], ...] = (Exception,),
) -> ResultT:
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")

    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return operation()
        except exception_types as error:
            last_error = error
            if attempt >= max_attempts:
                break
            delay = base_delay_seconds * (2 ** (attempt - 1))
            sleep_func(delay)

    assert last_error is not None
    raise last_error
