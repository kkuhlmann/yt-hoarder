from logger import logger
from models import JobType, Subscription
from orchestrator import JobContext
from repositories import subscriptions as sub_repo
from repositories.task_records import DIRECT_DOWNLOAD_PRIORITY, SUBSCRIPTION_DOWNLOAD_PRIORITY
from schemas import SubscriptionDTO
from serializers import (
    deserialize_subscription,
    serialize_subscription,
    subscription_to_dto,
)
from ytdlp.info import get_channel_from_info, get_url_info
from ytdlp.urls import is_channel_or_feed_url, normalize_playlist_url

# Fan-out backpressure. A large channel yields thousands of entries, and populate
# jobs run on the default lane, not this one — submitting them all at once parks the
# whole backlog in memory where a restart loses it. Holding the producer here instead
# bounds queue depth without capping throughput: the lane drains at its own rate and
# we simply top it up. The cron fire uses a fixed task_id, so a pipeline that runs
# long suppresses its own overlapping ticks rather than piling them up.
FANOUT_QUEUE_TARGET = 100
FANOUT_POLL_SECONDS = 2.0
# Give up rather than hold the subscriptions lane forever if the default lane wedges.
# Lossless: the next tick re-enumerates and resumes from the persisted state.
FANOUT_MAX_WAIT_SECONDS = 1800


def _wait_for_fanout_capacity(ctx: JobContext, waited: float) -> float | None:
    """Block until the default lane has room. Returns the new cumulative wait, or None
    if the job was cancelled or the stall budget ran out."""
    from orchestrator import DEFAULT_LANE, orch

    if ctx.cancel_event.is_set():
        return None

    while orch.queued_count(DEFAULT_LANE) >= FANOUT_QUEUE_TARGET:
        if waited >= FANOUT_MAX_WAIT_SECONDS:
            logger.warning(
                f'Fan-out stalled for {waited:.0f}s with the {DEFAULT_LANE} lane at capacity; '
                f'stopping this cycle. The next tick will resume.'
            )
            return None
        if ctx.cancel_event.wait(FANOUT_POLL_SECONDS):
            return None
        waited += FANOUT_POLL_SECONDS

    return waited


def run_subscription_pipeline(ctx: JobContext, job_type: str) -> dict:
    """Orchestrator body for the scheduled check of every enabled subscription of a type."""
    return _check_subscriptions(
        ctx, get_all_subscriptions_impl(job_type), SUBSCRIPTION_DOWNLOAD_PRIORITY
    )


def run_subscription_check(ctx: JobContext, subscription_id: int) -> dict:
    """Orchestrator body for an on-demand check of one subscription, enabled or not.

    What it finds queues at the manual-download priority: someone asked for it.
    """
    return _check_subscriptions(
        ctx, get_subscription_impl(subscription_id), DIRECT_DOWNLOAD_PRIORITY
    )


def _check_subscriptions(ctx: JobContext, subs: list[dict], priority: int) -> dict:
    """The subscription cycle as plain control flow.

    create_download_jobs → filter_completed → fan out one populate job per
    download at `priority`; each early return aborts the pipeline.
    """
    from orchestrator import POPULATE_JOB, JobSpec, orch
    from tasks.media import filter_completed_downloads_impl

    if not subs:
        return {'jobs_started': 0}

    jobs = create_download_jobs_from_subs_impl(subs, priority)
    if not jobs:
        return {'jobs_started': 0}

    jobs = filter_completed_downloads_impl(jobs)
    if not jobs:
        return {'jobs_started': 0}

    waited = 0.0
    started = 0
    for job in jobs:
        waited = _wait_for_fanout_capacity(ctx, waited)
        if waited is None:
            break

        orch.submit_from_thread(
            JobSpec(
                job_name=POPULATE_JOB,
                args=(job,),
                tracked=False,
                priority=priority,
                user_id=job.get('user_id'),
            )
        )
        started += 1

    logger.info(f'Started {started} download chains (of {len(jobs)} eligible)')
    return {'jobs_started': started}


def add_subscription_details_impl(subscription: dict) -> None:
    sub_dto = deserialize_subscription(subscription)
    if sub_dto.date_filter is not None:
        sub_dto = SubscriptionDTO(
            **sub_dto.model_dump(exclude={'date_filter'}),
            date_filter=sub_dto.date_filter.replace(tzinfo=None),
        )

    # Detect subscription type: playlist, channel, or yt-dlp fallback
    playlist_url = normalize_playlist_url(sub_dto.url)
    if playlist_url:
        # Known playlist pattern (YouTube list= param)
        logger.info(f'Playlist url: {sub_dto.url}')
        url = playlist_url
        job_type = JobType.PLAYLIST_SUBSCRIPTION
        info = get_url_info(url, max_entries=1)
        channel = info.get('title', 'Unknown') if info else 'Unknown'
    elif is_channel_or_feed_url(sub_dto.url):
        # Known YouTube channel pattern
        logger.info(f'Channel url: {sub_dto.url}')
        url = sub_dto.url
        job_type = JobType.CHANNEL_SUBSCRIPTION
        info = get_url_info(url, max_entries=1)
        channel = get_channel_from_info(info) or (info.get('title') if info else None) or 'Unknown'
    else:
        # yt-dlp fallback: let yt-dlp determine if it's a channel/playlist
        logger.info(f'Unknown URL type, using yt-dlp to detect: {sub_dto.url}')
        info = get_url_info(sub_dto.url, max_entries=1)
        if not info:
            msg = f'Could not fetch info for URL: {sub_dto.url}'
            raise ValueError(msg)
        url = sub_dto.url
        if info.get('_type') == 'playlist':
            # yt-dlp detected a playlist/channel - treat as channel subscription
            job_type = JobType.CHANNEL_SUBSCRIPTION
            channel = get_channel_from_info(info) or info.get('title', 'Unknown')
        else:
            msg = f'URL does not appear to be a channel or playlist: {sub_dto.url}'
            raise ValueError(msg)

    sub_orm = Subscription(
        url=url,
        channel=channel,
        enabled=sub_dto.enabled,
        audio_only=sub_dto.audio_only,
        media_type=sub_dto.media_type,
        string_match=sub_dto.string_match,
        overwrite=sub_dto.overwrite,
        date_filter=sub_dto.date_filter,
        min_duration_seconds=sub_dto.min_duration_seconds,
        max_duration_seconds=sub_dto.max_duration_seconds,
        job_type=job_type,
        generate_transcript=sub_dto.generate_transcript,
        download_quality=sub_dto.download_quality,
        audio_quality=sub_dto.audio_quality,
        user_id=sub_dto.user_id,
    )
    sub_repo.sync_add_subscription(sub_orm)


def run_add_subscription(_ctx: JobContext, subscription: dict) -> None:
    """Orchestrator body for the untracked add-subscription job."""
    return add_subscription_details_impl(subscription)


def get_all_subscriptions_impl(job_type: str) -> list[dict]:
    subs = sub_repo.sync_get_enabled_subscriptions(job_type=job_type)
    return [serialize_subscription(subscription_to_dto(s)) for s in subs]


def get_subscription_impl(subscription_id: int) -> list[dict]:
    """The subscription as a one-element list, or [] if it was deleted since the click."""
    sub = sub_repo.sync_get_subscription_by_id(subscription_id)
    return [serialize_subscription(subscription_to_dto(sub))] if sub else []


def create_download_jobs_from_subs_impl(
    subs: list[dict], priority: int = SUBSCRIPTION_DOWNLOAD_PRIORITY
) -> list[dict]:
    """Build one download job per video across all subscriptions, round-robin.

    The returned order is the order populate jobs are dequeued: the caller fans out
    in list order, and every populate JobSpec ties at the same lane sort key, so
    Lane.pop_next falls back to insertion order. Grouping by subscription would put
    the last-added one behind every other subscription's entire backlog.

    `priority` is stamped on every job so the download chain it becomes queues at it too.
    """
    from itertools import zip_longest

    from schemas import DownloadJobDTO, MediaDetailsDTO
    from serializers import serialize_download_job
    from ytdlp.info import extract_entries_from_info, get_release_timestamp
    from ytdlp.playlists import get_playlist_name

    jobs_by_sub: list[list[dict]] = []
    for sub_d in subs:
        sub_dto = deserialize_subscription(sub_d)
        info = get_url_info(
            sub_dto.url,
            sub_dto.string_match,
            sub_dto.min_duration_seconds,
            sub_dto.max_duration_seconds,
        )

        if not info:
            logger.warning(f'No info found for subscription URL: {sub_dto.url}, skipping...')
            continue

        entries = extract_entries_from_info(info)

        # For playlist subscriptions, extract playlist name and URL so that
        # _handle_playlist_creation auto-creates/reuses an app-level Playlist
        playlist_name = None
        source_playlist_url = None
        if sub_dto.job_type == JobType.PLAYLIST_SUBSCRIPTION:
            playlist_name = get_playlist_name(info, sub_dto.url)
            source_playlist_url = normalize_playlist_url(sub_dto.url) or sub_dto.url

        sub_jobs = []
        for entry in entries:
            # Skip the per-video yt-dlp call in _fetch_or_reuse_media_details when flat
            # extraction already carries a release date. YouTube never does — its flat
            # entries have no upload_date or release_timestamp on any tab — so this only
            # fires for extractors that expose dates in flat mode.
            release_ts = get_release_timestamp(entry)
            pending_md = None
            if release_ts:
                pending_md = MediaDetailsDTO(
                    url=entry['url'],
                    media_type=sub_dto.media_type,
                    channel=entry.get('channel') or sub_dto.channel,
                    title=entry.get('title', 'Unknown'),
                    release_timestamp=release_ts,
                    duration=entry.get('duration'),
                )

            dlj_dto = DownloadJobDTO(
                url=entry['url'],
                title=entry.get('title', 'Unknown'),
                channel=entry.get('channel') or sub_dto.channel,
                audio_only=sub_dto.audio_only,
                media_type=sub_dto.media_type,
                job_type=sub_dto.job_type,
                overwrite=sub_dto.overwrite,
                subscription_id=sub_dto.id,
                download_playlist=False,
                generate_transcript=sub_dto.generate_transcript,
                download_quality=sub_dto.download_quality,
                audio_quality=sub_dto.audio_quality,
                user_id=sub_dto.user_id,
                subscription=sub_dto,
                playlist_name=playlist_name,
                source_playlist_url=source_playlist_url,
                pending_media_details=pending_md,
                priority=priority,
            )
            sub_jobs.append(serialize_download_job(dlj_dto))

        jobs_by_sub.append(sub_jobs)

    return [job for group in zip_longest(*jobs_by_sub) for job in group if job is not None]
