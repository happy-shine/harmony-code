"""CC event TypedDicts. Only fields we actually consume."""
from typing import Any, Literal, TypedDict


class CCSystemInit(TypedDict, total=False):
    type: Literal["system"]
    subtype: Literal["init"]
    session_id: str
    model: str
    cwd: str
    tools: list[str]
    mcp_servers: list[dict[str, Any]]


class CCResultEvent(TypedDict, total=False):
    type: Literal["result"]
    duration_ms: int
    total_cost_usd: float
    usage: dict[str, Any]


# Note: We deliberately do NOT type assistant/user events strictly.
# They get forwarded as raw dicts — only the adapter extracts session_id from init,
# everything else is transport.
