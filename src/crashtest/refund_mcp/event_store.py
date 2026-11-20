"""An in-memory event store, so the MCP server can serve resumable SSE streams.

`mcp` 2.3.0 defines the `EventStore` interface (`mcp.server.streamable_http`)
but ships no implementation. This one follows the interface's docstrings and the
2025-11-25 specification: every stored event gets an ID unique across all
streams, and a replay sends only the events of the same stream that came after
the given ID ("The server MUST NOT replay messages that would have been
delivered on a different stream").

It lives in the server process, so it is lost when the server stops. That is
enough for experiment 007, which drops connections, not servers.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

from mcp.server.streamable_http import (
    EventCallback,
    EventId,
    EventMessage,
    EventStore,
    StreamId,
)
from mcp_types import JSONRPCMessage


@dataclass(frozen=True)
class _Stored:
    event_id: EventId
    stream_id: StreamId
    message: JSONRPCMessage | None  # None for a priming event


class InMemoryEventStore(EventStore):
    def __init__(self) -> None:
        self._events: list[_Stored] = []
        self._index: dict[EventId, int] = {}
        self._ids = itertools.count(1)

    async def store_event(self, stream_id: StreamId, message: JSONRPCMessage | None) -> EventId:
        event_id = str(next(self._ids))
        self._index[event_id] = len(self._events)
        self._events.append(_Stored(event_id, stream_id, message))
        return event_id

    async def replay_events_after(
        self, last_event_id: EventId, send_callback: EventCallback
    ) -> StreamId | None:
        position = self._index.get(last_event_id)
        if position is None:
            return None
        stream_id = self._events[position].stream_id
        for event in self._events[position + 1 :]:
            # Priming events carry no message; there is nothing to resend.
            if event.stream_id == stream_id and event.message is not None:
                await send_callback(EventMessage(event.message, event.event_id))
        return stream_id

    # Deliberately not __len__: `mcp` 2.3.0 tests the store by truthiness
    # (`if self._event_store:`), so an empty store with a length would count as
    # no store at all and never record its first event.
    @property
    def event_count(self) -> int:
        return len(self._events)
