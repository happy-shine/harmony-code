"""Smoke tests for :mod:`app.auth.passwords`.

We don't re-test argon2-cffi itself — it's a well-maintained binding —
but we do verify that our wrappers (a) produce a different hash each
call (salt is random), (b) verify successfully against the original
password, and (c) reject wrong passwords / malformed hashes without
raising.
"""

from __future__ import annotations

from app.auth.passwords import hash_password, verify_password


def test_hash_password_produces_distinct_hashes_for_same_plain():
    a = hash_password("hunter2")
    b = hash_password("hunter2")
    assert a != b  # random salt
    assert a.startswith("$argon2")


def test_verify_password_accepts_correct_plain():
    h = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", h) is True


def test_verify_password_rejects_wrong_plain():
    h = hash_password("real-password")
    assert verify_password("not-the-password", h) is False


def test_verify_password_rejects_malformed_hash():
    # Never raise on garbage input — callers should get a simple False.
    assert verify_password("x", "not-a-hash") is False
    assert verify_password("x", "") is False
