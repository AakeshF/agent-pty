from __future__ import annotations

import re
import time

from agent_pty.io import snapshot

POLL_INTERVAL = 0.05


def wait_for(
    name: str,
    pattern: str | re.Pattern[str],
    timeout: float = 10.0,
) -> str:
    if isinstance(pattern, str):
        compiled = re.compile(re.escape(pattern))
    else:
        compiled = pattern
    deadline = time.monotonic() + timeout
    while True:
        snap = snapshot(name)
        if compiled.search(snap):
            return snap
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"Pattern {pattern!r} not found in session {name!r} "
                f"within {timeout}s"
            )
        time.sleep(POLL_INTERVAL)
