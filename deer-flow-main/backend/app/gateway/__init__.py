# NOTE: Legacy `app`/`create_app` re-exports skipped when LangGraph runtime deps
# (deerflow, agent-sandbox, volcengine-python-sdk) aren't installed — this lets
# `app.gateway.harmony_app` (M1 slim entrypoint) import without dragging in
# legacy baggage that M5 will remove. When legacy deps ARE present the
# re-export still works.
try:  # pragma: no cover - import-time environment branch
    from .app import app, create_app  # noqa: F401
except ImportError:
    app = None  # type: ignore[assignment]
    create_app = None  # type: ignore[assignment]

from .config import GatewayConfig, get_gateway_config

__all__ = ["app", "create_app", "GatewayConfig", "get_gateway_config"]
