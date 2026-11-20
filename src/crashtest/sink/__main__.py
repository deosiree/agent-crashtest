"""Run the sink:  python -m crashtest.sink

Env: DATABASE_URL, SINK_HOST (default 127.0.0.1), SINK_PORT (default 8101).

The server is started here, not with the `uvicorn` command, because on Windows
uvicorn picks the Proactor event loop and psycopg's async mode needs a selector
loop. Starting it ourselves lets us choose the loop on every platform.
"""

from __future__ import annotations

import asyncio
import os
import selectors
import sys

import uvicorn


def main() -> None:
    config = uvicorn.Config(
        "crashtest.sink.app:app",
        host=os.environ.get("SINK_HOST", "127.0.0.1"),
        port=int(os.environ.get("SINK_PORT", "8101")),
        log_level=os.environ.get("SINK_LOG_LEVEL", "warning"),
    )
    server = uvicorn.Server(config)
    if sys.platform == "win32":
        asyncio.run(
            server.serve(),
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
        )
    else:
        asyncio.run(server.serve())


if __name__ == "__main__":
    main()
