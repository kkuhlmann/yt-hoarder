"""Adopt the tv_samsung player client for yt-dlp 2026.10.21

The stored `app_settings` lists are the ones actually used at runtime — `models.py`'s
defaults only seed a fresh row — so a default change alone would leave every existing
install on `tv_downgraded`, which fails with "The page needs to be reloaded" for sessions
YouTube serves its TV player-JS experiment (yt-dlp #17389). yt-dlp 2026.10.21 replaced it
with the new `tv_samsung` client in every default list (yt-dlp PR #17805); this rewrites the
stored lists so existing installs follow without a Settings-UI visit.

Revision ID: adopt_tv_samsung_client
Revises: transcript_only_subscriptions
Create Date: 2026-10-25

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'adopt_tv_samsung_client'
down_revision: str | None = 'transcript_only_subscriptions'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_PLAYER_CLIENTS = '["tv_samsung", "visionos", "tv_simply", "web_safari", "web", "web_embedded"]'
NEW_COOKIES_PLAYER_CLIENTS = '["web_embedded", "tv_samsung", "web", "web_safari", "mweb"]'

OLD_PLAYER_CLIENTS = '["visionos", "tv_simply", "web_safari", "web", "web_embedded"]'
OLD_COOKIES_PLAYER_CLIENTS = '["web_embedded", "tv_downgraded", "web", "web_safari", "mweb"]'


def _rewrite(player_client: str, cookies_player_client: str) -> None:
    op.execute(f"""
        UPDATE app_settings
        SET player_client = '{player_client}'::json,
            cookies_player_client = '{cookies_player_client}'::json,
            updated_at = NOW()
    """)


def upgrade() -> None:
    _rewrite(NEW_PLAYER_CLIENTS, NEW_COOKIES_PLAYER_CLIENTS)


def downgrade() -> None:
    _rewrite(OLD_PLAYER_CLIENTS, OLD_COOKIES_PLAYER_CLIENTS)
