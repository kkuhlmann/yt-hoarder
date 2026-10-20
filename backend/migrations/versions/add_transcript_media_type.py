"""Add the TRANSCRIPT media type

Revision ID: add_transcript_media_type
Revises: add_personal_access_tokens
Create Date: 2026-10-18

Alone in its revision: Postgres rejects any statement naming an enum value added in the
same transaction, and env.py runs each revision in its own (transaction_per_migration).

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'add_transcript_media_type'
down_revision: str | None = 'add_personal_access_tokens'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE mediatype ADD VALUE IF NOT EXISTS 'TRANSCRIPT'")


def downgrade() -> None:
    # Postgres cannot drop a value from an enum type. The value is inert once no row uses it.
    pass
