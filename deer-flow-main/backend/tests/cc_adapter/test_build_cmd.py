"""Pure argv-construction tests for :meth:`CCAdapter.build_cmd`.

These tests do NOT spawn the ``claude`` CLI, so they live in a separate
module from :mod:`test_adapter` (which is skipped wholesale when ``claude``
is missing from PATH).
"""

from __future__ import annotations

from app.cc_adapter.adapter import CCAdapter
from app.cc_adapter.types import SpawnConfig


def test_build_cmd_terminates_with_double_dash_before_prompt():
    """Variadic ``--mcp-config`` / ``--add-dir`` would swallow the prompt otherwise.

    The CC CLI declares both flags as variadic in Commander.js, which greedily
    consumes subsequent positionals until another flag is seen. A ``--``
    terminator stops that consumption and forces the remaining token to be
    parsed as the prompt positional.
    """
    cfg = SpawnConfig(
        cwd="/tmp",
        user_prompt="my prompt",
        mcp_config_path="/tmp/mcp.json",
        add_dirs=["/tmp/uploads"],
        permission_mode="bypassPermissions",
    )
    cmd = CCAdapter().build_cmd(cfg)
    assert cmd[-1] == "my prompt"
    assert cmd[-2] == "--", f"expected '--' before prompt, got cmd[-2]={cmd[-2]!r}"
    # Sanity: both flags are in argv
    assert "--mcp-config" in cmd
    assert "--add-dir" in cmd


def test_build_cmd_terminator_present_even_without_flags():
    """``--`` is appended unconditionally (adapter.py line 37).

    Even when neither ``--mcp-config`` nor ``--add-dir`` is set, the terminator
    still precedes the prompt — simplest-invariant is easier to reason about
    than conditional gating.
    """
    cfg = SpawnConfig(cwd="/tmp", user_prompt="hello", permission_mode="bypassPermissions")
    cmd = CCAdapter().build_cmd(cfg)
    assert cmd[-1] == "hello"
    assert cmd[-2] == "--"
