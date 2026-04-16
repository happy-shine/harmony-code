"""Harmony-code models router: static CC model catalog + per-user default.

Two surfaces:

* ``GET /api/models`` — the static catalog defined in
  :mod:`app.model_catalog`. No DB read.
* ``GET /api/models/me`` / ``PUT /api/models/me`` — the caller's
  ``user_prefs.default_model``. On PUT with a known id, ``send_message``
  will pass it as ``SpawnConfig.model`` on the next spawn; the adapter
  already emits ``--model <value>``.

``user_prefs.extras_json`` is deliberately *not* exposed here — it's a
forward-compat slot reserved for M4+ prefs (theme, timezone, etc.) and
surfacing it now would bake unrelated shape into the M3 contract.

M3 scope: ``user_id`` comes from :func:`app.gateway.deps.current_user_id`
(stub returning ``"u_default"``); M5 wires real auth.

Module name disambiguates from the sibling ``models.py`` in this package,
which is the M5-scheduled LangGraph-era models router mounted only by
``app.gateway.app`` (the legacy entry). ``harmony_app.py`` mounts *this*
router under the same ``/api/models`` prefix — the two apps are never
run in the same process.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.gateway.deps import current_user_id, get_db
from app.model_catalog import MODELS, is_valid_model_id

router = APIRouter(prefix="/api/models", tags=["models"])


# --- Models ---------------------------------------------------------------


class ModelInfoOut(BaseModel):
    id: str
    name: str | None = None
    description: str | None = None


class UserModelPrefOut(BaseModel):
    """Response shape for GET/PUT ``/api/models/me``.

    Always returns ``default_model`` — ``null`` when unset (either no row
    or row with ``default_model IS NULL``). We do not surface the
    "row exists" distinction because callers don't need it.
    """

    default_model: str | None


class UserModelPrefIn(BaseModel):
    """PUT body. ``null`` clears the pref; known model ids persist."""

    default_model: str | None


# --- Routes ---------------------------------------------------------------


@router.get("", response_model=list[ModelInfoOut])
def list_models() -> list[ModelInfoOut]:
    return [ModelInfoOut(id=m.id, name=m.name, description=m.description) for m in MODELS]


@router.get("/me", response_model=UserModelPrefOut)
def get_my_model(
    user_id: str = Depends(current_user_id),
    db=Depends(get_db),
) -> UserModelPrefOut:
    row = db.get_user_prefs(user_id)
    return UserModelPrefOut(default_model=row.default_model if row else None)


@router.put("/me", response_model=UserModelPrefOut)
def set_my_model(
    body: UserModelPrefIn,
    user_id: str = Depends(current_user_id),
    db=Depends(get_db),
) -> UserModelPrefOut:
    # 422 matches FastAPI's body-validation convention — same class a
    # pydantic type mismatch would produce, so clients can share one
    # "body was rejected" branch.
    if body.default_model is not None and not is_valid_model_id(body.default_model):
        raise HTTPException(422, f"unknown model id: {body.default_model!r}")
    db.upsert_user_prefs(user_id, default_model=body.default_model)
    return UserModelPrefOut(default_model=body.default_model)
