"""Key subscriptions on media_type instead of audio_only

Revision ID: transcript_only_subscriptions
Revises: add_transcript_media_type
Create Date: 2026-10-18

A transcript-only subscription is audio_only (its Whisper fallback downloads audio) but is
not an audio subscription, so audio_only can no longer tell the two apart in the unique key.

media_type is realigned from audio_only on every row first, not just the NULL ones: the
edit endpoint could flip audio_only without touching media_type, and two rows that disagree
that way would collide under the new key and fail the upgrade.

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'transcript_only_subscriptions'
down_revision: str | None = 'add_transcript_media_type'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE subscriptions SET media_type = CASE WHEN audio_only THEN 'AUDIO' ELSE 'VIDEO' END"
        '::mediatype'
    )
    op.drop_constraint('uq_subscriptions_url_match_user', 'subscriptions', type_='unique')
    op.create_unique_constraint(
        'uq_subscriptions_url_match_type_user',
        'subscriptions',
        ['url', 'string_match', 'media_type', 'user_id'],
    )


def downgrade() -> None:
    op.drop_constraint('uq_subscriptions_url_match_type_user', 'subscriptions', type_='unique')
    op.create_unique_constraint(
        'uq_subscriptions_url_match_user',
        'subscriptions',
        ['url', 'string_match', 'audio_only', 'user_id'],
    )
