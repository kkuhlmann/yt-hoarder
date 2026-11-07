"""Scope personal access tokens to MCP or the browser extension

Every token that exists so far was made for MCP, so the server default backfills them as
MCP and is then dropped: new rows always name their scope.

Revision ID: add_token_scope
Revises: adopt_tv_samsung_client
Create Date: 2026-10-28

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'add_token_scope'
down_revision: str | None = 'adopt_tv_samsung_client'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

token_scope = sa.Enum('MCP', 'EXTENSION', name='tokenscope')


def upgrade() -> None:
    # add_column, unlike create_table, does not create the enum type it names.
    token_scope.create(op.get_bind(), checkfirst=True)
    op.add_column(
        'personal_access_tokens',
        sa.Column('scope', token_scope, nullable=False, server_default='MCP'),
    )
    op.alter_column('personal_access_tokens', 'scope', server_default=None)


def downgrade() -> None:
    op.drop_column('personal_access_tokens', 'scope')
    token_scope.drop(op.get_bind(), checkfirst=True)
