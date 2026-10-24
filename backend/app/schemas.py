"""Pydantic schemas for API requests and background-job payload communication.

This module contains:
- DTOs for job payload passing (SubscriptionDTO, MediaDetailsDTO, DownloadJobDTO)
- API response schemas (MediaStats, TaskStats, etc.)
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, model_validator

from models import AudioQuality, DownloadQuality, JobType, MediaType, TaskStatus


def apply_transcript_only_defaults(options) -> None:
    """Force the options a transcript-only request implies, in place.

    Its Whisper fallback is an audio download, and the whole point is the transcript, so
    whatever the client sent for audio_only and generate_transcript is overridden. Takes any
    object carrying those three attributes (a request schema or a Subscription row).
    """
    if options.media_type == MediaType.TRANSCRIPT:
        options.audio_only = True
        options.generate_transcript = True


# --- DTOs for background-job payloads ---


class DictOrOrmMixin:
    @classmethod
    def from_orm(cls, obj):
        if isinstance(obj, dict):
            return cls(**obj)
        return cls.model_validate(obj)


class SubscriptionDTO(DictOrOrmMixin, BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    url: str
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
    download_quality: DownloadQuality = DownloadQuality.BEST
    audio_quality: AudioQuality = AudioQuality.BEST
    user_id: int | None = None


class MediaDetailsDTO(DictOrOrmMixin, BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    url: str
    media_type: MediaType
    channel: str | None = None
    title: str | None = None
    playlist_index: int | None = None
    file_path: str | None = None
    file_size_bytes: int | None = None
    status: TaskStatus = TaskStatus.NONE
    release_timestamp: datetime | None = None
    duration: float | None = None
    thumbnail_path: str | None = None
    download_task_record_id: int | None = None
    transcript_task_record_id: int | None = None
    owner_id: int | None = None


# An unannotated attribute on a BaseModel body would be captured by Pydantic's
# metaclass, so the constant must live outside the class.
#
# Embedded/context fields never read off an ORM row: DownloadJob's
# `subscription`/`media_details` Relationship attributes share these names,
# and touching them on a detached row lazy-loads or raises.
_DOWNLOAD_JOB_CONTEXT_FIELDS = frozenset(
    {
        'subscription',
        'media_details',
        'pending_media_details',
        'playlist_name',
        'source_playlist_url',
        'placeholder_task_id',
        'priority',
    }
)


class DownloadJobDTO(BaseModel):
    """Data transfer object for DownloadJob with embedded related data."""

    model_config = ConfigDict(from_attributes=True)

    # Core fields from DownloadJob ORM model
    id: int | None = None
    url: str
    audio_only: bool = False
    download_playlist: bool = False
    overwrite: bool = False
    media_type: MediaType | None = None
    channel: str | None = None
    title: str | None = None
    job_type: JobType = JobType.NORMAL_DOWNLOAD
    generate_transcript: bool = False
    download_quality: DownloadQuality = DownloadQuality.BEST
    audio_quality: AudioQuality = AudioQuality.BEST
    existing_media_details_id: int | None = None
    subscription_id: int | None = None
    media_details_id: int | None = None
    user_id: int | None = None

    # Embedded DTOs
    subscription: SubscriptionDTO | None = None
    media_details: MediaDetailsDTO | None = None

    # Additional context
    playlist_name: str | None = None
    pending_media_details: MediaDetailsDTO | None = None
    source_playlist_url: str | None = None  # YouTube playlist URL for auto-creating app playlists

    # task_id of the RESOLVING TaskRecord this job will adopt as its download row.
    # Never used as a JobSpec.task_id: the chain is dispatched from inside the still-
    # running populate job, so _submit_nowait's idempotence guard would see the id
    # already in _handles and silently drop the download.
    placeholder_task_id: str | None = None

    # Queue priority for the download chain. None ranks by source: direct beats subscription.
    priority: int | None = None

    @classmethod
    def from_orm(cls, obj, **extra_fields) -> 'DownloadJobDTO':
        """Create a DownloadJobDTO from an ORM model, dict, or with extra fields.

        Args:
            obj: DownloadJob ORM model or dict
            **extra_fields: Additional fields like subscription, media_details, playlist_name
        """
        if isinstance(obj, dict):
            return cls(**{**obj, **extra_fields})

        data = {
            name: getattr(obj, name)
            for name in cls.model_fields
            if name not in _DOWNLOAD_JOB_CONTEXT_FIELDS
        }
        data.update(extra_fields)

        return cls(**data)


# --- API Response/Request Schemas ---


class DownloadRequest(BaseModel):
    """Request body for POST /ytdl/.

    Deliberately not the DownloadJob table model: binding that would let a client set
    server-managed columns — subscription_id flows into shared-access grants for a
    subscription the caller may not own, and existing_media_details_id skips
    MediaDetails persistence entirely. Unknown fields are ignored, so stale clients
    sending them are harmless.
    """

    url: str
    audio_only: bool = False
    download_playlist: bool = False
    overwrite: bool = False
    media_type: MediaType | None = None
    channel: str | None = None
    title: str | None = None
    generate_transcript: bool = False
    download_quality: DownloadQuality = DownloadQuality.BEST
    audio_quality: AudioQuality = AudioQuality.BEST

    @model_validator(mode='after')
    def _transcript_only_defaults(self) -> 'DownloadRequest':
        apply_transcript_only_defaults(self)
        return self


class PlaybackStateUpdate(BaseModel):
    """Schema for updating per-user playback state."""

    playback_position: float | None = None
    last_accessed: datetime | None = None


class MediaStats(BaseModel):
    """Media library statistics."""

    total_downloads: int = 0
    total_transcript_blocks: int = 0
    downloads_with_transcripts: int = 0


class TaskStats(BaseModel):
    """Task queue statistics."""

    queued_total: int = 0
    queued_downloads: int = 0
    queued_transcripts: int = 0
    processing: int = 0
    failed: int = 0
    retry: int = 0
    not_ready: int = 0
    completed_24h: int = 0


class RetryTaskRequest(BaseModel):
    """Request body for retrying a task."""

    retry_downstream: bool = True
    overwrite: bool = False  # For download tasks: force overwrite (hard retry)


class BulkCancelRequest(BaseModel):
    """Request body for bulk cancelling tasks."""

    task_ids: list[str]


class BulkDeleteRequest(BaseModel):
    """Request body for bulk deleting tasks."""

    record_ids: list[int]


class BulkRetryRequest(BaseModel):
    """Request body for bulk retrying tasks."""

    record_ids: list[int]
    retry_downstream: bool = True
    overwrite: bool = False


class TagSetRequest(BaseModel):
    """Request body for setting tags on a media item."""

    tag_names: list[str]


class TagRenameRequest(BaseModel):
    """Request body for renaming a tag."""

    name: str


class RatingUpdateRequest(BaseModel):
    """Request body for setting/updating a rating."""

    rating: int


class ShareRequest(BaseModel):
    """Request model for sharing an entity with another user."""

    user_id: int


class BulkMediaDeleteRequest(BaseModel):
    """Request body for deleting multiple media items in one call."""

    media_details_ids: list[int]
    keep_transcripts: bool = False


class BulkTagRequest(BaseModel):
    """Request body for adding tags to multiple media items in one call."""

    media_details_ids: list[int]
    tag_names: list[str]


class BulkShareRequest(BaseModel):
    """Request body for sharing multiple entities with multiple users in one call."""

    entity_ids: list[int]
    user_ids: list[int]
