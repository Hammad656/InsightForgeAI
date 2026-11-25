"""Sample the head of each Redis FIFO bucket used by Kombu."""

import json
from time import time

from redis import Redis

from app.core.telemetry import emit


PRIORITIES = (0, 3, 6, 9)
SEPARATOR = "\x06\x16"


def head_age_ms(message: bytes | None, now: float) -> int | None:
    if message is None or len(message) > 64 * 1024:
        return None
    try:
        published = json.loads(message)["headers"]["insightforge_published_at"]
    except (ValueError, KeyError, TypeError):
        return None
    if type(published) not in (int, float) or not 0 < published <= now:
        return None
    return round((now - published) * 1000)


def observe_queue(redis: Redis) -> None:
    with redis.pipeline(transaction=True) as pipe:
        for priority in PRIORITIES:
            key = f"celery{SEPARATOR}{priority}" if priority else "celery"
            pipe.llen(key)
            pipe.lindex(key, -1)
        values = pipe.execute()
    now = time()
    ages = []
    backlog = 0
    for index, priority in enumerate(PRIORITIES):
        depth, message = values[index * 2:index * 2 + 2]
        age_ms = head_age_ms(message, now)
        backlog += depth
        if age_ms is not None:
            ages.append(age_ms)
        emit("broker.priority", priority=priority, depth=depth, head_age_ms=age_ms)
    emit("broker.queue", default_queue_depth=backlog, oldest_head_ms=max(ages, default=None))
