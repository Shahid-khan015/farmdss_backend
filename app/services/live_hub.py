"""Fan-out from the ingestion daemon threads to per-session WebSocket clients.

Telemetry is produced on ``threading.Thread``s (the MQTT subscriber, the HTTP poller)
and consumed on the asyncio event loop. Every publish therefore crosses a thread ->
loop boundary exactly once, via ``loop.call_soon_threadsafe``. Nothing else in this
module may be called from a non-loop thread except ``publish_threadsafe``,
``has_listeners``, ``connection_count`` and ``snapshot``.

Telemetry here is *display* data. The queue is bounded and drops under pressure
rather than blocking an ingestion thread, because persistence is
``ingest_buffer.ReadingBuffer``'s job, not this module's -- a dropped frame is never
a lost reading.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

#: Bounded so a stalled event loop cannot grow the queue without limit on a 512 MB
#: instance. At ten feeds per second across ten sessions this is minutes of slack.
MAX_QUEUE = 2000


class LiveHub:
    """In-process pub/sub bridging the ingestion threads to WebSocket clients."""

    def __init__(self) -> None:
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._queue: Optional["asyncio.Queue[Tuple[str, dict]]"] = None
        self._drain: Optional["asyncio.Task[None]"] = None
        self._conns: Dict[str, Set[Any]] = {}
        # Guards `_conns` only. Held for the microseconds it takes to add/discard a
        # socket, never across an await.
        self._lock = threading.Lock()
        self.published = 0
        self.dropped = 0

    # --- loop side -----------------------------------------------------------

    async def start(self) -> None:
        """Capture the running loop and start the drain task. Call from lifespan."""
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue(maxsize=MAX_QUEUE)
        self._drain = asyncio.create_task(self._drain_forever(), name="live-hub-drain")
        logger.info("live hub started (queue max=%s)", MAX_QUEUE)

    async def stop(self) -> None:
        drain = self._drain
        self._drain = None
        if drain is not None:
            drain.cancel()
            try:
                await drain
            except asyncio.CancelledError:
                pass
        self._loop = None
        self._queue = None
        logger.info(
            "live hub stopped (published=%s dropped=%s)", self.published, self.dropped
        )

    async def register(self, session_id: str, websocket: Any) -> None:
        with self._lock:
            self._conns.setdefault(session_id, set()).add(websocket)
        logger.info(
            "live: client attached session=%s connections=%s",
            session_id,
            self.connection_count(),
        )

    async def unregister(self, session_id: str, websocket: Any) -> None:
        with self._lock:
            peers = self._conns.get(session_id)
            if peers is not None:
                peers.discard(websocket)
                if not peers:
                    self._conns.pop(session_id, None)
        logger.info(
            "live: client detached session=%s connections=%s",
            session_id,
            self.connection_count(),
        )

    async def _drain_forever(self) -> None:
        queue = self._queue
        if queue is None:  # pragma: no cover - start() always sets it
            return
        while True:
            session_id, payload = await queue.get()
            try:
                await self._fanout(session_id, payload)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("live: fan-out failed session=%s", session_id)

    async def _fanout(self, session_id: str, payload: dict) -> None:
        with self._lock:
            peers = list(self._conns.get(session_id, ()))
        if not peers:
            return

        # Sent concurrently so one slow client cannot delay the others.
        results = await asyncio.gather(
            *(peer.send_json(payload) for peer in peers), return_exceptions=True
        )
        dead: List[Any] = [
            peer for peer, outcome in zip(peers, results) if isinstance(outcome, BaseException)
        ]
        for peer in dead:
            await self.unregister(session_id, peer)

    # --- thread side ---------------------------------------------------------

    def publish_threadsafe(self, session_id: Optional[str], payload: dict) -> None:
        """Queue one frame for delivery. Safe from any thread; never raises or blocks."""
        if not session_id:
            return
        loop = self._loop
        queue = self._queue
        if loop is None or queue is None:
            # Hub not started (or already stopped) -- nothing is listening anyway.
            return

        def _put() -> None:
            try:
                queue.put_nowait((session_id, payload))
                self.published += 1
            except asyncio.QueueFull:
                self.dropped += 1

        try:
            loop.call_soon_threadsafe(_put)
        except RuntimeError:
            # Loop closed mid-shutdown. A display frame is not worth a traceback.
            pass

    def has_listeners(self, session_id: Optional[str]) -> bool:
        """True when at least one client is watching. Lets producers skip work."""
        if not session_id:
            return False
        with self._lock:
            return bool(self._conns.get(session_id))

    def watched_sessions(self) -> List[str]:
        """Session ids with at least one client attached. Safe from any thread.

        Used by the pre-persistence hot path, which has a normalized reading but not yet
        a session id -- the row has not been written, so nothing has resolved its session.
        Publishing to the watched set is a deliberate approximation, and no worse than
        the attribution `resolve_target_session` already performs: it attributes every
        reading to the most recently started session regardless of which machine sent it.
        """
        with self._lock:
            return [key for key, peers in self._conns.items() if peers]

    def connection_count(self) -> int:
        with self._lock:
            return sum(len(peers) for peers in self._conns.values())

    def snapshot(self) -> dict:
        with self._lock:
            per_session = {key: len(peers) for key, peers in self._conns.items()}
        return {
            "running": self._loop is not None,
            "sessions": per_session,
            "connections": sum(per_session.values()),
            "published": self.published,
            "dropped": self.dropped,
            "queue_max": MAX_QUEUE,
        }


#: Process-wide singleton. One uvicorn worker only -- see render.yaml.
HUB = LiveHub()
