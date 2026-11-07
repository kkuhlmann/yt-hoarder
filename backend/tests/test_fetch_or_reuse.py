"""Tests for _fetch_or_reuse_media_details and its extracted helpers."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from models import MediaDetails, MediaType, SourceType, TaskStatus, utc_now
from schemas import DownloadJobDTO, MediaDetailsDTO
from tasks.media import (
    _fetch_or_reuse_media_details,
    _next_check_at,
    _reuse_or_delete_existing_media,
    _use_pending_or_fetch_fresh,
)
from ytdlp.info import EXTRACTION_TRANSIENT, EXTRACTION_UNAVAILABLE


def _make_dto(**overrides) -> DownloadJobDTO:
    """Create a minimal DownloadJobDTO for testing."""
    defaults = {
        'url': 'https://www.youtube.com/watch?v=abc123',
        'media_type': MediaType.AUDIO,
        'user_id': 1,
    }
    defaults.update(overrides)
    return DownloadJobDTO(**defaults)


def _make_media_details_orm(**overrides):
    """Create a mock MediaDetails ORM object."""
    md = MagicMock()
    md.id = overrides.get('id', 42)
    md.url = overrides.get('url', 'https://www.youtube.com/watch?v=abc123')
    md.media_type = overrides.get('media_type', MediaType.AUDIO)
    md.channel = overrides.get('channel', 'TestChannel')
    md.title = overrides.get('title', 'Test Video')
    md.status = overrides.get('status', TaskStatus.COMPLETE)
    md.release_timestamp = overrides.get('release_timestamp', datetime(2025, 1, 1, tzinfo=UTC))
    md.duration = overrides.get('duration', 300)
    md.owner_id = overrides.get('owner_id', 1)
    md.file_path = overrides.get('file_path', '/mnt/audio/test.mp3')
    md.file_size_bytes = overrides.get('file_size_bytes', 1000)
    md.thumbnail_path = overrides.get('thumbnail_path')
    return md


class TestReuseOrDeleteExistingMedia:
    """Tests for _reuse_or_delete_existing_media helper."""

    @patch('tasks.media.md_repo')
    @patch('tasks.media.media_details_to_dto')
    def test_reuses_existing_media_with_release_timestamp(self, mock_to_dto, mock_md_repo):
        """When DB has valid MediaDetails with release_timestamp, reuse it."""
        orm_obj = _make_media_details_orm()
        mock_md_repo.sync_get_media_details_by_url_and_media_type.return_value = orm_obj
        expected_dto = MediaDetailsDTO(
            url='https://www.youtube.com/watch?v=abc123',
            media_type=MediaType.AUDIO,
            channel='TestChannel',
            title='Test Video',
            release_timestamp=datetime(2025, 1, 1, tzinfo=UTC),
            owner_id=1,
        )
        mock_to_dto.return_value = expected_dto

        dto = _make_dto()
        result = _reuse_or_delete_existing_media(dto)

        mock_md_repo.sync_get_media_details_by_url_and_media_type.assert_called_once_with(
            'https://www.youtube.com/watch?v=abc123', 'AUDIO'
        )
        assert result.media_details == expected_dto
        assert result.existing_media_details_id == 42
        mock_md_repo.sync_delete_by_url_and_media_type.assert_not_called()

    @patch('tasks.media.tr_repo')
    @patch('tasks.media.md_repo')
    def test_deletes_and_clears_on_overwrite(self, mock_md_repo, mock_tr_repo):
        """When overwrite=True and DB has existing media, delete it."""
        orm_obj = _make_media_details_orm()
        mock_md_repo.sync_get_media_details_by_url_and_media_type.return_value = orm_obj
        mock_tr_repo.sync_find_active_by_url_and_type.return_value = None  # no in-flight chain

        dto = _make_dto(overwrite=True)
        result = _reuse_or_delete_existing_media(dto)

        mock_md_repo.sync_delete_by_url_and_media_type.assert_called_once_with(orm_obj)
        # After deletion, no media_details should be set
        assert result.media_details is None
        assert result.existing_media_details_id is None

    @patch('tasks.media.tr_repo')
    @patch('tasks.media.md_repo')
    def test_deletes_skipped_media(self, mock_md_repo, mock_tr_repo):
        """SKIPPED media should be deleted for re-download."""
        orm_obj = _make_media_details_orm(status=TaskStatus.SKIPPED)
        mock_md_repo.sync_get_media_details_by_url_and_media_type.return_value = orm_obj
        mock_tr_repo.sync_find_active_by_url_and_type.return_value = None  # no in-flight chain

        dto = _make_dto()
        result = _reuse_or_delete_existing_media(dto)

        mock_md_repo.sync_delete_by_url_and_media_type.assert_called_once_with(orm_obj)
        assert result.media_details is None

    @patch('tasks.media.tr_repo')
    @patch('tasks.media.md_repo')
    def test_deletes_deleted_media_for_non_owner(self, mock_md_repo, mock_tr_repo):
        """DELETED media requested by a different user is deleted for fresh re-download."""
        orm_obj = _make_media_details_orm(status=TaskStatus.DELETED, owner_id=2)
        mock_md_repo.sync_get_media_details_by_url_and_media_type.return_value = orm_obj
        mock_tr_repo.sync_find_active_by_url_and_type.return_value = None  # no in-flight chain

        dto = _make_dto(user_id=1)
        result = _reuse_or_delete_existing_media(dto)

        mock_md_repo.sync_delete_by_url_and_media_type.assert_called_once_with(orm_obj)
        assert result.media_details is None
        assert result.existing_media_details_id is None

    @patch('tasks.media.md_repo')
    @patch('tasks.media.media_details_to_dto')
    def test_owner_deleted_media_not_deleted(self, mock_to_dto, mock_md_repo):
        """The owner's own DELETED media is never deleted for recreation."""
        orm_obj = _make_media_details_orm(status=TaskStatus.DELETED, owner_id=1)
        mock_md_repo.sync_get_media_details_by_url_and_media_type.return_value = orm_obj
        mock_to_dto.return_value = MediaDetailsDTO(
            url='https://www.youtube.com/watch?v=abc123',
            media_type=MediaType.AUDIO,
            release_timestamp=datetime(2025, 1, 1, tzinfo=UTC),
            owner_id=1,
        )

        dto = _make_dto(user_id=1)
        _reuse_or_delete_existing_media(dto)

        mock_md_repo.sync_delete_by_url_and_media_type.assert_not_called()

    @patch('tasks.media.md_repo')
    @patch('tasks.media.media_details_to_dto')
    def test_deleted_media_without_user_id_not_deleted(self, mock_to_dto, mock_md_repo):
        """A job with no user must not trigger DELETED-record recreation."""
        orm_obj = _make_media_details_orm(status=TaskStatus.DELETED, owner_id=2)
        mock_md_repo.sync_get_media_details_by_url_and_media_type.return_value = orm_obj
        mock_to_dto.return_value = MediaDetailsDTO(
            url='https://www.youtube.com/watch?v=abc123',
            media_type=MediaType.AUDIO,
            release_timestamp=datetime(2025, 1, 1, tzinfo=UTC),
            owner_id=2,
        )

        dto = _make_dto(user_id=None)
        _reuse_or_delete_existing_media(dto)

        mock_md_repo.sync_delete_by_url_and_media_type.assert_not_called()

    @patch('tasks.media.md_repo')
    def test_no_existing_media_returns_dto_unchanged(self, mock_md_repo):
        """When no DB match, return DTO as-is (no media_details populated)."""
        mock_md_repo.sync_get_media_details_by_url_and_media_type.return_value = None

        dto = _make_dto()
        result = _reuse_or_delete_existing_media(dto)

        assert result.media_details is None
        assert result.existing_media_details_id is None

    @patch('tasks.media.md_repo')
    def test_existing_media_without_release_timestamp_not_reused(self, mock_md_repo):
        """Media without release_timestamp should not be reused (needs fresh fetch)."""
        orm_obj = _make_media_details_orm(release_timestamp=None)
        mock_md_repo.sync_get_media_details_by_url_and_media_type.return_value = orm_obj

        dto = _make_dto()
        result = _reuse_or_delete_existing_media(dto)

        assert result.media_details is None
        mock_md_repo.sync_delete_by_url_and_media_type.assert_not_called()

    @patch('tasks.media.md_repo')
    @patch('tasks.media.media_details_to_dto')
    def test_backfills_channel_and_title_from_existing(self, mock_to_dto, mock_md_repo):
        """When reusing, channel/title should be backfilled from existing if DTO lacks them."""
        orm_obj = _make_media_details_orm(channel='FromDB', title='DB Title')
        mock_md_repo.sync_get_media_details_by_url_and_media_type.return_value = orm_obj
        mock_to_dto.return_value = MediaDetailsDTO(
            url='https://www.youtube.com/watch?v=abc123',
            media_type=MediaType.AUDIO,
            channel='FromDB',
            title='DB Title',
            release_timestamp=datetime(2025, 1, 1, tzinfo=UTC),
            owner_id=1,
        )

        dto = _make_dto(channel=None, title=None)
        result = _reuse_or_delete_existing_media(dto)

        assert result.channel == 'FromDB'
        assert result.title == 'DB Title'

    @patch('tasks.media.subscription_access_repo')
    @patch('tasks.media.media_access_repo')
    @patch('tasks.media.md_repo')
    @patch('tasks.media.media_details_to_dto')
    def test_reuse_cross_user_grants_subscription_access(
        self, mock_to_dto, mock_md_repo, mock_access_repo, mock_sub_access_repo
    ):
        """Reusing another owner's record must grant the requesting user access."""
        orm_obj = _make_media_details_orm(owner_id=2)
        mock_md_repo.sync_get_media_details_by_url_and_media_type.return_value = orm_obj
        mock_to_dto.return_value = MediaDetailsDTO(
            url='https://www.youtube.com/watch?v=abc123',
            media_type=MediaType.AUDIO,
            release_timestamp=datetime(2025, 1, 1, tzinfo=UTC),
            owner_id=2,
        )
        mock_sub_access_repo.sync_get_users_with_access.return_value = []

        dto = _make_dto(user_id=1, subscription_id=7)
        result = _reuse_or_delete_existing_media(dto)

        assert result.existing_media_details_id == 42
        mock_access_repo.sync_add_access.assert_called_once_with(
            1, 42, source_type=SourceType.SUBSCRIPTION, source_id=7
        )

    @patch('tasks.media.subscription_access_repo')
    @patch('tasks.media.media_access_repo')
    @patch('tasks.media.md_repo')
    @patch('tasks.media.media_details_to_dto')
    def test_reuse_cross_user_direct_download_grants_direct_access(
        self, mock_to_dto, mock_md_repo, mock_access_repo, mock_sub_access_repo
    ):
        """Reuse without a subscription grants DIRECT-sourced access."""
        orm_obj = _make_media_details_orm(owner_id=2)
        mock_md_repo.sync_get_media_details_by_url_and_media_type.return_value = orm_obj
        mock_to_dto.return_value = MediaDetailsDTO(
            url='https://www.youtube.com/watch?v=abc123',
            media_type=MediaType.AUDIO,
            release_timestamp=datetime(2025, 1, 1, tzinfo=UTC),
            owner_id=2,
        )

        dto = _make_dto(user_id=1)
        _reuse_or_delete_existing_media(dto)

        mock_access_repo.sync_add_access.assert_called_once_with(
            1, 42, source_type=SourceType.DIRECT, source_id=0
        )

    @patch('tasks.media.subscription_access_repo')
    @patch('tasks.media.media_access_repo')
    @patch('tasks.media.md_repo')
    @patch('tasks.media.media_details_to_dto')
    def test_reuse_same_owner_does_not_grant_access(
        self, mock_to_dto, mock_md_repo, mock_access_repo, mock_sub_access_repo
    ):
        """Reusing your own record needs no new grant (owner granted at persist time)."""
        orm_obj = _make_media_details_orm(owner_id=1)
        mock_md_repo.sync_get_media_details_by_url_and_media_type.return_value = orm_obj
        mock_to_dto.return_value = MediaDetailsDTO(
            url='https://www.youtube.com/watch?v=abc123',
            media_type=MediaType.AUDIO,
            release_timestamp=datetime(2025, 1, 1, tzinfo=UTC),
            owner_id=1,
        )
        mock_sub_access_repo.sync_get_users_with_access.return_value = []

        dto = _make_dto(user_id=1, subscription_id=7)
        _reuse_or_delete_existing_media(dto)

        mock_access_repo.sync_add_access.assert_not_called()


class TestUsePendingOrFetchFresh:
    """Tests for _use_pending_or_fetch_fresh helper."""

    def test_uses_pending_media_details_when_valid(self):
        """Pending metadata with release_timestamp AND duration uses the fast-path."""
        pending = MediaDetailsDTO(
            url='https://www.youtube.com/watch?v=abc123',
            media_type=MediaType.AUDIO,
            channel='Cached',
            title='Cached Title',
            release_timestamp=datetime(2025, 6, 1, tzinfo=UTC),
            duration=300,
            owner_id=1,
        )

        dto = _make_dto(pending_media_details=pending)
        result = _use_pending_or_fetch_fresh(dto)

        assert result is dto  # Same object, no modifications needed

    @patch('tasks.media.md_repo')
    @patch(
        'tasks.media.get_release_timestamp',
        return_value=datetime(2025, 6, 1, tzinfo=UTC),
    )
    @patch('tasks.media.get_channel_from_info', return_value='Fresh Channel')
    @patch('tasks.media.get_url_info_with_failure')
    def test_pending_without_duration_falls_through(
        self, mock_info, mock_channel, mock_timestamp, mock_md_repo
    ):
        """Pending metadata missing duration should trigger a fresh yt-dlp fetch."""
        mock_info.return_value = (
            {
                'title': 'Fresh Title',
                'channel': 'Fresh Channel',
                'upload_date': '20250601',
                'duration': 120,
            },
            None,
        )
        pending = MediaDetailsDTO(
            url='https://www.youtube.com/watch?v=abc123',
            media_type=MediaType.AUDIO,
            channel='Cached',
            title='Cached Title',
            release_timestamp=datetime(2025, 6, 1, tzinfo=UTC),
            duration=None,
            owner_id=1,
        )

        dto = _make_dto(pending_media_details=pending)
        result = _use_pending_or_fetch_fresh(dto)

        mock_info.assert_called_once()
        assert result is not None
        assert result.pending_media_details.duration == 120

    @patch('tasks.media._defer_media')
    @patch('tasks.media.get_url_info_with_failure')
    def test_defers_when_video_not_ready(self, mock_info, mock_defer):
        """An unreleased video (live/upcoming/post-live) is deferred — returns None
        and the deferral (release time + next check) is persisted."""
        info = {'title': 'Premiere', 'duration': None, 'live_status': 'is_upcoming'}
        mock_info.return_value = (info, None)

        dto = _make_dto()
        result = _use_pending_or_fetch_fresh(dto)

        assert result is None
        mock_defer.assert_called_once_with(dto, info, 'Video is an upcoming premiere')

    @patch('tasks.media.md_repo')
    @patch(
        'tasks.media.get_release_timestamp',
        return_value=datetime(2025, 6, 1, tzinfo=UTC),
    )
    @patch('tasks.media.get_channel_from_info', return_value='Fresh Channel')
    @patch('tasks.media.get_url_info_with_failure')
    def test_pending_without_release_timestamp_falls_through(
        self, mock_info, mock_channel, mock_timestamp, mock_md_repo
    ):
        """Pending metadata without release_timestamp should trigger yt-dlp fetch."""
        mock_info.return_value = (
            {
                'title': 'Fresh Title',
                'channel': 'Fresh Channel',
                'upload_date': '20250601',
                'duration': 120,
            },
            None,
        )
        pending = MediaDetailsDTO(
            url='https://www.youtube.com/watch?v=abc123',
            media_type=MediaType.AUDIO,
            channel='Cached',
            title='Cached Title',
            release_timestamp=None,
            owner_id=1,
        )

        dto = _make_dto(pending_media_details=pending)
        result = _use_pending_or_fetch_fresh(dto)

        assert result.pending_media_details.title == 'Fresh Title'
        assert result.pending_media_details.channel == 'Fresh Channel'

    @patch('tasks.media._defer_media')
    @patch('tasks.media.get_url_info_with_failure', return_value=(None, EXTRACTION_UNAVAILABLE))
    def test_failed_extraction_is_deferred_not_dropped(self, mock_info, mock_defer):
        """Persisting the failure is what stops the URL being re-fetched every tick."""
        dto = _make_dto()
        result = _use_pending_or_fetch_fresh(dto)

        assert result is None
        mock_defer.assert_called_once_with(
            dto, {}, 'Video metadata is unavailable', EXTRACTION_UNAVAILABLE
        )

    @patch('tasks.media._defer_media')
    @patch('tasks.media.get_url_info_with_failure', return_value=(None, EXTRACTION_TRANSIENT))
    def test_transient_failure_passes_its_kind_through(self, mock_info, mock_defer):
        """The kind drives which ladder _defer_media picks; a rate-limit must not
        inherit the multi-day backoff."""
        dto = _make_dto()
        _use_pending_or_fetch_fresh(dto)

        assert mock_defer.call_args.args[3] == EXTRACTION_TRANSIENT

    @patch('tasks.media.tr_repo')
    @patch('tasks.media._defer_media')
    @patch(
        'tasks.media.get_url_info_with_failure',
        return_value=({'_type': 'playlist', 'title': 'Some Channel'}, None),
    )
    def test_playlist_info_is_rejected_not_deferred(self, mock_info, mock_defer, mock_tr):
        """A channel URL never becomes a video, so a deferral would re-fetch it every tick.

        Nothing downstream catches this: is_video_ready_for_download inspects only live
        status, and create_ydl_options sets no noplaylist.
        """
        dto = _make_dto(placeholder_task_id='resolving-1')
        result = _use_pending_or_fetch_fresh(dto)

        assert result is None
        mock_defer.assert_not_called()
        assert mock_tr.sync_retire_placeholder.call_args.args[:2] == (
            'resolving-1',
            TaskStatus.FAILED,
        )

    @patch('tasks.media.md_repo')
    @patch('tasks.media.tr_repo')
    @patch(
        'tasks.media.get_url_info_with_failure',
        return_value=({'_type': 'video', 'title': 'T', 'duration': 10}, None),
    )
    def test_video_type_is_not_rejected(self, mock_info, mock_tr, mock_md):
        """Only 'playlist' means channel/feed — a single video carries 'video' or nothing."""
        dto = _make_dto()
        result = _use_pending_or_fetch_fresh(dto)

        assert result is not None
        mock_tr.sync_retire_placeholder.assert_not_called()

    @patch('tasks.media.md_repo')
    @patch('tasks.media.get_release_timestamp')
    @patch('tasks.media.get_channel_from_info')
    @patch('tasks.media.get_url_info_with_failure')
    def test_ytdlp_fetch_builds_correct_dto(
        self, mock_info, mock_channel, mock_timestamp, mock_md_repo
    ):
        """Fresh yt-dlp fetch should build MediaDetailsDTO with correct fields."""
        mock_info.return_value = ({'title': 'New Video', 'duration': 600}, None)
        mock_channel.return_value = 'New Channel'
        mock_timestamp.return_value = datetime(2025, 3, 15, tzinfo=UTC)

        dto = _make_dto(user_id=5)
        result = _use_pending_or_fetch_fresh(dto)

        assert result.pending_media_details is not None
        md = result.pending_media_details
        assert md.url == dto.url
        assert md.media_type == MediaType.AUDIO
        assert md.channel == 'New Channel'
        assert md.title == 'New Video'
        assert md.duration == 600
        assert md.owner_id == 5
        assert result.channel == 'New Channel'
        assert result.title == 'New Video'

    def test_returns_dto_unchanged_when_media_details_already_set(self):
        """When dto.media_details is already populated (DB reuse), return as-is."""
        media_dto = MediaDetailsDTO(
            url='https://www.youtube.com/watch?v=abc123',
            media_type=MediaType.AUDIO,
            release_timestamp=datetime(2025, 1, 1, tzinfo=UTC),
            owner_id=1,
        )
        dto = _make_dto()
        dto_with_media = DownloadJobDTO(
            **dto.model_dump(exclude={'media_details'}),
            media_details=media_dto,
        )
        result = _use_pending_or_fetch_fresh(dto_with_media)

        assert result is dto_with_media

    @patch('tasks.media.md_repo')
    @patch('tasks.media.get_release_timestamp')
    @patch('tasks.media.get_channel_from_info', return_value=None)
    @patch('tasks.media.get_url_info_with_failure')
    def test_ytdlp_falls_back_to_dto_channel(
        self, mock_info, mock_channel, mock_timestamp, mock_md_repo
    ):
        """When yt-dlp returns no channel, fall back to DTO's existing channel."""
        mock_info.return_value = ({'title': 'Video', 'duration': 60}, None)
        mock_timestamp.return_value = datetime(2025, 1, 1, tzinfo=UTC)

        dto = _make_dto(channel='OriginalChannel')
        result = _use_pending_or_fetch_fresh(dto)

        assert result.channel == 'OriginalChannel'

    @patch('tasks.media.md_repo')
    @patch('tasks.media.get_release_timestamp')
    @patch('tasks.media.get_channel_from_info', return_value='C')
    @patch('tasks.media.get_url_info_with_failure')
    def test_ready_video_clears_its_deferral(
        self, mock_info, mock_channel, mock_timestamp, mock_md_repo
    ):
        """_copy_upsert_fields refuses to write status=NONE, so the NOT_READY row can
        only be released by an explicit clear."""
        mock_info.return_value = ({'title': 'Aired', 'duration': 60}, None)
        mock_timestamp.return_value = datetime(2025, 1, 1, tzinfo=UTC)

        dto = _make_dto()
        _use_pending_or_fetch_fresh(dto)

        mock_md_repo.sync_clear_deferral.assert_called_once_with(dto.url, 'AUDIO')


class TestDeferredMediaIsNeverDownloadedEarly:
    """A NOT_READY row carries a real release_timestamp, which is exactly the shape
    the reuse and pre-fetch fast-paths accept. If either accepts it, populate skips
    is_video_ready_for_download and dispatches a download of an unreleased video."""

    def _deferred_row(self):
        return _make_media_details_orm(
            status=TaskStatus.NOT_READY,
            release_timestamp=datetime(2099, 1, 1, tzinfo=UTC),
        )

    @patch('tasks.media.md_repo')
    def test_deferred_row_is_not_reused(self, mock_md_repo):
        mock_md_repo.sync_get_media_details_by_url_and_media_type.return_value = (
            self._deferred_row()
        )

        result = _reuse_or_delete_existing_media(_make_dto())

        assert result.media_details is None
        assert result.existing_media_details_id is None

    @patch('tasks.media.md_repo')
    def test_deferred_row_discards_pending_metadata(self, mock_md_repo):
        """The second door: complete pre-fetched metadata also short-circuits the fetch."""
        mock_md_repo.sync_get_media_details_by_url_and_media_type.return_value = (
            self._deferred_row()
        )
        pending = MediaDetailsDTO(
            url='https://www.youtube.com/watch?v=abc123',
            media_type=MediaType.AUDIO,
            release_timestamp=datetime(2099, 1, 1, tzinfo=UTC),
            duration=300,
            owner_id=1,
        )

        result = _reuse_or_delete_existing_media(_make_dto(pending_media_details=pending))

        assert result.pending_media_details is None

    @patch('tasks.media.md_repo')
    def test_deferred_row_is_not_deleted(self, mock_md_repo):
        """Deleting would reset created_at, which the age-keyed ladder reads."""
        mock_md_repo.sync_get_media_details_by_url_and_media_type.return_value = (
            self._deferred_row()
        )

        _reuse_or_delete_existing_media(_make_dto())

        mock_md_repo.sync_delete_by_url_and_media_type.assert_not_called()

    @patch('tasks.media._defer_media')
    @patch('tasks.media.get_url_info_with_failure')
    @patch('tasks.media.md_repo')
    def test_deferred_row_forces_a_fresh_readiness_check(self, mock_md_repo, mock_info, mock_defer):
        """End to end: a still-unreleased video round-trips back to deferral, never
        reaching the persist/dispatch path."""
        mock_md_repo.sync_get_media_details_by_url_and_media_type.return_value = (
            self._deferred_row()
        )
        mock_info.return_value = ({'title': 'Premiere', 'live_status': 'is_upcoming'}, None)

        result = _fetch_or_reuse_media_details(_make_dto())

        assert result is None
        mock_info.assert_called_once()
        mock_defer.assert_called_once()


class TestNextCheckAt:
    """When a deferred URL is looked at again."""

    def _row_aged(self, age: timedelta):
        return MediaDetails(
            url='https://www.youtube.com/watch?v=abc123',
            media_type=MediaType.AUDIO,
            created_at=utc_now() - age,
        )

    def test_known_future_premiere_schedules_for_air_time(self):
        """get_release_timestamp yields naive *local* time; next_check_at is naive UTC.
        Asserting on the resulting instant catches a missing conversion, which a
        same-basis comparison would hide on a UTC host. The premiere starts as a UTC
        instant rather than as local time plus three days, which is off by an hour
        whenever a DST change falls in between."""
        air_time = (utc_now() + timedelta(days=3)).replace(microsecond=0)
        local = datetime.fromtimestamp(air_time.replace(tzinfo=UTC).timestamp())

        assert _next_check_at(None, local, None) == air_time

    def test_past_release_falls_back_to_the_ladder(self):
        """post_live: the stream aired, the VOD just isn't ready yet."""
        result = _next_check_at(None, datetime.now() - timedelta(hours=1), None)

        assert result <= utc_now() + timedelta(minutes=11)

    def test_short_ladder_escalates_with_age(self):
        fresh = _next_check_at(self._row_aged(timedelta(minutes=5)), None, None)
        old = _next_check_at(self._row_aged(timedelta(days=2)), None, None)

        assert fresh < old

    def test_unavailable_backs_off_much_further_than_unreleased(self):
        age = timedelta(days=2)
        unreleased = _next_check_at(self._row_aged(age), None, EXTRACTION_TRANSIENT)
        unavailable = _next_check_at(self._row_aged(age), None, EXTRACTION_UNAVAILABLE)

        assert unavailable > unreleased

    def test_first_unavailable_failure_is_not_parked_for_a_week(self):
        """A rate-limit burst looks identical to a private video, so the long ladder
        must still start short."""
        result = _next_check_at(self._row_aged(timedelta(0)), None, EXTRACTION_UNAVAILABLE)

        assert result <= utc_now() + timedelta(hours=7)

    def test_unavailable_ignores_a_stale_release_timestamp(self):
        """No info dict means no trustworthy premiere time to schedule against."""
        result = _next_check_at(None, utc_now() + timedelta(days=365), EXTRACTION_UNAVAILABLE)

        assert result <= utc_now() + timedelta(hours=7)


class TestFetchOrReuseMediaDetails:
    """Integration tests for the orchestrator function."""

    @patch('tasks.media._use_pending_or_fetch_fresh')
    @patch('tasks.media._reuse_or_delete_existing_media')
    def test_calls_helpers_in_order(self, mock_reuse, mock_fetch):
        """Orchestrator should call reuse first, then fetch."""
        dto = _make_dto()
        intermediate_dto = _make_dto(channel='Intermediate')
        final_dto = _make_dto(channel='Final')
        mock_reuse.return_value = intermediate_dto
        mock_fetch.return_value = final_dto

        result = _fetch_or_reuse_media_details(dto)

        mock_reuse.assert_called_once_with(dto)
        mock_fetch.assert_called_once_with(intermediate_dto)
        assert result is final_dto

    @patch('tasks.media._use_pending_or_fetch_fresh')
    @patch('tasks.media._reuse_or_delete_existing_media')
    def test_returns_none_when_fetch_fails(self, mock_reuse, mock_fetch):
        """When yt-dlp fails, orchestrator returns None."""
        dto = _make_dto()
        mock_reuse.return_value = dto
        mock_fetch.return_value = None

        result = _fetch_or_reuse_media_details(dto)

        assert result is None
