"""Static catalog of CC-supported models for the ``/api/models`` router.

The plan text for Task 3.6 said "configured in ``config.yaml``", but the
rest of the backend has no YAML config infrastructure — adding it just
for a three-item static list is YAGNI. This module-level constant is the
pragmatic shape; M4+ can promote it to a config file (or an env-var
override) without changing the router.

Model ids are the CC CLI's **aliases** (``sonnet``/``opus``/``haiku``)
rather than versioned full names. Per ``claude --help`` observed on
2026-04-16 (CC 2.1.92):

    --model <model>   Model for the current session. Provide an alias
                      for the latest model (e.g. 'sonnet' or 'opus') or
                      a model's full name (e.g. 'claude-sonnet-4-6').

Aliases always track the latest model of that tier, so the catalog
doesn't need to be edited every time Anthropic ships a new point
release — a full-name catalog would have drifted (e.g. the empirical
notes at ``docs/plans/cc-cli-notes.md:41-42`` already show the CLI
advertising ``-4-6`` while earlier drafts of this file hard-coded
``-4-5``).

Adapter code already emits ``--model <value>`` when ``SpawnConfig.model``
is set (``app/cc_adapter/adapter.py`` build_cmd) — this module is *not*
consumed by the adapter directly; it's the source of truth for the
router's catalog response and for validating user-supplied model ids.
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
        id="sonnet",
        name="Claude Sonnet",
        description="Balanced capability and latency. Default pick.",
    ),
    ModelInfo(
        id="opus",
        name="Claude Opus",
        description="Highest capability; slower and more expensive.",
    ),
    ModelInfo(
        id="haiku",
        name="Claude Haiku",
        description="Fastest and cheapest; for lightweight tasks.",
    ),
)


MODEL_IDS: frozenset[str] = frozenset(m.id for m in MODELS)


def is_valid_model_id(mid: str) -> bool:
    """Accept a model id iff it's in :data:`MODELS`."""
    return mid in MODEL_IDS
