# NOTE: Legacy routers depend on LangGraph-era modules (`deerflow.*`) that
# aren't installed in the harmony-code dev environment. The try/except lets the
# M1 slim router (`app.gateway.routers.messages`) be imported stand-alone.
# When legacy deps ARE present the re-exports still work as before.
try:  # pragma: no cover - import-time environment branch
    from . import artifacts, assistants_compat, mcp, models, skills, suggestions, thread_runs, threads, uploads  # noqa: F401
    __all__ = ["artifacts", "assistants_compat", "mcp", "models", "skills", "suggestions", "threads", "thread_runs", "uploads"]
except ImportError:
    __all__ = []
