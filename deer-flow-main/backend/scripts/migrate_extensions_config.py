"""One-time migration of legacy ``extensions_config.json`` into ``harmony.db``.

See Task 3.7 in ``docs/plans/2026-04-15-harmony-code-plan.md`` for the
migration contract. Stub — implementation follows TDD.
"""
from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - stub
    raise NotImplementedError("migrate_extensions_config not yet implemented")


if __name__ == "__main__":
    sys.exit(main())
