"""In-process pub/sub + presence, powering the frontend's live-update feed
(docs/design/schema.md#realtime). Single-process only — no external broker,
which is fine at this app's scale (a lab, not a cluster); if that ever
changes, this is the one module that would need a real backend (e.g. Redis
pub/sub), not a redesign of how the rest of the app calls it.
"""

import time
from asyncio import Queue
from collections import defaultdict

PRESENCE_TTL = 20  # seconds — a heartbeat older than this is treated as "gone"


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[Queue]] = defaultdict(set)

    def subscribe(self, channel: str) -> Queue:
        q: Queue = Queue()
        self._subscribers[channel].add(q)
        return q

    def unsubscribe(self, channel: str, q: Queue) -> None:
        self._subscribers[channel].discard(q)

    def publish(self, channel: str, event: dict) -> None:
        for q in self._subscribers.get(channel, ()):
            q.put_nowait(event)


bus = EventBus()

# (channel, item_id) -> {username: last_seen_epoch} — who currently has this
# item open, refreshed by a heartbeat while its editor is open on screen
_presence: dict[tuple[str, str], dict[str, float]] = defaultdict(dict)


def touch_presence(channel: str, item_id: str, username: str) -> list[str]:
    """Record a heartbeat for `username` on this item, drop anyone whose
    last heartbeat is older than PRESENCE_TTL, broadcast the resulting
    roster to the channel, and return everyone else currently present."""
    now = time.time()
    users = _presence[(channel, item_id)]
    users[username] = now
    for stale in [u for u, seen in users.items() if now - seen > PRESENCE_TTL]:
        del users[stale]
    bus.publish(channel, {"type": "presence", "item_id": item_id, "users": sorted(users)})
    return sorted(u for u in users if u != username)
