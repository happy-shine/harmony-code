"""uploads: track per-thread uploaded files for audit/listing.

Rows are inserted by ``POST /api/threads/{tid}/uploads`` (Task 4.3)
whenever a client uploads a file; they are deleted by
``DELETE /api/threads/{tid}/uploads/{upload_id}``. The on-disk file
lives at ``<HARMONY_DATA_DIR>/threads/<tid>/user-data/uploads/<filename>``
and is what CC ultimately sees via its ``--add-dir`` flag.

No UNIQUE (thread_id, filename) constraint — users can re-upload under
the same name (new row; file on disk overwritten). ``user_id`` is
forward-compat for M5 auth; today the router always writes NULL.
"""

import sqlalchemy as sa

from alembic import op

revision = "002"
down_revision = "001"


def upgrade():
    op.create_table(
        "uploads",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("thread_id", sa.String, nullable=False),
        sa.Column("user_id", sa.String, nullable=True),
        sa.Column("filename", sa.String, nullable=False),
        sa.Column("size", sa.Integer, nullable=False),
        sa.Column("content_type", sa.String, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index("ix_uploads_thread", "uploads", ["thread_id"])


def downgrade():
    op.drop_index("ix_uploads_thread")
    op.drop_table("uploads")
