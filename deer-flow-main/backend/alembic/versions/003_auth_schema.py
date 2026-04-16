"""auth schema: users + auth_sessions for M5 Task 5.2 better-auth-compatible session cookie auth.

Single-tenant homelab posture: no OAuth, no sign-up endpoint, user creation
is admin-CLI-only (``python -m app.admin create-user``).

``users.password_hash`` stores argon2 output (argon2-cffi) — never the plain
password. ``auth_sessions.id`` is the opaque cookie value itself
(``secrets.token_hex(16)`` → 32 hex chars, 128 bits of entropy); we do not
hash it at storage time since guessing is infeasible and hashing would
complicate the lookup path.
"""

import sqlalchemy as sa

from alembic import op

revision = "003"
down_revision = "002"


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("email", sa.String, nullable=False, unique=True),
        sa.Column("password_hash", sa.String, nullable=False),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column(
            "is_admin",
            sa.Boolean,
            nullable=False,
            server_default=sa.false(),
            default=False,
        ),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column(
            "user_id",
            sa.String,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime, nullable=False),
        sa.Column(
            "last_seen_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("user_agent", sa.String, nullable=True),
        sa.Column("ip", sa.String, nullable=True),
    )
    op.create_index("ix_auth_sessions_user", "auth_sessions", ["user_id"])
    op.create_index("ix_auth_sessions_expires", "auth_sessions", ["expires_at"])


def downgrade():
    op.drop_index("ix_auth_sessions_expires")
    op.drop_index("ix_auth_sessions_user")
    op.drop_table("auth_sessions")
    op.drop_index("ix_users_email")
    op.drop_table("users")
