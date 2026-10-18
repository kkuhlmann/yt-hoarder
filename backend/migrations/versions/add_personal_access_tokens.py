"""Add personal access tokens for the MCP endpoint

Revision ID: add_personal_access_tokens
Revises: realign_player_clients
Create Date: 2026-10-18

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'add_personal_access_tokens'
down_revision: str | None = 'realign_player_clients'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'personal_access_tokens',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('name', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column('token_hash', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('token_prefix', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('last_used_at', sa.DateTime(), nullable=True),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_personal_access_tokens_token_hash'),
        'personal_access_tokens',
        ['token_hash'],
        unique=True,
    )
    op.create_index(
        op.f('ix_personal_access_tokens_user_id'),
        'personal_access_tokens',
        ['user_id'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_personal_access_tokens_user_id'), table_name='personal_access_tokens')
    op.drop_index(op.f('ix_personal_access_tokens_token_hash'), table_name='personal_access_tokens')
    op.drop_table('personal_access_tokens')
