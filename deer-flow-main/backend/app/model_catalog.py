"""Static catalog of CC-supported models for the ``/api/models`` router.

The plan text for Task 3.6 said "configured in ``config.yaml``", but the
rest of the backend has no YAML config infrastructure — adding it just
for a three-item static list is YAGNI. This module-level constant is the
pragmatic shape; M4+ can promote it to a config file (or an env-var
override) without changing the router.

Model IDs match what the CC CLI accepts for ``--model`` as of 2026-04.
Adapter code already emits ``--model <value>`` when ``SpawnConfig.model``
is set (``app/cc_adapter/adapter.py`` build_cmd) — this module is *not*
consumed by the adapter directly; it's the source of truth for the
router's catalog response and for validating user-supplied model IDs.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelInfo:
    id: str
    name: str | None = None
    description: str | None = None


MODELS: tuple[ModelInfo, ...] = (
    ModelInfo(
        id="claude-sonnet-4-5",
        name="Claude Sonnet 4.5",
        description="Balanced capability and latency. Default pick.",
    ),
    ModelInfo(
        id="claude-opus-4-5",
        name="Claude Opus 4.5",
        description="Highest capability; slower and more expensive.",
    ),
    ModelInfo(
        id="claude-haiku-4-5",
        name="Claude Haiku 4.5",
        description="Fastest and cheapest; for lightweight tasks.",
    ),
)


MODEL_IDS: frozenset[str] = frozenset(m.id for m in MODELS)


def is_valid_model_id(mid: str) -> bool:
    """Accept a model id iff it's in :data:`MODELS`."""
    return mid in MODEL_IDS
