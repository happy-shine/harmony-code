"""Password hashing wrappers around argon2-cffi.

Thin by design: the library already handles salting, parameter tuning,
and constant-time verification. We wrap it for two reasons:

1. A single place to call ``PasswordHasher`` with our defaults, so the
   admin CLI and auth router can't drift.
2. A ``verify_password`` that returns ``bool`` instead of raising
   ``argon2.exceptions.VerifyMismatchError`` / ``InvalidHash`` — callers
   shouldn't need to know argon2-specific exceptions to answer
   "is this password right?".
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

_hasher = PasswordHasher()


def hash_password(plain: str) -> str:
    """Return an argon2 PHC-formatted hash for ``plain``.

    Non-deterministic (random salt per call); the verifier reads the
    salt + parameters out of the hash string itself.
    """
    return _hasher.hash(plain)


def verify_password(plain: str, stored_hash: str) -> bool:
    """Return True iff ``plain`` matches ``stored_hash``.

    Catches both the "wrong password" and "malformed hash" argon2
    exceptions — either way the answer is ``False``. Any other exception
    propagates (genuine bugs shouldn't be swallowed).
    """
    if not stored_hash:
        return False
    try:
        return _hasher.verify(stored_hash, plain)
    except (VerifyMismatchError, InvalidHashError):
        return False
