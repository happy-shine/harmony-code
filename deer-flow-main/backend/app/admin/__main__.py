"""Entrypoint for ``python -m app.admin``."""

from __future__ import annotations

import sys

from app.admin.cli import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
