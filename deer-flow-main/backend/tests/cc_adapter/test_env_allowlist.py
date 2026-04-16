"""Task 6.1: env-allowlist hardening coverage for ``CCAdapter.build_env``.

Design Section 2 pins the passthrough to a tight whitelist (PATH, HOME,
LANG, LC_ALL, TZ, plus ``CLAUDE_CODE_*``). Anything else on the parent's
environment — specifically AWS/GCP tokens, generic ``*_TOKEN`` /
``*_KEY`` service creds, and ``DATABASE_URL`` — MUST NOT be forwarded
to the CC subprocess. The current impl is whitelist-only, so "blocked"
is an implicit property; these tests codify that property.
"""

from __future__ import annotations

from app.cc_adapter.adapter import CCAdapter
from app.cc_adapter.types import SpawnConfig


def _cfg() -> SpawnConfig:
    return SpawnConfig(cwd="/tmp", user_prompt="x")


def test_env_allowlist_keeps_PATH_HOME_LANG_LC_ALL_TZ(monkeypatch):
    """All five basic passthrough vars survive into the subprocess env."""
    monkeypatch.setenv("PATH", "/custom/bin:/usr/bin")
    monkeypatch.setenv("HOME", "/home/tester")
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    monkeypatch.setenv("LC_ALL", "C")
    monkeypatch.setenv("TZ", "UTC")

    env = CCAdapter().build_env(_cfg())

    assert env["PATH"] == "/custom/bin:/usr/bin"
    assert env["HOME"] == "/home/tester"
    assert env["LANG"] == "en_US.UTF-8"
    assert env["LC_ALL"] == "C"
    assert env["TZ"] == "UTC"


def test_env_allowlist_keeps_CLAUDE_CODE_prefix(monkeypatch):
    """Anything starting with ``CLAUDE_CODE_`` is passed verbatim — the CLI
    reads its own OAuth token / debug flags from this namespace."""
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "tok-123")
    monkeypatch.setenv("CLAUDE_CODE_DEBUG", "1")

    env = CCAdapter().build_env(_cfg())

    assert env.get("CLAUDE_CODE_OAUTH_TOKEN") == "tok-123"
    assert env.get("CLAUDE_CODE_DEBUG") == "1"


def test_env_blocklist_drops_AWS_GCP_TOKEN_KEY_DATABASE_URL(monkeypatch):
    """None of the common cloud/creds vars leak through the adapter.

    This is the core M6 Section 2 hardening test: CC spawns with a
    scrubbed environment so a prompt-injection or a malicious MCP server
    cannot trivially exfiltrate parent-process secrets.
    """
    leaky = {
        "AWS_ACCESS_KEY_ID": "AKIA_SECRET",
        "AWS_SECRET_ACCESS_KEY": "long/secret",
        "AWS_SESSION_TOKEN": "tok",
        "GCP_PROJECT": "prod-42",
        "GCP_SERVICE_ACCOUNT_KEY": "{}",
        "MY_SERVICE_TOKEN": "abc",
        "OPENAI_API_KEY": "sk-abc",
        "ANTHROPIC_API_KEY": "sk-ant-xyz",
        "DATABASE_URL": "postgres://user:pw@host/db",
        "REDIS_URL": "redis://host",
        "GITHUB_TOKEN": "ghp_xxx",
    }
    for k, v in leaky.items():
        monkeypatch.setenv(k, v)

    env = CCAdapter().build_env(_cfg())

    for k in leaky:
        assert k not in env, f"{k} leaked into subprocess env"
    # And definitively: none of the secret VALUES appear either (belt-and-suspenders
    # against someone accidentally remapping a leaky var through an allowlisted key).
    for v in leaky.values():
        assert v not in env.values(), f"leaky value {v!r} surfaced in env"


def test_extra_env_overrides_allowlist(monkeypatch):
    """``cfg.extra_env`` is the documented escape hatch (tests use it to
    simulate missing ``claude`` via ``PATH=""``). Must still work."""
    monkeypatch.setenv("PATH", "/usr/bin")
    cfg = SpawnConfig(cwd="/tmp", user_prompt="x", extra_env={"PATH": ""})

    env = CCAdapter().build_env(cfg)

    assert env["PATH"] == ""  # extra_env wins


def test_env_is_whitelist_only_no_inheritance(monkeypatch):
    """A random non-whitelisted var must NOT appear in the subprocess env,
    even if it's innocuous. The adapter is whitelist-only, not blocklist-based."""
    monkeypatch.setenv("RANDOM_UNRELATED_VAR", "present")

    env = CCAdapter().build_env(_cfg())

    assert "RANDOM_UNRELATED_VAR" not in env
