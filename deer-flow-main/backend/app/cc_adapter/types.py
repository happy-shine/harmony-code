"""CC event TypedDicts. Only fields we actually consume."""

from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict


@dataclass
class SpawnConfig:
    cwd: str
    user_prompt: str
    resume_session_id: str | None = None
    mcp_config_path: str | None = None
    model: str | None = None
    add_dirs: list[str] = field(default_factory=list)
    permission_mode: str = "bypassPermissions"
    extra_env: dict[str, str] = field(default_factory=dict)
    # Wall-clock budget for the entire CC run, in seconds. ``None``
    # disables the guard entirely (the MVP default — legitimate runs
    # can legitimately take a long time). On timeout the adapter emits
    # ``_adapter.error`` with ``code="timeout"`` and terminates CC.
    timeout_seconds: float | None = None


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
