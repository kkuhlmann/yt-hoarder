"""Fan-out ordering and URL handling for the two download pipelines.

Pure unit tests — yt-dlp is patched out, no database, no network.
"""

from unittest.mock import patch

import pytest

from models import JobType, MediaType
from orchestrator import POPULATE_JOB, JobContext, orch
from repositories.task_records import DIRECT_DOWNLOAD_PRIORITY, SUBSCRIPTION_DOWNLOAD_PRIORITY
from routers.ytdl_router import _canonical_job_url
from schemas import DownloadJobDTO, SubscriptionDTO
from serializers import serialize_download_job, serialize_subscription
from tasks.media import create_download_and_transcript_chains_impl
from tasks.scheduling import expand_playlists_impl, run_direct_download_pipeline
from tasks.subscriptions import (
    FANOUT_MAX_WAIT_SECONDS,
    FANOUT_POLL_SECONDS,
    FANOUT_QUEUE_TARGET,
    create_download_jobs_from_subs_impl,
    run_subscription_check,
    run_subscription_pipeline,
)


def _sub(sub_id, channel):
    return serialize_subscription(
        SubscriptionDTO(
            id=sub_id,
            url=f'https://www.youtube.com/@{channel}',
            channel=channel,
            media_type=MediaType.VIDEO,
            job_type=JobType.CHANNEL_SUBSCRIPTION,
            user_id=1,
        )
    )


def _flat_info(channel, count):
    """A flat channel extraction with `count` video entries."""
    return {
        'entries': [
            {'url': f'https://www.youtube.com/watch?v={channel}{i}', 'title': f'{channel} {i}'}
            for i in range(count)
        ]
    }


def _channels_in_order(jobs):
    return [job['channel'] for job in jobs]


class TestSubscriptionFanOutOrder:
    """The returned order is the dequeue order, so subscriptions must interleave."""

    def test_subscriptions_are_interleaved(self):
        infos = {'Alpha': _flat_info('Alpha', 3), 'Beta': _flat_info('Beta', 3)}
        with patch(
            'tasks.subscriptions.get_url_info',
            side_effect=lambda url, *a, **k: infos[url.split('@')[-1]],
        ):
            jobs = create_download_jobs_from_subs_impl([_sub(1, 'Alpha'), _sub(2, 'Beta')])

        assert _channels_in_order(jobs) == ['Alpha', 'Beta', 'Alpha', 'Beta', 'Alpha', 'Beta']

    def test_newest_subscription_is_not_stuck_behind_a_large_backlog(self):
        """The regression: a fresh subscription's first video must not queue last."""
        infos = {'Alpha': _flat_info('Alpha', 50), 'Beta': _flat_info('Beta', 2)}
        with patch(
            'tasks.subscriptions.get_url_info',
            side_effect=lambda url, *a, **k: infos[url.split('@')[-1]],
        ):
            jobs = create_download_jobs_from_subs_impl([_sub(1, 'Alpha'), _sub(2, 'Beta')])

        assert _channels_in_order(jobs)[:4] == ['Alpha', 'Beta', 'Alpha', 'Beta']
        # Uneven lengths: the longer subscription's tail still survives, in order
        assert len(jobs) == 52
        assert _channels_in_order(jobs)[4:] == ['Alpha'] * 48

    def test_single_subscription_keeps_source_order(self):
        with patch('tasks.subscriptions.get_url_info', return_value=_flat_info('Alpha', 3)):
            jobs = create_download_jobs_from_subs_impl([_sub(1, 'Alpha')])

        assert [job['url'] for job in jobs] == [
            f'https://www.youtube.com/watch?v=Alpha{i}' for i in range(3)
        ]

    def test_subscription_yielding_no_info_is_skipped_without_shifting_others(self):
        infos = {'Alpha': None, 'Beta': _flat_info('Beta', 2)}
        with patch(
            'tasks.subscriptions.get_url_info',
            side_effect=lambda url, *a, **k: infos[url.split('@')[-1]],
        ):
            jobs = create_download_jobs_from_subs_impl([_sub(1, 'Alpha'), _sub(2, 'Beta')])

        assert _channels_in_order(jobs) == ['Beta', 'Beta']


class TestPlaylistExpansionUrlSurvival:
    """normalize_video_url strips list=, which expand_playlists_impl keys on."""

    def test_video_in_playlist_url_still_expands(self):
        dto = DownloadJobDTO(
            url='https://www.youtube.com/watch?v=SomeVideoId&list=PLtestlist123',
            media_type=MediaType.VIDEO,
            download_playlist=True,
            user_id=1,
        )
        with patch(
            'ytdlp.playlists.populate_playlist_jobs', return_value=[{'expanded': True}]
        ) as p:
            result = expand_playlists_impl([serialize_download_job(dto)])

        assert result == [{'expanded': True}]
        assert p.call_args.args[1] == 'https://www.youtube.com/playlist?list=PLtestlist123'

    def test_bare_playlist_url_still_expands(self):
        dto = DownloadJobDTO(
            url='https://www.youtube.com/playlist?list=PLtestlist123',
            media_type=MediaType.VIDEO,
            download_playlist=True,
            user_id=1,
        )
        with patch(
            'ytdlp.playlists.populate_playlist_jobs', return_value=[{'expanded': True}]
        ) as p:
            result = expand_playlists_impl([serialize_download_job(dto)])

        assert result == [{'expanded': True}]
        assert p.call_args.args[1] == 'https://www.youtube.com/playlist?list=PLtestlist123'

    def test_plain_video_url_is_not_expanded(self):
        dto = DownloadJobDTO(
            url='https://www.youtube.com/watch?v=SomeVideoId',
            media_type=MediaType.VIDEO,
            download_playlist=True,
            user_id=1,
        )
        with patch('ytdlp.playlists.populate_playlist_jobs') as p:
            result = expand_playlists_impl([serialize_download_job(dto)])

        p.assert_not_called()
        assert result[0]['url'] == 'https://www.youtube.com/watch?v=SomeVideoId'

    def test_expanded_entries_are_canonicalized_and_not_re_expanded(self):
        """End-to-end through the real populate_playlist_jobs, not a mock."""
        dto = DownloadJobDTO(
            url='https://www.youtube.com/watch?v=SomeVideoId&list=PLtestlist123',
            media_type=MediaType.VIDEO,
            download_playlist=True,
            user_id=1,
        )
        entries = [
            {'url': 'https://www.youtube.com/shorts/PlShort01', 'title': 'S'},
            {'url': 'https://www.youtube.com/watch?v=PlVideo001', 'title': 'V'},
        ]
        with patch(
            'ytdlp.playlists.get_url_info', return_value={'title': 'A Playlist', 'entries': entries}
        ):
            jobs = expand_playlists_impl([serialize_download_job(dto)])

        assert [job['url'] for job in jobs] == [
            'https://www.youtube.com/watch?v=PlShort01',
            'https://www.youtube.com/watch?v=PlVideo001',
        ]
        assert all(not job['download_playlist'] for job in jobs)
        assert all(
            job['source_playlist_url'] == 'https://www.youtube.com/playlist?list=PLtestlist123'
            for job in jobs
        )


class TestDirectDownloadUrlCanonicalization:
    """The /ytdl router's conflict lookups and the pipeline must share one identity."""

    def test_single_video_forms_collapse_to_canonical(self):
        for url in (
            'https://www.youtube.com/shorts/AltFormVid1',
            'https://youtu.be/AltFormVid1?si=xyz',
            'https://www.youtube.com/watch?v=AltFormVid1&t=42s',
        ):
            assert _canonical_job_url({'url': url, 'download_playlist': False}) == (
                'https://www.youtube.com/watch?v=AltFormVid1'
            )

    def test_playlist_job_keeps_its_list_param(self):
        url = 'https://www.youtube.com/watch?v=SomeVideoId&list=PLtestlist123'
        assert _canonical_job_url({'url': url, 'download_playlist': True}) == url

    def test_bare_playlist_url_survives_either_way(self):
        url = 'https://www.youtube.com/playlist?list=PLtestlist123'
        assert _canonical_job_url({'url': url, 'download_playlist': True}) == url
        assert _canonical_job_url({'url': url, 'download_playlist': False}) == url


class TestFanOutPriorities:
    """Manual downloads must outrank subscription work on the shared default lane."""

    def test_direct_priority_outranks_subscription_priority(self):
        assert DIRECT_DOWNLOAD_PRIORITY < SUBSCRIPTION_DOWNLOAD_PRIORITY

    def test_subscription_fanout_uses_subscription_priority(self):
        jobs = [{'url': 'https://www.youtube.com/watch?v=Sub00000001', 'user_id': 1}]
        with (
            patch('tasks.subscriptions.get_all_subscriptions_impl', return_value=[{'id': 1}]),
            patch('tasks.subscriptions.create_download_jobs_from_subs_impl', return_value=jobs),
            patch('tasks.media.filter_completed_downloads_impl', return_value=jobs),
            patch.object(orch, 'queued_count', return_value=0),
            patch.object(orch, 'submit_from_thread') as submit,
        ):
            run_subscription_pipeline(JobContext(task_id='t'), JobType.CHANNEL_SUBSCRIPTION.value)

        specs = [call.args[0] for call in submit.call_args_list]
        assert specs, 'pipeline must fan out at least one populate job'
        assert all(s.job_name == POPULATE_JOB for s in specs)
        assert all(s.priority == SUBSCRIPTION_DOWNLOAD_PRIORITY for s in specs)

    def test_on_demand_check_fans_out_at_direct_priority(self):
        """Someone pressed Check now, so what it finds must not wait behind the cron backlog."""
        with (
            patch('tasks.subscriptions.get_subscription_impl', return_value=[_sub(7, 'Alpha')]),
            patch('tasks.subscriptions.get_url_info', return_value=_flat_info('Alpha', 2)),
            patch('tasks.media.filter_completed_downloads_impl', side_effect=lambda jobs: jobs),
            patch.object(orch, 'queued_count', return_value=0),
            patch.object(orch, 'submit_from_thread') as submit,
        ):
            result = run_subscription_check(JobContext(task_id='t'), 7)

        specs = [call.args[0] for call in submit.call_args_list]
        assert result == {'jobs_started': 2}
        assert all(s.job_name == POPULATE_JOB for s in specs)
        assert all(s.priority == DIRECT_DOWNLOAD_PRIORITY for s in specs)
        # The download chain each populate job becomes reads its priority off the payload.
        assert all(s.args[0]['priority'] == DIRECT_DOWNLOAD_PRIORITY for s in specs)

    @pytest.mark.parametrize(
        ('payload_priority', 'expected'),
        [
            (None, SUBSCRIPTION_DOWNLOAD_PRIORITY),
            (DIRECT_DOWNLOAD_PRIORITY, DIRECT_DOWNLOAD_PRIORITY),
        ],
    )
    def test_download_chain_takes_the_payload_priority_over_the_source_rule(
        self, payload_priority, expected
    ):
        job = serialize_download_job(
            DownloadJobDTO(
                url='https://www.youtube.com/watch?v=Sub00000001',
                media_type=MediaType.VIDEO,
                subscription_id=7,
                priority=payload_priority,
            )
        )
        with (
            patch('tasks.media._check_storage_quota', return_value=False),
            patch(
                'tasks.media._find_duplicate_active_tasks', return_value=(True, False, False, None)
            ),
            patch('tasks.media._persist_download_chain_state', return_value=('dl-1', None)),
            patch('tasks.media.tr_repo') as tr_repo,
        ):
            create_download_and_transcript_chains_impl(job)

        assert tr_repo.dispatch_download_chain.call_args.kwargs['priority'] == expected

    def test_on_demand_check_of_a_deleted_subscription_starts_nothing(self):
        with (
            patch('tasks.subscriptions.get_subscription_impl', return_value=[]),
            patch.object(orch, 'submit_from_thread') as submit,
        ):
            result = run_subscription_check(JobContext(task_id='t'), 7)

        assert result == {'jobs_started': 0}
        submit.assert_not_called()

    def test_direct_download_fanout_uses_direct_priority(self):
        jobs = [{'url': 'https://www.youtube.com/watch?v=Direct00001', 'user_id': 1}]
        with (
            patch('tasks.scheduling.expand_playlists_impl', return_value=jobs),
            patch('tasks.media.filter_completed_downloads_impl', return_value=jobs),
            patch.object(orch, 'submit_from_thread') as submit,
        ):
            run_direct_download_pipeline(None, jobs)

        specs = [call.args[0] for call in submit.call_args_list]
        assert specs, 'pipeline must fan out at least one populate job'
        assert all(s.job_name == POPULATE_JOB for s in specs)
        assert all(s.priority == DIRECT_DOWNLOAD_PRIORITY for s in specs)


def _run_pipeline_with_lane(ctx, jobs, *, depth, drain_per_poll):
    """Drive the pipeline against a simulated default lane.

    submit_from_thread grows the lane; each poll of the cancel event drains it, standing
    in for populate jobs completing while the producer waits.
    """
    seen = {'peak': 0}

    def _submit(_spec):
        depth['value'] += 1
        seen['peak'] = max(seen['peak'], depth['value'])

    def _poll(_timeout):
        depth['value'] = max(0, depth['value'] - drain_per_poll)
        return ctx.cancel_event.is_set()

    with (
        patch('tasks.subscriptions.get_all_subscriptions_impl', return_value=[{'id': 1}]),
        patch('tasks.subscriptions.create_download_jobs_from_subs_impl', return_value=jobs),
        patch('tasks.media.filter_completed_downloads_impl', return_value=jobs),
        patch.object(orch, 'queued_count', side_effect=lambda _lane: depth['value']),
        patch.object(orch, 'submit_from_thread', side_effect=_submit) as submit,
        patch.object(ctx.cancel_event, 'wait', side_effect=_poll),
    ):
        result = run_subscription_pipeline(ctx, JobType.CHANNEL_SUBSCRIPTION.value)

    return result, submit, seen['peak']


class TestFanOutBackpressure:
    """Peak default-lane depth must stay bounded however large the channel is.

    Populate jobs are untracked and in-memory only, so a 5,000-deep queue is both a
    memory spike and work a restart silently loses.
    """

    def _jobs(self, count):
        return [
            {'url': f'https://www.youtube.com/watch?v=Vid{i:08d}', 'user_id': 1}
            for i in range(count)
        ]

    def test_every_job_is_submitted_when_the_lane_keeps_up(self):
        ctx = JobContext(task_id='t')
        jobs = self._jobs(FANOUT_QUEUE_TARGET * 3)
        result, submit, _ = _run_pipeline_with_lane(
            ctx, jobs, depth={'value': 0}, drain_per_poll=25
        )

        assert result == {'jobs_started': len(jobs)}
        assert submit.call_count == len(jobs)

    def test_queue_depth_never_exceeds_the_target(self):
        """The whole point: throughput is unchanged, peak depth is bounded."""
        ctx = JobContext(task_id='t')
        jobs = self._jobs(FANOUT_QUEUE_TARGET * 5)
        result, _, peak = _run_pipeline_with_lane(ctx, jobs, depth={'value': 0}, drain_per_poll=10)

        assert result == {'jobs_started': len(jobs)}
        assert peak <= FANOUT_QUEUE_TARGET

    def test_a_prefilled_lane_is_topped_up_not_ignored(self):
        """Unlike the old guard, a busy lane delays the fan-out instead of dropping it."""
        ctx = JobContext(task_id='t')
        jobs = self._jobs(10)
        result, submit, peak = _run_pipeline_with_lane(
            ctx, jobs, depth={'value': FANOUT_QUEUE_TARGET * 4}, drain_per_poll=50
        )

        assert result == {'jobs_started': 10}
        assert submit.call_count == 10
        assert peak <= FANOUT_QUEUE_TARGET

    def test_cancellation_stops_the_fanout(self):
        ctx = JobContext(task_id='t')
        ctx.cancel_event.set()
        result, submit, _ = _run_pipeline_with_lane(
            ctx, self._jobs(10), depth={'value': 0}, drain_per_poll=0
        )

        assert result == {'jobs_started': 0}
        submit.assert_not_called()

    def test_a_wedged_lane_gives_up_instead_of_holding_the_subscriptions_lane(self):
        ctx = JobContext(task_id='t')
        result, submit, _ = _run_pipeline_with_lane(
            ctx, self._jobs(10), depth={'value': FANOUT_QUEUE_TARGET}, drain_per_poll=0
        )

        assert result == {'jobs_started': 0}
        submit.assert_not_called()

    def test_stall_budget_is_bounded(self):
        assert FANOUT_MAX_WAIT_SECONDS / FANOUT_POLL_SECONDS < 2000, (
            'poll loop must terminate in a sane number of iterations'
        )
