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


def test_env_allowlist_keeps_basic_passthrough(monkeypatch):
    """Basic passthrough vars survive into the subprocess env. USER/LOGNAME
    are in the list so macOS Keychain fallback can resolve the login keychain
    owner when no OAuth token is present in env."""
    monkeypatch.setenv("PATH", "/custom/bin:/usr/bin")
    monkeypatch.setenv("HOME", "/home/tester")
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    monkeypatch.setenv("LC_ALL", "C")
    monkeypatch.setenv("TZ", "UTC")
    monkeypatch.setenv("USER", "tester")
    monkeypatch.setenv("LOGNAME", "tester")

    env = CCAdapter().build_env(_cfg())

    assert env["PATH"] == "/custom/bin:/usr/bin"
    assert env["HOME"] == "/home/tester"
    assert env["LANG"] == "en_US.UTF-8"
    assert env["LC_ALL"] == "C"
    assert env["TZ"] == "UTC"
    assert env["USER"] == "tester"
    assert env["LOGNAME"] == "tester"


def test_env_allowlist_keeps_non_host_managed_CLAUDE_CODE_vars(monkeypatch):
    """``CLAUDE_CODE_*`` vars that are NOT host-managed OAuth state pass
    through — e.g. debug flags."""
    monkeypatch.setenv("CLAUDE_CODE_DEBUG", "1")

    env = CCAdapter().build_env(_cfg())

    assert env.get("CLAUDE_CODE_DEBUG") == "1"


def test_env_blocklist_drops_host_managed_OAuth_vars(monkeypatch):
    """Host-managed OAuth state (set by claude-desktop / Claude Code when
    they spawn child processes) MUST be dropped: those tokens are scoped to
    the host session and rotate, so a backend started inside a host process
    would otherwise cache a token at startup that goes stale and 401s on
    every subsequent ``claude -p`` spawn. Dropping these forces the CLI to
    fall back to Keychain credentials (``claude login``), which are stable."""
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "tok-stale")
    monkeypatch.setenv("CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST", "1")
    monkeypatch.setenv("CLAUDE_CODE_SDK_HAS_OAUTH_REFRESH", "1")
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "claude-desktop")

    env = CCAdapter().build_env(_cfg())

    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env
    assert "CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST" not in env
    assert "CLAUDE_CODE_SDK_HAS_OAUTH_REFRESH" not in env
    assert "CLAUDE_CODE_ENTRYPOINT" not in env
    # And the token value must not surface via some other key.
    assert "tok-stale" not in env.values()


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
