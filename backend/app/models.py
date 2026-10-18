from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Column,
    Index,
    Sequence,
    String,
    UniqueConstraint,
    text,
)
from sqlmodel import Field, Relationship, SQLModel

if TYPE_CHECKING:
    pass


def utc_now() -> datetime:
    """Return current UTC time as a naive datetime.

    PostgreSQL TIMESTAMP columns (without timezone) require naive datetimes.
    asyncpg is strict about this - it will reject timezone-aware datetimes.
    """
    return datetime.now(UTC).replace(tzinfo=None)


class MediaType(str, Enum):
    AUDIO = 'AUDIO'
    VIDEO = 'VIDEO'


class JobType(str, Enum):
    NORMAL_DOWNLOAD = 'NORMAL_DOWNLOAD'
    PLAYLIST_DOWNLOAD = 'PLAYLIST_DOWNLOAD'  # One-off playlist download (not subscription)
    PLAYLIST_SUBSCRIPTION = 'PLAYLIST_SUBSCRIPTION'
    CHANNEL_SUBSCRIPTION = 'CHANNEL_SUBSCRIPTION'


class TaskType(str, Enum):
    DOWNLOAD = 'DOWNLOAD'
    MEDIA_CONVERSION = 'MEDIA_CONVERSION'
    TRANSCRIPT_GENERATION = 'TRANSCRIPT_GENERATION'
    CLIP_GENERATION = 'CLIP_GENERATION'
    SPRITE_GENERATION = 'SPRITE_GENERATION'


class TaskStatus(str, Enum):
    NONE = 'NONE'
    RESOLVING = 'RESOLVING'
    QUEUED = 'QUEUED'
    IN_PROGRESS = 'IN_PROGRESS'
    POSTPROCESSING = 'POSTPROCESSING'
    COMPLETE = 'COMPLETE'
    RETRY = 'RETRY'
    FAILED = 'FAILED'
    SKIPPED = 'SKIPPED'
    UPSTREAM_FAILED = 'UPSTREAM_FAILED'
    CANCELLED = 'CANCELLED'
    DELETED = 'DELETED'
    NOT_READY = 'NOT_READY'


class DownloadQuality(str, Enum):
    BEST = 'BEST'
    Q1440P = '1440P'
    Q1080P = '1080P'
    Q720P = '720P'
    Q480P = '480P'
    Q360P = '360P'


class AudioQuality(str, Enum):
    """Audio bitrate tier for audio-only downloads (downward re-encode ladder)."""

    BEST = 'BEST'
    K128 = '128K'
    K96 = '96K'
    K64 = '64K'
    K48 = '48K'


class SourceType(str, Enum):
    DIRECT = 'direct'
    PLAYLIST = 'playlist'
    SUBSCRIPTION = 'subscription'


# All valid yt-dlp YouTube player clients, grouped by GVS PO-token policy. bgutil-pot
# supplies those tokens in the Docker image, so a PO-token client is usable here — but a
# client that needs no token still degrades more gracefully, hence the ordering.
# Capabilities below are read off yt-dlp's INNERTUBE_CLIENTS (extractor/youtube/_base.py);
# re-check them on a yt-dlp bump, since a client's policy can change under a stable name.
#
# Cookie support for age-restricted content:
#   SUPPORTS cookies: web, web_safari, mweb, web_creator, web_embedded, web_music, tv, tv_downgraded
#   NO cookie support: visionos, android_vr, android, ios, tv_simply
VALID_PLAYER_CLIENTS = [
    # No PO token required
    'visionos',  # No PO token, no JS player either, no cookies support
    'tv',  # No PO token, may have DRM
    'tv_downgraded',  # No PO token, cookie-capable
    'web_embedded',  # No PO token, only embeddable videos (yt-dlp's first choice with cookies)
    # PO token supported (provided automatically by bgutil-pot)
    'web',  # PO token supported, only SABR formats
    'web_safari',  # PO token supported, HLS (m3u8) formats
    'tv_simply',  # PO token required (GVS), no cookies support
    'ios',  # Requires iOSGuard PO token (not provided by bgutil-pot), no cookies support
    'mweb',  # PO token required (GVS), mobile web
    'web_music',  # PO token required (GVS), YouTube Music
    'web_creator',  # PO token required (GVS), requires cookies
    'android',  # PO token required (GVS/Player), no cookies support
    # Kept selectable, but YouTube 403s every format it returns as of 2026-08-17, which
    # is why yt-dlp dropped it from its own defaults (yt-dlp PR #17461). Do not default to it.
    'android_vr',  # PO token required (GVS), no cookies support
]

# visionos leads because it is the only client needing neither a PO token nor a JS player,
# and it is yt-dlp's own first anonymous choice since 2026.08.19. The rest is depth yt-dlp's
# two-client default does not carry: a PO-token client, an HLS client, and an embed fallback.
DEFAULT_PLAYER_CLIENTS = ['visionos', 'tv_simply', 'web_safari', 'web', 'web_embedded']

# Default player clients when cookies are active - only clients that support cookie-based age-gate
# bypass, so visionos cannot lead here. Order follows yt-dlp's own authenticated default
# (web_embedded, tv_downgraded, web), then adds the two cookie-capable clients it leaves out.
DEFAULT_COOKIES_PLAYER_CLIENTS = ['web_embedded', 'tv_downgraded', 'web', 'web_safari', 'mweb']


class User(SQLModel, table=True):
    __tablename__ = 'users'

    id: int | None = Field(default=None, primary_key=True)
    username: str = Field(unique=True, index=True)
    password_hash: str
    is_admin: bool = Field(default=False)
    is_approved: bool = Field(default=False)
    storage_limit_bytes: int | None = Field(default=None, sa_type=BigInteger)
    geo_background_preset: str | None = Field(default=None)
    geo_background_filename: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: utc_now())

    password_reset_requested_at: datetime | None = Field(default=None)
    must_change_password: bool = Field(default=False)
    # Tokens whose `iat` predates this are rejected by the auth middleware, so a
    # password change evicts sessions on other devices instead of leaving them
    # valid until the (30-day) token expires.
    password_changed_at: datetime | None = Field(default=None)
    recovery_code_hash: str | None = Field(default=None)
    recovery_code_expires_at: datetime | None = Field(default=None)


class PersonalAccessToken(SQLModel, table=True):
    """A bearer credential for the MCP endpoint, acting as one user.

    Only a SHA-256 of the token is stored. The token carries 32 random bytes, so a
    fast hash is safe, where bcrypt's ~250 ms would be paid on every MCP request.
    """

    __tablename__ = 'personal_access_tokens'

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key='users.id', ondelete='CASCADE', index=True)
    name: str = Field(max_length=64)
    token_hash: str = Field(unique=True, index=True)
    token_prefix: str
    created_at: datetime = Field(default_factory=lambda: utc_now())
    last_used_at: datetime | None = Field(default=None)
    expires_at: datetime | None = Field(default=None)


class MediaDetails(SQLModel, table=True):
    __tablename__ = 'media_details'
    __table_args__ = (
        UniqueConstraint('url', 'media_type', name='uq_media_details_url_type'),
        Index('ix_media_details_search', 'channel', 'title'),
    )

    id: int | None = Field(default=None, primary_key=True)
    url: str = Field(index=True)
    media_type: MediaType
    channel: str | None = None
    title: str | None = None
    playlist_index: int | None = None
    file_path: str | None = None
    file_size_bytes: int | None = Field(default=None, sa_type=BigInteger)
    summary: str | None = None
    release_timestamp: datetime | None = None
    duration: float | None = None  # Media runtime in seconds
    thumbnail_path: str | None = None
    status: TaskStatus = TaskStatus.NONE
    created_at: datetime = Field(default_factory=lambda: utc_now())
    downloaded_at: datetime | None = None

    # Earliest a subscription tick should re-evaluate this URL. Only set on NOT_READY
    # rows (unreleased or unavailable videos); NULL means due now.
    next_check_at: datetime | None = Field(default=None)

    download_task_record_id: int | None = Field(default=None, foreign_key='task_records.id')
    transcript_task_record_id: int | None = Field(default=None, foreign_key='task_records.id')

    # Owner (nullable for backward compatibility — NULL means pre-multi-user data)
    owner_id: int | None = Field(default=None, foreign_key='users.id', ondelete='SET NULL')

    transcript_blocks: list['TranscriptBlock'] = Relationship(
        back_populates='media_details',
        sa_relationship_kwargs={'cascade': 'all, delete-orphan'},
    )
    download_jobs: list['DownloadJob'] = Relationship(
        back_populates='media_details',
        sa_relationship_kwargs={'cascade': 'all, delete-orphan'},
    )
    download_task_record: Optional['TaskRecord'] = Relationship(
        sa_relationship_kwargs={'foreign_keys': '[MediaDetails.download_task_record_id]'}
    )
    transcript_task_record: Optional['TaskRecord'] = Relationship(
        sa_relationship_kwargs={'foreign_keys': '[MediaDetails.transcript_task_record_id]'}
    )
    clips: list['Clip'] = Relationship(
        back_populates='media_details',
        sa_relationship_kwargs={'foreign_keys': '[Clip.media_details_id]'},
    )
    playlist_associations: list['PlaylistMedia'] = Relationship(
        back_populates='media_details',
        sa_relationship_kwargs={'cascade': 'all, delete-orphan'},
    )


class Subscription(SQLModel, table=True):
    __tablename__ = 'subscriptions'
    __table_args__ = (
        UniqueConstraint(
            'url', 'string_match', 'audio_only', 'user_id', name='uq_subscriptions_url_match_user'
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    url: str = Field(index=True)
    channel: str | None = None
    enabled: bool = True
    audio_only: bool = False
    media_type: MediaType | None = None
    string_match: str | None = None
    overwrite: bool = False
    date_filter: datetime | None = None
    min_duration_seconds: int | None = None
    max_duration_seconds: int | None = None
    job_type: JobType | None = None
    generate_transcript: bool = False
    download_quality: DownloadQuality = Field(default=DownloadQuality.BEST, sa_type=String)
    audio_quality: AudioQuality = Field(default=AudioQuality.BEST, sa_type=String)
    created_at: datetime = Field(default_factory=lambda: utc_now())

    # User who created this subscription (nullable for backward compatibility)
    user_id: int | None = Field(default=None, foreign_key='users.id', ondelete='CASCADE')

    download_jobs: list['DownloadJob'] = Relationship(
        back_populates='subscription',
        sa_relationship_kwargs={'cascade': 'all, delete-orphan'},
    )


class DownloadJob(SQLModel, table=True):
    __tablename__ = 'download_jobs'
    __table_args__ = (Index('ix_download_jobs_url', 'url'),)

    id: int | None = Field(default=None, primary_key=True)
    url: str
    audio_only: bool = False
    download_playlist: bool = False
    overwrite: bool = False
    media_type: MediaType | None = None
    channel: str | None = None
    title: str | None = None
    job_type: JobType = JobType.NORMAL_DOWNLOAD
    generate_transcript: bool = False
    download_quality: DownloadQuality = Field(default=DownloadQuality.BEST, sa_type=String)
    audio_quality: AudioQuality = Field(default=AudioQuality.BEST, sa_type=String)
    existing_media_details_id: int | None = None
    created_at: datetime = Field(default_factory=lambda: utc_now())

    subscription_id: int | None = Field(default=None, foreign_key='subscriptions.id')
    media_details_id: int | None = Field(
        default=None, foreign_key='media_details.id', ondelete='CASCADE'
    )

    # User who initiated this download (nullable for backward compatibility)
    user_id: int | None = Field(default=None, foreign_key='users.id', ondelete='SET NULL')

    subscription: Subscription | None = Relationship(back_populates='download_jobs')
    media_details: MediaDetails | None = Relationship(back_populates='download_jobs')


class TranscriptBlock(SQLModel, table=True):
    __tablename__ = 'transcript_blocks'
    __table_args__ = (Index('ix_transcript_blocks_media', 'media_details_id'),)

    id: int | None = Field(default=None, primary_key=True)
    start_time: float | None = None
    end_time: float | None = None
    text: str | None = None
    transcript_model: str | None = None
    embedding_model: str | None = None

    media_details_id: int = Field(foreign_key='media_details.id', ondelete='CASCADE')

    media_details: MediaDetails = Relationship(back_populates='transcript_blocks')


class TaskRecord(SQLModel, table=True):
    __tablename__ = 'task_records'
    __table_args__ = (
        Index('ix_task_records_task_id', 'task_id', unique=True),
        Index('ix_task_records_status', 'status'),
        # Prevents duplicate active tasks for the same URL/media_type. Mirrors the
        # index created in the initial-schema migration so create_all installs and
        # tests enforce the same guarantee.
        Index(
            'ix_task_records_active_unique',
            'task_type',
            'download_job_url',
            'media_type',
            unique=True,
            postgresql_where=text(
                "status IN ('RESOLVING', 'QUEUED', 'IN_PROGRESS', 'RETRY', 'CANCELLED') "
                'AND download_job_url IS NOT NULL'
            ),
        ),
        # Backs the NOT_READY placeholder upsert in _record_not_ready_task — two
        # subscription chains deferring the same unreleased video in the same tick
        # must not create duplicate placeholders (concurrency_unique_indexes migration).
        Index(
            'ix_task_records_not_ready_unique',
            'task_type',
            'download_job_url',
            'media_type',
            unique=True,
            postgresql_where=text(
                "status = 'NOT_READY' AND deleted_at IS NULL AND download_job_url IS NOT NULL"
            ),
        ),
        # Backs the retry scheduler's due-rows scan (status=RETRY AND next_retry_at<=now).
        Index(
            'ix_task_records_retry_due',
            'next_retry_at',
            postgresql_where=text("status = 'RETRY'"),
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    task_id: str = Field(unique=True)
    upstream_task_ids: list[str] | None = Field(default=None, sa_column=Column(JSON))
    task_type: TaskType
    percent_complete: int = 0
    eta_seconds: float | None = None
    status: TaskStatus = TaskStatus.NONE
    status_message: str | None = None
    created_at: datetime = Field(default_factory=lambda: utc_now())
    updated_at: datetime = Field(default_factory=lambda: utc_now())
    title: str | None = None
    channel: str | None = None
    release_timestamp: datetime | None = None
    media_type: MediaType | None = None

    # For download task existence checking (URL to prevent duplicate downloads)
    download_job_url: str | None = None

    # Queue ordering - set at dispatch time (task_queue_sequence Postgres sequence)
    queue_sequence: int | None = Field(default=None, index=True)

    # Priority (0=highest, 9=lowest). Direct downloads=1, subscription=5, prioritized=0.
    priority: int | None = Field(default=None)

    # Current download phase: 'VIDEO', 'AUDIO', or None (merged/unknown)
    download_phase: str | None = None

    # Soft delete - set when record is "deleted" instead of hard delete
    deleted_at: datetime | None = Field(default=None)

    # User who initiated this task (nullable for backward compatibility)
    user_id: int | None = Field(default=None, foreign_key='users.id', ondelete='SET NULL')

    # Automatic-retry scheduling: attempt counter (0 on the first run; also
    # drives cookie use on retry attempts) and when a RETRY-status task
    # becomes due for re-dispatch by the retry scheduler.
    retry_count: int = Field(default=0, sa_column_kwargs={'server_default': '0'})
    next_retry_at: datetime | None = Field(default=None)

    # Short display code for the last failure (orchestrator.error_codes), so a RETRY
    # row can lead with what went wrong instead of the truncated exception text.
    # Cleared on start/success — a stale code outlives its error otherwise.
    error_code: str | None = Field(default=None)

    # When a download parked in the pre-download rate-limit sleep wakes up. An absolute
    # deadline rather than a remaining-seconds count, so the UI countdown survives a
    # reload and the sleeping job never has to re-publish while it waits.
    sleep_until: datetime | None = Field(default=None)

    # Serialized download job for a RESOLVING placeholder, whose work exists only as an
    # in-memory JobHandle until populate resolves it. Startup recovery re-submits the
    # populate job from this; cleared once the chain adopts the row.
    pending_payload: dict | None = Field(default=None, sa_column=Column(JSON))


# Monotonic dispatch counter backing TaskRecord.queue_sequence. Attached to
# SQLModel metadata so create_all provisions it for tests; production gets it
# via the add_orchestrator_fields migration.
TASK_QUEUE_SEQUENCE = Sequence('task_queue_sequence', metadata=SQLModel.metadata)


COOKIE_FILE_PATH = '/data/cookies.txt'
BACKGROUNDS_DIR = '/data/backgrounds'

# Shared by the /auth endpoints and the reset_password CLI, which must not drift.
MIN_PASSWORD_LENGTH = 6


class CookiesMode(str, Enum):
    ALWAYS = 'ALWAYS'
    RETRIES_ONLY = 'RETRIES_ONLY'
    NEVER = 'NEVER'


class AppSettings(SQLModel, table=True):
    """Single-row table (id=1) with typed columns for all configurable settings.
    Settings are read at task start time, so changes take effect without worker restart.
    """

    __tablename__ = 'app_settings'

    id: int | None = Field(default=None, primary_key=True)

    # Download settings
    download_sleep_seconds: int = Field(default=60)
    # yt-dlp throttling, 0 = off. ratelimit is per job body, so it multiplies with
    # downloads_lane_concurrency; sleep_interval_requests acts during extraction, not transfer.
    download_rate_limit_kbps: int = Field(default=0)
    request_sleep_seconds: int = Field(default=0)
    cleanup_age_hours: int = Field(default=24)
    player_client: list[str] = Field(
        default_factory=lambda: DEFAULT_PLAYER_CLIENTS.copy(), sa_column=Column(JSON)
    )
    cookies_mode: str = Field(default=CookiesMode.RETRIES_ONLY.value)
    cookies_uploaded_at: datetime | None = Field(default=None)
    cookies_player_client: list[str] = Field(
        default_factory=lambda: DEFAULT_COOKIES_PLAYER_CLIENTS.copy(), sa_column=Column(JSON)
    )

    # Transcript settings
    transcript_chunk_duration: int = Field(default=600)
    transcript_block_duration: int = Field(default=20)
    force_whisper_transcription: bool = Field(default=False)

    # Frontend table settings
    subscription_table_page_size: int = Field(default=25)
    download_table_page_size: int = Field(default=25)

    # Subscription cron cadence, applied live (see orchestrator.scheduler.subscription_schedule)
    subscription_check_minutes: int = Field(default=10)

    # Orchestrator lane widths, applied live (see Orchestrator.set_lane_concurrency)
    default_lane_concurrency: int = Field(default=2)
    downloads_lane_concurrency: int = Field(default=1)
    subscriptions_lane_concurrency: int = Field(default=1)
    ml_lane_concurrency: int = Field(default=1)

    updated_at: datetime = Field(default_factory=lambda: utc_now())


class Clip(SQLModel, table=True):
    __tablename__ = 'clips'
    __table_args__ = (
        Index('ix_clips_media_details', 'media_details_id'),
        Index('ix_clips_created_at', 'created_at'),
    )

    id: int | None = Field(default=None, primary_key=True)
    media_details_id: int | None = Field(
        default=None, foreign_key='media_details.id', ondelete='SET NULL'
    )
    title: str
    description: str | None = None
    start_time: float
    end_time: float
    duration: float | None = None
    file_path: str | None = None
    media_type: MediaType
    status: TaskStatus = TaskStatus.QUEUED
    task_record_id: int | None = Field(default=None, foreign_key='task_records.id')
    created_at: datetime = Field(default_factory=lambda: utc_now())

    # Denormalized source info (preserved even if source is deleted)
    source_title: str | None = None
    source_channel: str | None = None

    # User who created this clip (nullable for backward compatibility)
    user_id: int | None = Field(default=None, foreign_key='users.id', ondelete='SET NULL')

    media_details: MediaDetails | None = Relationship(
        back_populates='clips',
        sa_relationship_kwargs={'foreign_keys': '[Clip.media_details_id]'},
    )
    task_record: Optional['TaskRecord'] = Relationship(
        sa_relationship_kwargs={'foreign_keys': '[Clip.task_record_id]'}
    )


class Playlist(SQLModel, table=True):
    __tablename__ = 'playlists'
    __table_args__ = (
        Index('ix_playlists_name', 'name'),
        # Backs the get-or-create in _handle_playlist_creation: parallel chains for
        # videos of the same playlist must not create duplicate playlists (duplicates
        # make every later scalar_one_or_none() lookup raise MultipleResultsFound).
        Index(
            'uq_playlists_source_url',
            'source_url',
            unique=True,
            postgresql_where=text('source_url IS NOT NULL'),
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    name: str  # Index defined in __table_args__
    description: str | None = None
    source_url: str | None = None  # YouTube playlist URL if auto-created
    created_at: datetime = Field(default_factory=lambda: utc_now())
    updated_at: datetime = Field(default_factory=lambda: utc_now())

    # User who created this playlist (nullable for backward compatibility)
    user_id: int | None = Field(default=None, foreign_key='users.id', ondelete='SET NULL')

    media_associations: list['PlaylistMedia'] = Relationship(
        back_populates='playlist',
        sa_relationship_kwargs={'cascade': 'all, delete-orphan'},
    )


class PlaylistMedia(SQLModel, table=True):
    """Association table linking playlists to media with ordering support."""

    __tablename__ = 'playlist_media'
    __table_args__ = (
        Index('ix_playlist_media_playlist_id', 'playlist_id'),
        Index('ix_playlist_media_media_details_id', 'media_details_id'),
        UniqueConstraint('playlist_id', 'media_details_id', name='uq_playlist_media'),
    )

    id: int | None = Field(default=None, primary_key=True)
    playlist_id: int = Field(
        foreign_key='playlists.id', ondelete='CASCADE'
    )  # Index defined in __table_args__
    media_details_id: int = Field(
        foreign_key='media_details.id', ondelete='CASCADE'
    )  # Index defined in __table_args__
    position: int  # 1-based ordering
    added_at: datetime = Field(default_factory=lambda: utc_now())

    playlist: 'Playlist' = Relationship(back_populates='media_associations')
    media_details: 'MediaDetails' = Relationship(back_populates='playlist_associations')


class MediaAccess(SQLModel, table=True):
    """Tracks which users have access to which media.

    When a user downloads media, they get a media_access row. If another user subscribes
    to the same channel, they get a media_access row pointing to the same MediaDetails
    (no re-download). The owner_id on MediaDetails tracks who originally downloaded it.

    source_type tracks HOW access was granted (see SourceType enum):
    - DIRECT: Explicitly shared or owner access
    - PLAYLIST: Granted via playlist sharing
    - SUBSCRIPTION: Granted via subscription sharing

    source_id is the playlist_id or subscription_id (0 for direct).
    Multiple rows can exist for the same user+media if access comes from different sources.
    """

    __tablename__ = 'media_access'
    __table_args__ = (
        UniqueConstraint(
            'user_id',
            'media_details_id',
            'source_type',
            'source_id',
            name='uq_media_access_user_media_source',
        ),
        Index('ix_media_access_user_id', 'user_id'),
        Index('ix_media_access_media_details_id', 'media_details_id'),
        Index('ix_media_access_source', 'source_type', 'source_id'),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key='users.id', ondelete='CASCADE')
    media_details_id: int = Field(foreign_key='media_details.id', ondelete='CASCADE')
    source_type: str = Field(default=SourceType.DIRECT)
    source_id: int = Field(default=0)
    created_at: datetime = Field(default_factory=lambda: utc_now())


class SubscriptionAccess(SQLModel, table=True):
    """Tracks which users have shared access to which subscriptions.

    The subscription owner is tracked by Subscription.user_id. This table tracks
    additional users who have been granted read access to the subscription and
    auto-receive media_access for new downloads from it.
    """

    __tablename__ = 'subscription_access'
    __table_args__ = (
        UniqueConstraint('user_id', 'subscription_id', name='uq_subscription_access'),
        Index('ix_subscription_access_user_id', 'user_id'),
        Index('ix_subscription_access_subscription_id', 'subscription_id'),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key='users.id', ondelete='CASCADE')
    subscription_id: int = Field(foreign_key='subscriptions.id', ondelete='CASCADE')
    created_at: datetime = Field(default_factory=lambda: utc_now())


class PlaylistAccess(SQLModel, table=True):
    """Tracks which users have shared access to which playlists.

    The playlist owner is tracked by Playlist.user_id. This table tracks
    additional users who have been granted read access to the playlist.
    """

    __tablename__ = 'playlist_access'
    __table_args__ = (
        UniqueConstraint('user_id', 'playlist_id', name='uq_playlist_access'),
        Index('ix_playlist_access_user_id', 'user_id'),
        Index('ix_playlist_access_playlist_id', 'playlist_id'),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key='users.id', ondelete='CASCADE')
    playlist_id: int = Field(foreign_key='playlists.id', ondelete='CASCADE')
    created_at: datetime = Field(default_factory=lambda: utc_now())


class ClipAccess(SQLModel, table=True):
    """Tracks which users have shared access to which clips.

    The clip owner is tracked by Clip.user_id. This table tracks
    additional users who have been granted read access to the clip.
    """

    __tablename__ = 'clip_access'
    __table_args__ = (
        UniqueConstraint('user_id', 'clip_id', name='uq_clip_access'),
        Index('ix_clip_access_user_id', 'user_id'),
        Index('ix_clip_access_clip_id', 'clip_id'),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key='users.id', ondelete='CASCADE')
    clip_id: int = Field(foreign_key='clips.id', ondelete='CASCADE')
    created_at: datetime = Field(default_factory=lambda: utc_now())


class Tag(SQLModel, table=True):
    """Per-user tag for organizing media."""

    __tablename__ = 'tags'
    __table_args__ = (
        UniqueConstraint('user_id', 'name', name='uq_tags_user_name'),
        Index('ix_tags_user_id', 'user_id'),
        Index('ix_tags_name', 'name'),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key='users.id')
    name: str = Field(max_length=50)
    created_at: datetime = Field(default_factory=lambda: utc_now())


class MediaTag(SQLModel, table=True):
    """Many-to-many join between media and tags, scoped per user."""

    __tablename__ = 'media_tags'
    __table_args__ = (
        UniqueConstraint(
            'user_id', 'media_details_id', 'tag_id', name='uq_media_tags_user_media_tag'
        ),
        Index('ix_media_tags_user_id', 'user_id'),
        Index('ix_media_tags_media_details_id', 'media_details_id'),
        Index('ix_media_tags_tag_id', 'tag_id'),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key='users.id')
    media_details_id: int = Field(foreign_key='media_details.id', ondelete='CASCADE')
    tag_id: int = Field(foreign_key='tags.id', ondelete='CASCADE')
    created_at: datetime = Field(default_factory=lambda: utc_now())


class MediaRating(SQLModel, table=True):
    """Per-user 5-star rating for media items."""

    __tablename__ = 'media_ratings'
    __table_args__ = (
        UniqueConstraint('user_id', 'media_details_id', name='uq_media_ratings_user_media'),
        CheckConstraint('rating >= 1 AND rating <= 5', name='ck_media_ratings_rating_range'),
        Index('ix_media_ratings_user_id', 'user_id'),
        Index('ix_media_ratings_media_details_id', 'media_details_id'),
        Index('ix_media_ratings_rating', 'rating'),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key='users.id')
    media_details_id: int = Field(foreign_key='media_details.id', ondelete='CASCADE')
    rating: int = Field(ge=1, le=5)
    created_at: datetime = Field(default_factory=lambda: utc_now())
    updated_at: datetime = Field(default_factory=lambda: utc_now())


class PlaybackState(SQLModel, table=True):
    """Per-user playback state for media items.

    Each user gets independent playback position, last_accessed, and access_count
    per media item. This replaces the old fields that lived directly on MediaDetails,
    which caused all users sharing a media item to share the same playback position.
    """

    __tablename__ = 'playback_state'
    __table_args__ = (
        UniqueConstraint('user_id', 'media_details_id', name='uq_playback_state_user_media'),
        Index('ix_playback_state_user_id', 'user_id'),
        Index('ix_playback_state_media_details_id', 'media_details_id'),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key='users.id')
    media_details_id: int = Field(foreign_key='media_details.id', ondelete='CASCADE')
    playback_position: float | None = None
    last_accessed: datetime | None = None
    access_count: int = Field(default=0)


# Default settings values - used for reset functionality
APP_SETTINGS_DEFAULTS = {
    'download_sleep_seconds': 60,
    'download_rate_limit_kbps': 0,
    'request_sleep_seconds': 0,
    'cleanup_age_hours': 24,
    'player_client': DEFAULT_PLAYER_CLIENTS.copy(),
    'transcript_chunk_duration': 600,
    'transcript_block_duration': 20,
    'cookies_mode': CookiesMode.RETRIES_ONLY.value,
    'cookies_player_client': DEFAULT_COOKIES_PLAYER_CLIENTS.copy(),
    'force_whisper_transcription': False,
    'subscription_table_page_size': 25,
    'download_table_page_size': 25,
    'subscription_check_minutes': 10,
    'default_lane_concurrency': 2,
    'downloads_lane_concurrency': 1,
    'subscriptions_lane_concurrency': 1,
    'ml_lane_concurrency': 1,
}
