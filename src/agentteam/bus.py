"""Message bus: real-time exchange between the agents.

Two interchangeable implementations:

* :class:`InMemoryBus` - asyncio queues, perfect for a single process / tests.
* :class:`RedisBus`     - Redis pub/sub, lets agents run as separate processes.

Both expose the same API: ``publish()``, ``inbox(role)``, ``firehose()``
(an observer of *every* message) and ``history()``.
"""

from __future__ import annotations

import abc
import asyncio
import contextlib
import logging
from collections import deque
from typing import Any

from .config import Settings
from .schemas import Message, Role

logger = logging.getLogger(__name__)

_STOP = object()
BROADCAST = "broadcast"


def _role_key(role: Role | str) -> str:
    return role.value if isinstance(role, Role) else str(role)


def addressed_to(message: Message, role: Role | str) -> bool:
    """True when *message* is for *role* (direct or broadcast)."""

    recipient = message.recipient
    if recipient == BROADCAST:
        return True
    return _role_key(recipient) == _role_key(role)


class Subscription:
    """Async iterator over the messages matching a predicate.

    Slow consumers never block the sender: when the queue is full the oldest
    message is dropped and a warning is logged.
    """

    def __init__(self, name: str, predicate: Any = None, *, maxsize: int = 1024) -> None:
        self.name = name
        self._predicate = predicate
        self._queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=maxsize)
        self._closed = False
        self.dropped = 0

    @property
    def closed(self) -> bool:
        return self._closed

    def matches(self, message: Message) -> bool:
        if self._closed:
            return False
        return self._predicate is None or bool(self._predicate(message))

    def offer(self, message: Message) -> None:
        if self._closed:
            return
        try:
            self._queue.put_nowait(message)
        except asyncio.QueueFull:
            self.dropped += 1
            with contextlib.suppress(asyncio.QueueEmpty):
                self._queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                self._queue.put_nowait(message)
            logger.warning("subscription %s is slow, dropped %d message(s)", self.name, self.dropped)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        with contextlib.suppress(asyncio.QueueFull):
            self._queue.put_nowait(_STOP)

    async def get(self, timeout: float | None = None) -> Message | None:
        """Next message, ``None`` once the subscription is closed.

        Raises :class:`asyncio.TimeoutError` when *timeout* expires.
        """

        if timeout is None:
            item = await self._queue.get()
        else:
            item = await asyncio.wait_for(self._queue.get(), timeout)
        return None if item is _STOP else item

    def drain_nowait(self) -> list[Message]:
        """Return and remove every message currently queued."""

        items: list[Message] = []
        while True:
            try:
                item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if item is not _STOP:
                items.append(item)
        return items

    def __aiter__(self) -> "Subscription":
        return self

    async def __anext__(self) -> Message:
        item = await self._queue.get()
        if item is _STOP:
            raise StopAsyncIteration
        return item


class MessageBus(abc.ABC):
    """Common bookkeeping shared by every backend."""

    backend = "abstract"

    def __init__(self, *, history_size: int = 4000) -> None:
        self._subscriptions: list[Subscription] = []
        self._records: deque[Message] = deque(maxlen=history_size)
        self._closed = False

    # -- lifecycle ---------------------------------------------------------
    async def start(self) -> None:
        return None

    async def close(self) -> None:
        self._closed = True
        for subscription in list(self._subscriptions):
            subscription.close()

    async def __aenter__(self) -> "MessageBus":
        await self.start()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    # -- public API --------------------------------------------------------
    @abc.abstractmethod
    async def publish(self, message: Message) -> None:
        """Send *message* to every interested subscriber."""

    def inbox(self, role: Role | str) -> Subscription:
        """Messages addressed to *role* (plus broadcasts)."""

        subscription = Subscription(f"role:{_role_key(role)}", lambda m: addressed_to(m, role))
        self._subscriptions.append(subscription)
        return subscription

    def firehose(self) -> Subscription:
        """Observer subscription that receives *every* message."""

        subscription = Subscription("firehose")
        self._subscriptions.append(subscription)
        return subscription

    @property
    def subscribers(self) -> int:
        return len([s for s in self._subscriptions if not s.closed])

    async def history(self) -> list[Message]:
        return list(self._records)

    def _dispatch(self, message: Message) -> None:
        self._records.append(message)
        for subscription in list(self._subscriptions):
            if subscription.matches(message):
                subscription.offer(message)

    # -- helpers -----------------------------------------------------------
    def _prune(self) -> None:
        self._subscriptions = [s for s in self._subscriptions if not s.closed]


class InMemoryBus(MessageBus):
    """Single-process bus built on asyncio queues."""

    backend = "memory"

    async def publish(self, message: Message) -> None:
        if self._closed:
            logger.debug("publish on a closed in-memory bus, ignoring %s", message.id)
            return
        self._dispatch(message)
        await asyncio.sleep(0)  # let consumers run between messages


class RedisBus(MessageBus):
    """Redis pub/sub bus: agents may live in different processes / machines.

    Messages are published to ``channel`` (live fan-out) and appended to
    ``history_key`` (durable transcript, trimmed to ``history_size``).
    """

    backend = "redis"

    def __init__(
        self,
        url: str = "redis://localhost:6379/0",
        *,
        channel: str = "agentteam:messages",
        history_key: str = "agentteam:history",
        history_size: int = 4000,
    ) -> None:
        super().__init__(history_size=history_size)
        self.url = url
        self.channel = channel
        self.history_key = history_key
        self._redis: Any = None
        self._pubsub: Any = None
        self._reader: asyncio.Task[None] | None = None

    async def start(self) -> None:
        try:
            from redis import asyncio as redis_async  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("redis package is not installed: pip install redis") from exc

        client = redis_async.from_url(self.url, decode_responses=True)
        await client.ping()
        self._redis = client
        self._pubsub = client.pubsub()
        await self._pubsub.subscribe(self.channel)
        self._reader = asyncio.create_task(self._read_loop())
        logger.info("RedisBus connected to %s (channel=%s)", self.url, self.channel)

    async def _read_loop(self) -> None:
        assert self._pubsub is not None
        try:
            async for item in self._pubsub.listen():
                if item.get("type") != "message":
                    continue
                raw = item.get("data")
                if not isinstance(raw, str):
                    continue
                try:
                    self._dispatch(Message.model_validate_json(raw))
                except Exception as exc:  # pragma: no cover - malformed payload
                    logger.warning("ignoring malformed bus message: %s", exc)
        except asyncio.CancelledError:  # pragma: no cover - normal shutdown
            raise
        except Exception as exc:  # pragma: no cover - connection lost
            logger.error("RedisBus reader stopped: %s", exc)

    async def publish(self, message: Message) -> None:
        if self._redis is None:
            raise RuntimeError("RedisBus.start() must be awaited before publish()")
        payload = message.model_dump_json()
        await self._redis.publish(self.channel, payload)
        await self._redis.rpush(self.history_key, payload)
        await self._redis.ltrim(self.history_key, -self.maxlen, -1)

    @property
    def maxlen(self) -> int:
        return self._records.maxlen or 4000

    async def history(self) -> list[Message]:
        if self._redis is not None:
            raw_items = await self._redis.lrange(self.history_key, 0, -1)
            return [Message.model_validate_json(item) for item in raw_items]
        return list(self._records)

    async def close(self) -> None:
        if self._reader is not None:
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader
            self._reader = None
        if self._pubsub is not None:
            with contextlib.suppress(Exception):
                await self._pubsub.unsubscribe(self.channel)
                await self._pubsub.aclose()
            self._pubsub = None
        if self._redis is not None:
            with contextlib.suppress(Exception):
                await self._redis.aclose()
            self._redis = None
        await super().close()


async def create_bus(settings: Settings) -> MessageBus:
    """Build the bus requested by ``settings.bus_backend``.

    ``auto`` tries Redis first and silently falls back to the in-memory bus, so
    the project always runs - with or without a Redis server.
    """

    backend = settings.bus_backend
    if backend == "memory":
        return InMemoryBus()
    if backend == "redis":
        bus = RedisBus(settings.redis_url)
        await bus.start()
        return bus

    try:
        bus = RedisBus(settings.redis_url, history_size=2000)
        await bus.start()
        return bus
    except Exception as exc:
        logger.warning("Redis bus unavailable (%s); using the in-memory bus", exc)
        return InMemoryBus()
