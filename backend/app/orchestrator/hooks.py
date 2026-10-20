"""Job lifecycle hooks: TaskRecord/MediaDetails status tracking around bodies.

Each job kind gets a hooks class; the job wrapper invokes:
- before_start / on_success / on_failure / on_retry — status writes + SSE
  publishes at each lifecycle transition;
- on_cancel — cleanup of partial output when a job is cancelled mid-run.
"""

from logger import logger
from models import MediaType, TaskStatus, utc_now
from orchestrator.error_codes import classify_error
from progress_publisher import publish_status_change
from repositories import clips as clips_repo
from repositories import media_details as md_repo
from repositories import task_records as tr_repo
from services.cleanup import cleanup_task_files, delete_file
from ytdlp.files import (
    delete_transcript_staging,
    kept_thumbnail_path,
    move_thumbnail,
    staged_thumbnail_path,
)

ERROR_MESSAGE_MAX_LENGTH = 200


def error_message(exc: BaseException | None) -> str:
    return str(exc)[:ERROR_MESSAGE_MAX_LENGTH] if exc else 'Unknown error'


class NullHooks:
    """No-op hooks for untracked jobs (add-subscription, populate, the pipelines)."""

    def before_start(self, task_id, args, kwargs):
        pass

    def on_success(self, retval, task_id, args, kwargs):
        pass

    def on_failure(self, exc, task_id, args, kwargs, einfo=None):
        pass

    def on_retry(self, exc, task_id, args, kwargs, einfo=None):
        pass

    def on_cancel(self, task_id, args):
        pass


class StatusHooks(NullHooks):
    """Shared lifecycle template: TaskRecord write → entity write → SSE publish.

    Subclasses set the message attributes, override _update_entity_status()
    for their entity-side status write (default: none), and get_user_id().
    """

    start_message = 'Processing...'
    success_message = 'Task completed successfully'
    failure_prefix = 'Task failed'
    retry_prefix = 'Retrying due to'

    def get_user_id(self, args):
        return None

    def _update_entity_status(self, args, status):
        pass

    def _transition(self, task_id, args, status, status_msg, exc=None, tr_extra=None):
        # error_code is written on every transition, not just the failing ones: a code
        # left behind by an earlier attempt would outlive its error and mislabel the row.
        tr_repo.sync_update_one(
            task_id,
            {
                'status': status.value,
                'status_message': status_msg,
                'error_code': classify_error(exc),
                **(tr_extra or {}),
            },
        )
        self._update_entity_status(args, status)
        publish_status_change(task_id, status.value, status_msg, user_id=self.get_user_id(args))

    def before_start(self, task_id, args, kwargs):
        self._transition(task_id, args, TaskStatus.IN_PROGRESS, self.start_message)

    def on_success(self, retval, task_id, args, kwargs):
        self._transition(task_id, args, TaskStatus.COMPLETE, self.success_message)

    def on_failure(self, exc, task_id, args, kwargs, einfo=None):
        self._transition(
            task_id, args, TaskStatus.FAILED, f'{self.failure_prefix}: {error_message(exc)}', exc
        )
        logger.error(f'Task {task_id} failed: {exc}')

    def on_retry(self, exc, task_id, args, kwargs, einfo=None):
        self._transition(
            task_id, args, TaskStatus.RETRY, f'{self.retry_prefix}: {error_message(exc)}', exc
        )
        logger.info(f'Task {task_id} retried: {exc}')


class BaseStatusHooks(StatusHooks):
    """Hooks for jobs that mirror status into a MediaDetails column."""

    md_status_field: str = 'status'

    def get_media_details_id(self, args):
        msg = 'Subclass must implement get_media_details_id()'
        raise NotImplementedError(msg)

    def _update_entity_status(self, args, status):
        md_repo.sync_update_one(
            self.get_media_details_id(args), {self.md_status_field: status.value}
        )


class DownloadHooks(BaseStatusHooks):
    start_message = 'Starting download...'
    failure_prefix = 'Download failed'

    def get_media_details_id(self, args):
        # Re-resolve by url+media_type: the payload id was captured at populate
        # time and may be stale if a concurrent chain replaced the row while this
        # job sat in the downloads lane (hooks would then silently update 0 rows).
        job = args[0] if args else {}
        md = job.get('media_details') or {}
        url = job.get('url') or md.get('url')
        if url:
            live = md_repo.sync_get_media_details_by_url_and_media_type(
                url, job.get('media_type') or md.get('media_type')
            )
            if live:
                return live.id
        return md.get('id')

    def get_user_id(self, args):
        return args[0].get('user_id') if args else None

    def on_success(self, retval, task_id, args, kwargs):
        # Download uses retval for id on success, not args
        user_id = self.get_user_id(args)

        # Don't overwrite NOT_READY status — _handle_video_not_ready already set it
        # (and published the SSE event); the record stays visible as Not Released.
        if retval and retval.get('status') == TaskStatus.NOT_READY.value:
            return

        # Guard: if retval is missing or invalid, mark as FAILED instead of crashing
        if not retval or 'id' not in retval:
            status_msg = 'Download completed but no media details returned'
            tr_repo.sync_update_one(
                task_id, {'status': TaskStatus.FAILED.value, 'status_message': status_msg}
            )
            publish_status_change(task_id, TaskStatus.FAILED.value, status_msg, user_id=user_id)
            logger.error(f'Task {task_id}: on_success called with invalid retval: {retval}')
            return

        # A transcript-only item has only staged its captions or audio; it is COMPLETE once
        # TranscriptHooks has its transcript, and has no file for a sprite sheet.
        transcript_only = retval.get('media_type') == MediaType.TRANSCRIPT
        status_msg = 'Staged for transcription' if transcript_only else 'Download completed'
        tr_repo.sync_update_one(
            task_id,
            {
                'status': TaskStatus.COMPLETE.value,
                'status_message': status_msg,
                'percent_complete': 100,
            },
        )
        if transcript_only:
            md_repo.sync_update_one(retval['id'], {'status': TaskStatus.POSTPROCESSING.value})
        else:
            md_repo.sync_update_one(
                retval['id'], {'status': TaskStatus.COMPLETE.value, 'downloaded_at': utc_now()}
            )
        publish_status_change(task_id, TaskStatus.COMPLETE.value, status_msg, user_id=user_id)

        # Last on purpose: the row is already COMPLETE and published, so a raise here
        # (absorbed by wrapper._run_hook) costs only the sheet, and startup recovery
        # picks the still-QUEUED sprite row up on the next boot.
        if not transcript_only:
            self._queue_sprite_generation(retval['id'])

    @staticmethod
    def _discard_transcript_staging(args) -> None:
        """A transcript-only download that ends without a transcript job keeps nothing.

        Its staged files are named by URL hash alone, not '{title} [{hash}]', so
        cleanup_task_files never matches them.
        """
        job = args[0] if args else {}
        url = job.get('url') or (job.get('media_details') or {}).get('url')
        if url and job.get('media_type') == MediaType.TRANSCRIPT:
            delete_transcript_staging(url)

    def _queue_sprite_generation(self, media_details_id: int) -> None:
        """Dispatch the sprite row created for this download at populate time.

        Lives in on_success rather than the job body because that fires for exactly
        the right outcomes: normal success plus the already-downloaded paths (which
        have a file on disk), but not superseded/quota (SkipJob) or not-ready (which
        returns above).
        """
        from tasks.sprites import sync_submit_sprites_job

        # Re-fetch rather than trust retval: the repeat-download path returns the
        # stale populate-time DTO.
        media = md_repo.sync_get_media_details_by_id(media_details_id)
        if media:
            sync_submit_sprites_job(media, user_id=media.owner_id)

    def on_failure(self, exc, task_id, args, kwargs, einfo=None):
        super().on_failure(exc, task_id, args, kwargs, einfo)
        tr_repo.sync_mark_downstream_as_failed(task_id)
        self._discard_transcript_staging(args)

    def on_cancel(self, task_id, args):
        # The cancel endpoint already cleaned partial files, but it raced the
        # still-downloading thread — re-run the cleanup now that the download
        # has actually aborted so no fresh .part files survive.
        job = args[0] if args else {}
        md = job.get('media_details') or {}
        title = md.get('title') or job.get('title')
        url = job.get('url') or md.get('url')
        if title or url:
            deleted = cleanup_task_files(task_title=title, task_url=url)
            if deleted:
                logger.info(f'Cleaned up {deleted} partial file(s) for cancelled task {task_id}')
        self._discard_transcript_staging(args)

        # before_start left the row IN_PROGRESS; give it the terminal status the
        # other lifecycle hooks all write, so it doesn't claim to be downloading.
        if url:
            md_repo.sync_mark_download_cancelled(url, job.get('media_type') or md.get('media_type'))


class TranscriptHooks(StatusHooks):
    """Only updates TaskRecord status. MediaDetails status is tracked via
    the transcript_task_record_id foreign key relationship.
    """

    start_message = 'Generating transcript...'
    success_message = 'Transcript generated'
    failure_prefix = 'Transcript generation failed'
    retry_prefix = 'Retrying transcript due to'

    def get_user_id(self, args):
        if args:
            return args[0].get('owner_id')
        return None

    def before_start(self, task_id, args, kwargs):
        md = args[0]
        self._transition(
            task_id,
            args,
            TaskStatus.IN_PROGRESS,
            self.start_message,
            tr_extra={'title': md['title'], 'channel': md['channel'], 'percent_complete': 0},
        )

    def on_success(self, retval, task_id, args, kwargs):
        self._transition(
            task_id,
            args,
            TaskStatus.COMPLETE,
            self.success_message,
            tr_extra={'percent_complete': 100},
        )
        self._finish_transcript_only(args, TaskStatus.COMPLETE)
        logger.info(f'Task {task_id} succeeded')

    def on_failure(self, exc, task_id, args, kwargs, einfo=None):
        super().on_failure(exc, task_id, args, kwargs, einfo)
        self._finish_transcript_only(args, TaskStatus.FAILED)

    def on_cancel(self, task_id, args):
        from repositories import transcript_blocks as tb_repo

        md = args[0]
        media_details_id = md.get('id')
        if media_details_id:
            tb_repo.sync_delete_transcript_block_by_media_details_id(media_details_id)
            logger.info(f'Cleaned up partial transcripts for media_details {media_details_id}')
        self._finish_transcript_only(args, TaskStatus.CANCELLED)

    @staticmethod
    def _finish_transcript_only(args, status: TaskStatus) -> None:
        """End a transcript-only item: nothing it staged outlives its transcript job.

        Not called on retry, which needs the staged captions or audio again.
        """
        md = args[0] if args else {}
        if md.get('media_type') != MediaType.TRANSCRIPT or not md.get('url'):
            return
        thumbnail_path = None
        if status == TaskStatus.COMPLETE:
            # Before the staging delete below, which would take the staged thumbnail with it.
            thumbnail_path = move_thumbnail(
                staged_thumbnail_path(md['url']),
                kept_thumbnail_path(md['url'], MediaType.TRANSCRIPT),
            )
        removed = delete_transcript_staging(md['url'])
        md_repo.sync_finish_transcript_only(md['url'], status, thumbnail_path=thumbnail_path)
        logger.info(
            f'Transcript-only {md["url"]} ended {status.value}; deleted {removed} staged file(s)'
        )


class ClipHooks(StatusHooks):
    """Updates both TaskRecord and Clip status (unlike TranscriptHooks, which
    only tracks TaskRecord)."""

    start_message = 'Generating clip...'
    success_message = 'Clip created successfully'
    failure_prefix = 'Clip generation failed'
    retry_prefix = 'Clip generation retrying'

    def get_clip_id(self, args):
        return args[0].get('clip_id')

    def get_user_id(self, args):
        return args[0].get('user_id')

    def _update_entity_status(self, args, status):
        clips_repo.sync_update_clip(self.get_clip_id(args), {'status': status.value})

    def before_start(self, task_id, args, kwargs):
        self._transition(
            task_id,
            args,
            TaskStatus.IN_PROGRESS,
            self.start_message,
            tr_extra={'percent_complete': 0},
        )

    def on_success(self, retval, task_id, args, kwargs):
        # file_path is set by the job itself, just update status here
        self._transition(
            task_id,
            args,
            TaskStatus.COMPLETE,
            self.success_message,
            tr_extra={'percent_complete': 100},
        )
        logger.info(f'Clip task {task_id} succeeded for clip {self.get_clip_id(args)}')

    def on_cancel(self, task_id, args):
        clip_id = self.get_clip_id(args)
        clip = clips_repo.sync_get_clip_by_id(clip_id)
        if clip and clip.file_path:
            delete_file(clip.file_path)
            clips_repo.sync_update_clip(
                clip_id,
                {
                    'status': TaskStatus.CANCELLED.value,
                    'file_path': None,
                },
            )
            logger.info(f'Cleaned up partial clip file for clip {clip_id}')


class SpriteHooks(StatusHooks):
    """Only updates TaskRecord status — MediaDetails has no sprite-status column."""

    start_message = 'Generating sprite sheet...'
    success_message = 'Sprite sheet generated'
    failure_prefix = 'Sprite generation failed'
    retry_prefix = 'Retrying sprite generation due to'

    def get_user_id(self, args):
        return args[0].get('user_id') if args else None

    def on_cancel(self, task_id, args):
        from tasks.sprites import delete_partial_sprite_output

        delete_partial_sprite_output(args[0].get('media_details_id'))
