"""memory_facts: user-curated facts surfaced to the agent as context.

One row per fact; the harmony v1 "memory" UI lets the user add, edit,
and delete these directly. Fact categories (``context`` by default) and
confidence scores (0.0-1.0) are opaque to the backend — they're just
surfaced back in ``GET /api/memory`` so the frontend can render them.

``source`` is free-form. Manual-entry facts store ``"manual"``; if a
future auto-distillation pipeline ever lands, it can write a thread id
and the frontend already links to that thread.

No summaries table yet: the deer-flow summary blocks
(``user.workContext``, ``history.recentMonths``, ...) require a
server-side summarization pipeline that's out of scope for this cut.
The memory router returns empty summary blobs so the frontend's
``UserMemory`` shape stays compatible.
"""

import sqlalchemy as sa

from alembic import op

revision = "004"
down_revision = "003"


def upgrade():
    op.create_table(
        "memory_facts",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("user_id", sa.String, nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("category", sa.String, nullable=False, server_default="context"),
        # SQLite stores REAL. Range [0.0, 1.0] is enforced in the router,
        # not by a CHECK constraint, so the alembic migration stays simple
        # and existing rows can't fail a validation retroactively.
        sa.Column("confidence", sa.Float, nullable=False, server_default="0.8"),
        sa.Column("source", sa.String, nullable=False, server_default="manual"),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index("ix_memory_facts_user", "memory_facts", ["user_id"])


def downgrade():
    op.drop_index("ix_memory_facts_user")
    op.drop_table("memory_facts")
