"""Parse CC stdout jsonl line-by-line, extract session_id, pass through everything else."""
import json
from typing import AsyncIterator


class StreamParser:
    """Stateful parser: reads lines, yields (event_dict, raw_line).
    Tracks the first observed session_id.
    """

    def __init__(self) -> None:
        self.session_id: str | None = None

    def feed_line(self, raw_line: bytes) -> tuple[dict | None, bytes]:
        """Parse one line; return (parsed_event or None if invalid, raw_line).
        Side effect: capture session_id on first system.init event.
        """
        line = raw_line.strip()
        if not line:
            return None, raw_line
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return None, raw_line
        if (self.session_id is None
                and event.get("type") == "system"
                and event.get("subtype") == "init"):
            self.session_id = event.get("session_id")
        return event, raw_line
