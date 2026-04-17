"""Memory router: user-curated facts (harmony v1).

The deer-flow UI talks to a richer ``UserMemory`` shape with six
pre-defined summary blocks plus a ``facts`` array. Harmony v1 only
persists facts — the summary blocks are returned as empty-shell objects
so the frontend's shape validator stays happy and we can ship now.

Endpoints::

    GET    /api/memory                       → UserMemory
    DELETE /api/memory                       → clears every fact
    GET    /api/memory/export                → same as GET /api/memory
    POST   /api/memory/import                → replace all facts
    POST   /api/memory/facts                 → add a fact
    PATCH  /api/memory/facts/{fact_id}       → edit
    DELETE /api/memory/facts/{fact_id}       → delete

The import/export round-trip is deliberately lossy for summaries (the
backend drops them on import) — we do not pretend to persist data the
server can't derive.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.db import MemoryFactRow
from app.gateway.deps import current_user_id, get_db

router = APIRouter(prefix="/api/memory", tags=["memory"])


# --- Models ---------------------------------------------------------------


class MemoryFactOut(BaseModel):
    id: str
    content: str
    category: str
    confidence: float
    source: str
    createdAt: str  # camelCase to match the deer-flow UserMemory shape


class MemorySection(BaseModel):
    summary: str = ""
    updatedAt: str = ""


class UserMemoryUser(BaseModel):
    workContext: MemorySection = Field(default_factory=MemorySection)
    personalContext: MemorySection = Field(default_factory=MemorySection)
    topOfMind: MemorySection = Field(default_factory=MemorySection)


class UserMemoryHistory(BaseModel):
    recentMonths: MemorySection = Field(default_factory=MemorySection)
    earlierContext: MemorySection = Field(default_factory=MemorySection)
    longTermBackground: MemorySection = Field(default_factory=MemorySection)


class UserMemoryOut(BaseModel):
    """Top-level shape the frontend's ``isImportedMemory`` validator expects.

    ``version`` is a schema tag; bump if we ever rename keys. ``lastUpdated``
    is computed at serialization time from the newest fact's ``createdAt``
    (or the current time if there are no facts), so the UI's "last updated"
    stamp stays live.
    """

    version: str = "1"
    lastUpdated: str
    user: UserMemoryUser = Field(default_factory=UserMemoryUser)
    history: UserMemoryHistory = Field(default_factory=UserMemoryHistory)
    facts: list[MemoryFactOut]


class MemoryFactIn(BaseModel):
    content: str
    category: str = "context"
    confidence: float = 0.8


class MemoryFactPatch(BaseModel):
    """Partial update. ``None`` is treated as "don't touch" (not "null")."""

    content: str | None = None
    category: str | None = None
    confidence: float | None = None


# --- Helpers --------------------------------------------------------------


def _iso(value: Any) -> str:
    """Normalize a SQLAlchemy timestamp (datetime OR ISO string) to ISO-8601 UTC.

    SQLite's default ``CURRENT_TIMESTAMP`` emits a naive ``"YYYY-MM-DD HH:MM:SS"``
    string. If we pass that through verbatim, JavaScript parses it as LOCAL
    time and ``formatTimeAgo`` shows bogus offsets (e.g. "8 hours ago"
    immediately after insert in a UTC+8 browser). Tag the value as UTC by
    normalizing the space separator to ``T`` and appending ``Z``.
    """
    if value is None:
        return datetime.now(UTC).isoformat()
    if isinstance(value, datetime):
        # Ensure tz-aware so isoformat() includes an offset.
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.isoformat()
    # String path (sqlite default). Normalize: space→T, ensure trailing Z
    # if no offset is present already.
    s = str(value)
    if " " in s and "T" not in s:
        s = s.replace(" ", "T", 1)
    if s.endswith("Z") or "+" in s[10:] or "-" in s[10:]:
        return s
    return s + "Z"


def _row_to_out(r: MemoryFactRow) -> MemoryFactOut:
    return MemoryFactOut(
        id=r.id,
        content=r.content,
        category=r.category,
        confidence=float(r.confidence),
        source=r.source,
        createdAt=_iso(r.created_at),
    )


def _validate_confidence(v: float) -> None:
    if not (0.0 <= v <= 1.0):
        raise HTTPException(400, "confidence must be between 0 and 1")


def _build_user_memory(rows: list[MemoryFactRow]) -> UserMemoryOut:
    facts = [_row_to_out(r) for r in rows]
    last = facts[0].createdAt if facts else datetime.now(UTC).isoformat()
    return UserMemoryOut(lastUpdated=last, facts=facts)


# --- Routes ---------------------------------------------------------------


@router.get("", response_model=UserMemoryOut)
def get_memory(user_id: str = Depends(current_user_id)) -> UserMemoryOut:
    rows = get_db().list_memory_facts_for_user(user_id)
    return _build_user_memory(rows)


@router.get("/export", response_model=UserMemoryOut)
def export_memory(user_id: str = Depends(current_user_id)) -> UserMemoryOut:
    # Same payload as GET /api/memory — the deer-flow UI uses a dedicated
    # endpoint so the server could one day emit a different encoding or
    # a derivative bundle, but for harmony v1 it's a straight alias.
    return get_memory(user_id=user_id)


class ImportBody(BaseModel):
    # Loose — we only care about ``facts``; other keys are ignored by
    # design (see module docstring). Use ``dict`` so pydantic doesn't
    # reject unknown fields the UI exports (version, lastUpdated, ...).
    facts: list[dict[str, Any]]


@router.post("/import")
def import_memory(
    body: ImportBody,
    user_id: str = Depends(current_user_id),
) -> dict[str, Any]:
    """Replace every fact owned by ``user_id`` with the imported set.

    Idempotent, last-write-wins: the previous rows are removed in the
    same transaction so the UI doesn't briefly double-render while the
    new batch lands.
    """
    db = get_db()
    db.clear_memory_facts_for_user(user_id)
    imported = 0
    for item in body.facts:
        content = item.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        category = item.get("category") or "context"
        confidence = float(item.get("confidence", 0.8))
        _validate_confidence(confidence)
        db.insert_memory_fact(
            user_id=user_id,
            content=content,
            category=str(category),
            confidence=confidence,
            # ``source`` is reset on import — the original might have
            # been a thread id, but that thread may not belong to this
            # user (cross-user import), so we flatten to "manual".
            source="manual",
        )
        imported += 1
    return {"imported": imported}


@router.delete("")
def clear_memory(user_id: str = Depends(current_user_id)) -> dict[str, Any]:
    deleted = get_db().clear_memory_facts_for_user(user_id)
    return {"deleted": deleted}


@router.post("/facts", response_model=MemoryFactOut)
def create_fact(
    body: MemoryFactIn,
    user_id: str = Depends(current_user_id),
) -> MemoryFactOut:
    content = body.content.strip()
    if not content:
        raise HTTPException(400, "content is required")
    _validate_confidence(body.confidence)
    db = get_db()
    new_id = db.insert_memory_fact(
        user_id=user_id,
        content=content,
        category=body.category,
        confidence=body.confidence,
        source="manual",
    )
    row = db.get_memory_fact(new_id)
    if row is None:
        raise HTTPException(500, "inserted row not found")
    return _row_to_out(row)


@router.patch("/facts/{fact_id}", response_model=MemoryFactOut)
def update_fact(
    fact_id: str,
    patch: MemoryFactPatch,
    user_id: str = Depends(current_user_id),
) -> MemoryFactOut:
    db = get_db()
    row = db.get_memory_fact(fact_id)
    if row is None or row.user_id != user_id:
        # Single 404 covers "unknown" AND "not-yours" to avoid leaking
        # existence (same convention as /api/threads).
        raise HTTPException(404, "fact not found")
    if patch.confidence is not None:
        _validate_confidence(patch.confidence)
    if patch.content is not None and not patch.content.strip():
        raise HTTPException(400, "content cannot be blank")
    db.update_memory_fact(fact_id, patch.model_dump(exclude_unset=True))
    updated = db.get_memory_fact(fact_id)
    assert updated is not None
    return _row_to_out(updated)


@router.delete("/facts/{fact_id}")
def delete_fact(
    fact_id: str,
    user_id: str = Depends(current_user_id),
) -> dict[str, Any]:
    db = get_db()
    row = db.get_memory_fact(fact_id)
    if row is None or row.user_id != user_id:
        raise HTTPException(404, "fact not found")
    db.delete_memory_fact(fact_id)
    return {"deleted": True, "id": fact_id}
