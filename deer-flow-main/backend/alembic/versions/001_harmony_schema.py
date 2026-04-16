"""harmony schema"""
from alembic import op
import sqlalchemy as sa

revision = "001"
down_revision = None


def upgrade():
    op.create_table(
        "mcp_servers",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("user_id", sa.String, nullable=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("transport", sa.String, nullable=False),
        sa.Column("command", sa.String, nullable=True),
        sa.Column("args_json", sa.String, nullable=True),
        sa.Column("url", sa.String, nullable=True),
        sa.Column("headers_json", sa.String, nullable=True),
        sa.Column("env_json", sa.String, nullable=True),
        sa.Column("enabled", sa.Boolean, default=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index("ix_mcp_user_name", "mcp_servers", ["user_id", "name"], unique=True)

    op.create_table(
        "skills",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("user_id", sa.String, nullable=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("source", sa.String, nullable=False),
        sa.Column("path", sa.String, nullable=False),
        sa.Column("enabled", sa.Boolean, default=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index("ix_skills_user_name", "skills", ["user_id", "name"], unique=True)

    op.create_table(
        "user_prefs",
        sa.Column("user_id", sa.String, primary_key=True),
        sa.Column("default_model", sa.String, nullable=True),
        sa.Column("extras_json", sa.String, nullable=True),
    )

    op.create_table(
        "cc_thread_session",
        sa.Column("thread_id", sa.String, primary_key=True),
        sa.Column("user_id", sa.String, nullable=True),
        sa.Column("session_id", sa.String, nullable=True),
        sa.Column("cwd", sa.String, nullable=False),
    )


def downgrade():
    op.drop_table("cc_thread_session")
    op.drop_table("user_prefs")
    op.drop_index("ix_skills_user_name")
    op.drop_table("skills")
    op.drop_index("ix_mcp_user_name")
    op.drop_table("mcp_servers")
