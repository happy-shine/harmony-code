"""Admin CLI package for harmony-code (M5 Task 5.2).

Entry point: ``python -m app.admin <subcommand>`` — see :mod:`app.admin.cli`
for the argparse spec.

Created here instead of ``app/server/`` (which the original plan spec
mentioned) because there is no long-running "server" component distinct
from the gateway app; the only admin surface we actually need is user
management for the session-cookie auth stack.
"""
