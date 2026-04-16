"""M1 harmony gateway app. Slim entrypoint mounting only the cc_adapter SSE router.
M5 will consolidate with the post-LangGraph-deletion gateway. Until then this runs independently."""
from fastapi import FastAPI

from app.gateway.routers import mcp as mcp_router
from app.gateway.routers import messages as messages_router
from app.gateway.routers import skills as skills_router


def create_harmony_app() -> FastAPI:
    app = FastAPI(title="harmony-code gateway (M1)")
    app.include_router(messages_router.router)
    app.include_router(mcp_router.router)
    app.include_router(skills_router.router)
    return app


app = create_harmony_app()
