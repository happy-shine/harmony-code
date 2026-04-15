import json
from pathlib import Path

import pytest

from app.cc_adapter.stream_parser import StreamParser

SAMPLE_PATH = Path(__file__).resolve().parents[4] / "docs/plans/cc-jsonl-samples/01-hello-text.jsonl"


def test_parser_extracts_session_id_from_init():
    """init frame may NOT be on line 0.

    M0 实测 (cc-cli-notes.md §Observed shape deviations): 当 host 配了
    SessionStart hook 时, CC 在 init 之前会先发若干 `system/hook_started` +
    `system/hook_response` 帧 (它们带的 session_id 是 **per-invocation**, 非
    对话 session_id)。Parser 必须按 `type/subtype` 匹配 init 帧, 而不是假设
    line 0。
    """
    parser = StreamParser()
    init_event = None
    for raw_line in SAMPLE_PATH.read_bytes().splitlines():
        if not raw_line.strip():
            continue
        event, _ = parser.feed_line(raw_line + b"\n")
        if event and event.get("type") == "system" and event.get("subtype") == "init":
            init_event = event
            break
    assert init_event is not None
    assert parser.session_id is not None


def test_parser_ignores_empty_lines():
    parser = StreamParser()
    event, raw = parser.feed_line(b"\n")
    assert event is None
    assert parser.session_id is None


def test_parser_ignores_malformed_json():
    parser = StreamParser()
    event, raw = parser.feed_line(b"not json\n")
    assert event is None
    assert parser.session_id is None


def test_parser_passes_through_non_init_events():
    parser = StreamParser()
    fake_event = json.dumps({"type": "assistant", "message": {"id": "x"}}).encode()
    event, raw = parser.feed_line(fake_event)
    assert event is not None
    assert event["type"] == "assistant"
    assert parser.session_id is None  # unchanged


def test_parser_does_not_overwrite_session_id_with_hook_frame():
    """Pre-init hook frames 的 session_id 是 per-invocation, 不是会话 id.
    StreamParser.session_id 必须只在 `type==system && subtype==init` 时设置。"""
    parser = StreamParser()
    hook_line = json.dumps({
        "type": "system",
        "subtype": "hook_started",
        "session_id": "hook-invocation-xyz",
    }).encode()
    parser.feed_line(hook_line + b"\n")
    assert parser.session_id is None
    init_line = json.dumps({
        "type": "system",
        "subtype": "init",
        "session_id": "conversation-abc",
    }).encode()
    parser.feed_line(init_line + b"\n")
    assert parser.session_id == "conversation-abc"


def test_parser_passes_through_rate_limit_event():
    """`rate_limit_event` is a top-level CC event not in the plan's initial
    set but confirmed in M0 samples — parser must forward it unmodified."""
    parser = StreamParser()
    line = json.dumps({"type": "rate_limit_event", "rate_limit_type": "..."}).encode()
    event, raw = parser.feed_line(line + b"\n")
    assert event is not None
    assert event["type"] == "rate_limit_event"
    assert parser.session_id is None  # unchanged
