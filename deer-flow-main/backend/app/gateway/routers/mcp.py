"""MCP server CRUD backed by ``harmony.db``.

Replaces the M0 ``extensions_config.json`` file-based router. Rows are
read by :func:`app.cc_adapter.compose.compose_mcp_config` on every CC
spawn, so any edit through these endpoints takes effect on the NEXT
message in any thread — no caching layer to invalidate.

M3 scope: ``user_id`` is stubbed to ``"u_default"`` via
:func:`app.gateway.deps.current_user_id`; M5 wires real auth (better-auth).
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.db import McpRow
from app.gateway.deps import current_user_id, get_db


router = APIRouter(prefix="/api/mcp", tags=["mcp"])


# --- Models ---------------------------------------------------------------


class MCPServerIn(BaseModel):
    name: str
    transport: str = Field(..., description="stdio | sse | http")
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    env: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True


class MCPServerOut(MCPServerIn):
    id: str
    user_id: str | None


class MCPServerPatch(BaseModel):
    """Partial update. Any field omitted is preserved.

    Typed rather than a raw ``dict`` so FastAPI emits a proper OpenAPI
    schema for the frontend and unknown keys cannot touch arbitrary
    columns. ``exclude_unset=True`` at the call site ensures omitted
    fields are not written as ``None``.
    """

    name: str | None = None
    transport: str | None = None
    command: str | None = None
    args: list[str] | None = None
    url: str | None = None
    headers: dict[str, str] | None = None
    env: dict[str, str] | None = None
    enabled: bool | None = None


def _row_to_out(r: McpRow) -> MCPServerOut:
    return MCPServerOut(
        id=r.id,
        user_id=r.user_id,
        name=r.name,
        transport=r.transport,
        command=r.command,
        args=json.loads(r.args_json) if r.args_json else [],
        url=r.url,
        headers=json.loads(r.headers_json) if r.headers_json else {},
        env=json.loads(r.env_json) if r.env_json else {},
        enabled=r.enabled,
    )


# --- Routes ---------------------------------------------------------------


@router.get("", response_model=list[MCPServerOut])
def list_mcp(user_id: str = Depends(current_user_id)) -> list[MCPServerOut]:
    return [_row_to_out(r) for r in get_db().list_mcp_for_user(user_id=user_id)]


@router.post("", response_model=MCPServerOut)
def create_mcp(
    body: MCPServerIn, user_id: str = Depends(current_user_id)
) -> MCPServerOut:
    db = get_db()
    new_id = db.insert_mcp(
        user_id=user_id,
        name=body.name,
        transport=body.transport,
        command=body.command,
        args=body.args or None,
        url=body.url,
        headers=body.headers or None,
        env=body.env or None,
        enabled=body.enabled,
    )
    row = db.get_mcp(new_id)
    if row is None:
        # Should not happen; insert just succeeded.
        raise HTTPException(500, "inserted row not found")
    return _row_to_out(row)


@router.delete("/{mcp_id}")
def delete_mcp(
    mcp_id: str, user_id: str = Depends(current_user_id)
) -> dict[str, Any]:
    db = get_db()
    row = db.get_mcp(mcp_id)
    if row is None:
        raise HTTPException(404, "mcp not found")
    # Global rows (user_id IS NULL) are visible to everyone in GET but
    # cannot be mutated by non-admins. M5 may introduce admin roles.
    if row.user_id is None or row.user_id != user_id:
        raise HTTPException(403, "not yours")
    db.delete_mcp(mcp_id)
    return {"ok": True}


@router.patch("/{mcp_id}", response_model=MCPServerOut)
def update_mcp(
    mcp_id: str,
    patch: MCPServerPatch,
    user_id: str = Depends(current_user_id),
) -> MCPServerOut:
    db = get_db()
    row = db.get_mcp(mcp_id)
    if row is None:
        raise HTTPException(404, "mcp not found")
    if row.user_id is None or row.user_id != user_id:
        raise HTTPException(403, "not yours")
    # ``exclude_unset=True`` so ``None`` on omitted fields doesn't null columns.
    db.update_mcp(mcp_id, patch.model_dump(exclude_unset=True))
    updated = db.get_mcp(mcp_id)
    assert updated is not None  # we just read it above under the same engine
    return _row_to_out(updated)
