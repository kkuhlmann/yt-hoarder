"""Follow yt-dlp 2026.11.23's default player clients

The stored `app_settings` lists are the ones actually used at runtime — `models.py`'s
defaults only seed a fresh row — so a default change alone would leave every existing
install on the old order. yt-dlp 2026.11.23 changed two defaults, and this rewrites the
stored lists so existing installs follow without a Settings-UI visit:

- `web` left the anonymous default (yt-dlp PR #18131). Anonymously it offers only SABR
  formats, and while yt-dlp skips the webpage player response (its temporary STS
  workaround) it costs an extra player request for nothing.
- `web_embedded` moved behind `tv_samsung` in the authenticated default (yt-dlp PR #18051),
  since a YouTube experiment now requires it a GVS PO token (yt-dlp #17989).

Revision ID: drop_anonymous_web_client
Revises: add_token_scope
Create Date: 2026-11-26

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'drop_anonymous_web_client'
down_revision: str | None = 'add_token_scope'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_PLAYER_CLIENTS = '["tv_samsung", "visionos", "tv_simply", "web_safari", "web_embedded"]'
NEW_COOKIES_PLAYER_CLIENTS = '["tv_samsung", "web_embedded", "web", "web_safari", "mweb"]'

OLD_PLAYER_CLIENTS = '["tv_samsung", "visionos", "tv_simply", "web_safari", "web", "web_embedded"]'
OLD_COOKIES_PLAYER_CLIENTS = '["web_embedded", "tv_samsung", "web", "web_safari", "mweb"]'


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
