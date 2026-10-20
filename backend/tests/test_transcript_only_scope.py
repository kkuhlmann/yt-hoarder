"""Where transcript-only media shows up: never the library, always search and the
Transcripts view, and never twice once the user has the full media's transcript too.

Real SQL throughout. The supersede rule is a correlated subquery spliced into the same
scope the transcript search compiles to raw SQL, so a mock would prove nothing.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import delete as sa_delete
from sqlmodel import select

from database import db
from models import LIBRARY_MEDIA_TYPES, MediaAccess, MediaType, SourceType, TaskStatus
from repositories import media_details as md_repo
from schemas import DownloadJobDTO
from serializers import serialize_download_job
from services import transcript as transcript_service
from tasks.media import filter_completed_downloads_impl
from tests.transcript_seed import StubEmbedder, seed_media, seed_user

RELEASED = datetime(2025, 5, 6, tzinfo=UTC)
TALK = 'https://www.youtube.com/watch?v=talkOnly001'
BOTH = 'https://www.youtube.com/watch?v=haveBoth001'
KEPT = 'https://www.youtube.com/watch?v=deletedKept'
BARE = 'https://www.youtube.com/watch?v=noTranscrip'
PENDING = 'https://www.youtube.com/watch?v=stillStaged'


@pytest.fixture(autouse=True)
def _clear_search_cache():
    transcript_service.semantic_cache.clear()
    yield
    transcript_service.semantic_cache.clear()


def _grant(session, user_id: int, media_id: int) -> None:
    session.add(
        MediaAccess(
            user_id=user_id, media_details_id=media_id, source_type=SourceType.DIRECT, source_id=0
        )
    )
    session.commit()


@pytest.fixture
def corpus(clean_database, pgvector_schema):
    """One user's mix of transcript-only and library media, plus a second user."""
    session = db.get_sync_session()
    try:
        me = seed_user(session, 'me')
        other = seed_user(session, 'other')

        def media(url, channel, *, owner=me, blocks=('rocket engines ignite',), **kw):
            return seed_media(
                session,
                owner_id=owner,
                url=url,
                channel=channel,
                title=url[-11:],
                released=RELEASED,
                blocks=list(blocks),
                **kw,
            )

        talk = media(TALK, 'Lectures', media_type=MediaType.TRANSCRIPT, grant_access_to=me)
        # The other user indexed BOTH transcript-only first and shared it; I also have the
        # full video with a transcript, which must replace it for me but not for them.
        both_transcript = media(
            BOTH,
            'Lectures',
            owner=other,
            media_type=MediaType.TRANSCRIPT,
            grant_access_to=other,
        )
        _grant(session, me, both_transcript)
        both_video = media(BOTH, 'Lectures', grant_access_to=me)
        # Soft delete drops every MediaAccess row; only owner_id keeps it reachable.
        kept = media(KEPT, 'Podcasts', status=TaskStatus.DELETED.value)
        bare = media(BARE, 'Podcasts', blocks=(), grant_access_to=me)
        pending = media(
            PENDING,
            'Podcasts',
            blocks=(),
            media_type=MediaType.TRANSCRIPT,
            status=TaskStatus.POSTPROCESSING.value,
            grant_access_to=me,
        )
        return SimpleNamespace(
            me=me,
            other=other,
            talk=talk,
            both_transcript=both_transcript,
            both_video=both_video,
            kept=kept,
            bare=bare,
            pending=pending,
        )
    finally:
        session.close()


async def _listed(user_id, **kwargs) -> set[int]:
    result = await md_repo.get_all_media_details(
        status=kwargs.pop('status', TaskStatus.COMPLETE.value),
        page_size=None,
        user_id=user_id,
        rating_user_id=user_id,
        **kwargs,
    )
    return {r['id'] for r in result['records']}


async def test_library_never_lists_transcript_only_media(corpus):
    listed = await _listed(corpus.me, media_types=list(LIBRARY_MEDIA_TYPES))

    assert listed == {corpus.both_video, corpus.bare}


async def test_transcript_type_lists_only_the_unsuperseded_ones(corpus):
    listed = await _listed(corpus.me, media_types=[MediaType.TRANSCRIPT])

    assert listed == {corpus.talk}, 'BOTH is replaced by my own full video'


async def test_transcripts_view_lists_everything_searchable_once(corpus):
    listed = await _listed(corpus.me, has_transcript=True)

    # Transcript-only, the full video in its place, and a deleted video's kept
    # transcript. Not the untranscribed video, nor the item still mid-Whisper.
    assert listed == {corpus.talk, corpus.both_video, corpus.kept}


async def test_supersede_is_decided_per_viewer(corpus):
    """The other user can't see my video, so their transcript-only entry stays theirs."""
    listed = await _listed(corpus.other, has_transcript=True)

    assert listed == {corpus.both_transcript}


async def test_admin_all_users_view_keeps_both_rows(corpus):
    listed = await _listed(None, has_transcript=True)

    assert {corpus.both_transcript, corpus.both_video} <= listed


async def test_transcripts_view_rows_carry_their_block_counts(corpus):
    result = await md_repo.get_all_media_details(
        status=TaskStatus.COMPLETE.value,
        page_size=None,
        user_id=corpus.me,
        rating_user_id=corpus.me,
        has_transcript=True,
    )

    assert {r['transcript_block_count'] for r in result['records']} == {1}


async def test_group_counts_match_the_transcripts_view(corpus):
    groups = await md_repo.get_media_groups(
        group_by='channel',
        user_id=corpus.me,
        rating_user_id=corpus.me,
        has_transcript=True,
    )
    counts = {g['key']: g['count'] for g in groups['groups']}

    assert counts == {'Lectures': 2, 'Podcasts': 1}
    for channel, count in counts.items():
        assert len(await _listed(corpus.me, has_transcript=True, channel=channel)) == count
    lectures = next(g for g in groups['groups'] if g['key'] == 'Lectures')
    assert lectures['transcript_count'] == 1
    assert lectures['video_count'] == 1


async def test_released_year_and_month_folders_narrow_the_view(corpus):
    in_month = await _listed(
        corpus.me, has_transcript=True, date_field='released', date_year=2025, date_month=5
    )
    other_month = await _listed(
        corpus.me, has_transcript=True, date_field='released', date_year=2025, date_month=6
    )

    assert in_month == {corpus.talk, corpus.both_video, corpus.kept}
    assert other_month == set()


@pytest.mark.parametrize('weight', [1.0, 0.0, 0.5])
async def test_search_finds_transcript_only_media_without_duplicates(corpus, weight):
    hits = await transcript_service.get_hybrid_search_results(
        StubEmbedder(),
        'rocket',
        semantic_weight=weight,
        user_id=corpus.me,
        rating_user_id=corpus.me,
    )

    assert {h['media_details']['id'] for h in hits} == {
        corpus.talk,
        corpus.both_video,
        corpus.kept,
    }


@pytest.mark.parametrize('weight', [1.0, 0.0, 0.5])
async def test_a_channel_folder_narrows_the_search(corpus, weight):
    hits = await transcript_service.get_hybrid_search_results(
        StubEmbedder(),
        'rocket',
        semantic_weight=weight,
        user_id=corpus.me,
        rating_user_id=corpus.me,
        channel='Podcasts',
    )

    assert {h['media_details']['id'] for h in hits} == {corpus.kept}


def test_full_transcript_lookup_for_the_subscription_filter(corpus):
    urls = [TALK, BOTH, KEPT, BARE, PENDING]

    assert md_repo.sync_get_urls_with_full_transcript(urls, corpus.me) == {BOTH, KEPT}
    assert md_repo.sync_get_urls_with_full_transcript(urls, corpus.other) == set()
    assert md_repo.sync_get_urls_with_full_transcript(urls, None) == {BOTH, KEPT}


async def test_full_transcript_check_for_a_direct_request(corpus):
    assert await md_repo.has_full_transcript(BOTH, corpus.me)
    assert not await md_repo.has_full_transcript(TALK, corpus.me)
    assert not await md_repo.has_full_transcript(BARE, corpus.me), 'no transcript yet'
    assert not await md_repo.has_full_transcript(BOTH, corpus.other)


async def test_library_stats_leave_transcript_only_media_out(corpus):
    library = await md_repo.get_media_stats(
        user_id=corpus.me, media_types=list(LIBRARY_MEDIA_TYPES)
    )
    transcripts = await md_repo.get_media_stats(
        user_id=corpus.me, media_types=None, has_transcript=True
    )

    assert library['total_downloads'] == 2
    assert transcripts['total_downloads'] == 3
    assert transcripts['downloads_with_transcripts'] == 3


def _job(url: str, user_id: int) -> dict:
    return serialize_download_job(
        DownloadJobDTO(
            url=url,
            media_type=MediaType.TRANSCRIPT,
            audio_only=True,
            generate_transcript=True,
            user_id=user_id,
            subscription_id=1,
        )
    )


def _access_rows(user_id: int, media_id: int) -> int:
    session = db.get_sync_session()
    try:
        return len(
            session.execute(
                select(MediaAccess).where(
                    MediaAccess.user_id == user_id, MediaAccess.media_details_id == media_id
                )
            ).all()
        )
    finally:
        session.close()


def test_subscription_filter_drops_superseded_jobs_without_a_grant(corpus):
    new_url = 'https://www.youtube.com/watch?v=brandNew001'
    session = db.get_sync_session()
    try:
        session.execute(
            sa_delete(MediaAccess).where(
                MediaAccess.user_id == corpus.me,
                MediaAccess.media_details_id == corpus.both_transcript,
            )
        )
        session.commit()
    finally:
        session.close()

    kept = filter_completed_downloads_impl(
        [_job(BOTH, corpus.me), _job(TALK, corpus.other), _job(new_url, corpus.me)]
    )

    assert [job['url'] for job in kept] == [new_url]
    # Re-granting would put the replaced entry back in front of me on every tick.
    assert _access_rows(corpus.me, corpus.both_transcript) == 0
    # Ordinary cross-user dedup still applies to transcript-only rows.
    assert _access_rows(corpus.other, corpus.talk) == 1
