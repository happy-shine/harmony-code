"""Audit emitter: single-line JSON events on a dedicated named logger.

Uses ``logging.getLogger("harmony.audit")`` rather than the root logger
so ops can route audit lines independently of application logs — e.g.
attach a JSON file handler or a syslog socket only to this logger via
standard ``logging.config.dictConfig`` without affecting the rest of
the app's log stream.

Transport is deliberately stdlib-only. One event per log line means an
operator can ``journalctl | grep harmony.audit`` or point a log shipper
at the stream and parse each line as JSON. Intentionally no file sink,
no rotation policy, no structured-log dependency — the design says
"stdout or file" and deployment-specific routing is the operator's
concern.

Level is ``INFO`` — audit events are operational signal, not debug
noise. To silence them in tests or local dev, set the ``harmony.audit``
logger's level to ``WARNING``.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger("harmony.audit")


def emit(event: dict) -> None:
    """Serialize ``event`` to a compact JSON line and log at INFO."""
    logger.info(json.dumps(event, separators=(",", ":"), sort_keys=True))
