"""Transcript database storage in the Stats view: real table sizes apportioned by channel.

The arithmetic is tested on hand-built snapshots. The database tests assert invariants
rather than exact byte counts: autovacuum runs in the test container, so a table's size
depends on what earlier tests deleted.
"""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from database import db
from models import MediaType, Playlist, PlaylistMedia, TaskStatus
from repositories import stats as stats_repo
from repositories.stats import TranscriptStorageSnapshot, _apportion_transcript_storage
from tests.transcript_seed import seed_media, seed_user

RELEASED = datetime(2025, 5, 6, tzinfo=UTC)
LONG = 'the rocket engine ignites and the booster climbs away from the pad ' * 4


@pytest.fixture(autouse=True)
def _fresh_stats_caches(monkeypatch):
    # A fresh lock per test: one that waited under a previous test's event loop stays
    # bound to it.
    monkeypatch.setattr(stats_repo, '_transcript_storage_lock', asyncio.Lock())
    stats_repo._cache.clear()
    stats_repo._transcript_storage_cache.clear()
    yield
    stats_repo._cache.clear()
    stats_repo._transcript_storage_cache.clear()


def _snapshot(per_media, *, blocks_bytes=1000, embeddings_bytes=3000, database_bytes=10_000):
    return TranscriptStorageSnapshot(blocks_bytes, embeddings_bytes, database_bytes, per_media)


def _by_name(result) -> dict[str, dict]:
    return {c['channel']: c for c in result['by_channel']}


# --- Apportioning -------------------------------------------------------------------


def test_each_table_is_split_by_its_own_share():
    # Weights sum to 1000 and embeddings to 4.
    snap = _snapshot({1: (2, 300, 2), 2: (1, 100, 1), 3: (1, 600, 1)})

    result = _apportion_transcript_storage(
        [(1, 'A'), (2, 'A'), (3, 'B')], snap, include_database=True
    )

    assert result['by_channel'] == [
        {
            'channel': 'A',
            'total_bytes': 400 + 2250,
            'text_bytes': 400,
            'embedding_bytes': 2250,
            'transcript_count': 2,
            'block_count': 3,
        },
        {
            'channel': 'B',
            'total_bytes': 600 + 750,
            'text_bytes': 600,
            'embedding_bytes': 750,
            'transcript_count': 1,
            'block_count': 1,
        },
    ]
    assert result['total_bytes'] == 4000
    assert result['text_bytes'] == 1000
    assert result['embedding_bytes'] == 3000
    assert result['database_bytes'] == 10_000


def test_a_scoped_view_keeps_the_instance_wide_shares():
    """Otherwise filtering to one channel would hand it both tables outright."""
    snap = _snapshot({1: (2, 300, 2), 2: (1, 100, 1), 3: (1, 600, 1)})

    result = _apportion_transcript_storage([(3, 'B')], snap, include_database=True)

    assert result['total_bytes'] == 600 + 750


def test_totals_are_sums_of_the_floored_channel_figures():
    snap = _snapshot({1: (1, 1, 1), 2: (1, 1, 1), 3: (1, 1, 1)}, blocks_bytes=10)

    result = _apportion_transcript_storage(
        [(1, 'A'), (2, 'B'), (3, 'C')], snap, include_database=False
    )

    assert [c['text_bytes'] for c in result['by_channel']] == [3, 3, 3]
    assert result['text_bytes'] == 9
    assert result['total_bytes'] == sum(c['total_bytes'] for c in result['by_channel'])


def test_media_missing_from_the_snapshot_is_skipped():
    """Not transcribed, or created after the snapshot was taken."""
    snap = _snapshot({1: (1, 10, 1)})

    result = _apportion_transcript_storage([(1, 'A'), (99, 'Z')], snap, include_database=False)

    assert list(_by_name(result)) == ['A']


def test_nothing_embedded_gives_no_embedding_bytes():
    snap = _snapshot({1: (1, 50, 0)})

    result = _apportion_transcript_storage([(1, 'A')], snap, include_database=False)

    assert result['text_bytes'] == 1000
    assert result['embedding_bytes'] == 0


def test_an_empty_snapshot_gives_zeros():
    result = _apportion_transcript_storage([(1, 'A')], _snapshot({}), include_database=False)

    assert result == stats_repo._empty_transcript_storage()


def test_database_size_only_when_asked_for():
    snap = _snapshot({1: (1, 10, 1)})

    assert _apportion_transcript_storage([], snap, include_database=False)['database_bytes'] is None
    assert (
        _apportion_transcript_storage([], snap, include_database=True)['database_bytes'] == 10_000
    )


def test_channels_sort_largest_first_then_by_name():
    snap = _snapshot({1: (1, 10, 1), 2: (1, 10, 1), 3: (1, 30, 3)})

    result = _apportion_transcript_storage(
        [(1, 'b'), (2, 'a'), (3, 'c')], snap, include_database=False
    )

    assert [c['channel'] for c in result['by_channel']] == ['c', 'a', 'b']


# --- Against the database -------------------------------------------------------------


@pytest.fixture
def library(clean_database, pgvector_schema):
    """Two users' transcripts across channels, covering every scope rule."""
    session = db.get_sync_session()
    try:
        me = seed_user(session, 'me')
        other = seed_user(session, 'other')

        def media(url, channel, blocks, *, owner=me, **kw):
            return seed_media(
                session,
                owner_id=owner,
                url=f'https://www.youtube.com/watch?v={url}',
                channel=channel,
                title=url,
                released=RELEASED,
                blocks=blocks,
                **kw,
            )

        # Both rows of one URL occupy space, superseded transcript-only entry included.
        lecture_transcript = media(
            'lecture0001',
            'Lectures',
            [LONG] * 3,
            media_type=MediaType.TRANSCRIPT,
            grant_access_to=me,
        )
        lecture_video = media('lecture0001', 'Lectures', [LONG] * 3, grant_access_to=me)
        podcast = media('podcast0001', 'Podcasts', ['short'], grant_access_to=me)
        # A soft delete drops every MediaAccess row; only owner_id still reaches it.
        kept = media('podcast0002', 'Podcasts', ['kept'], status=TaskStatus.DELETED.value)
        secret = media('secret00001', 'Secret', [LONG], owner=other, grant_access_to=other)
        nameless = media('nameless001', None, ['who made this'], grant_access_to=me)
        captions = media('captions001', 'Captions', ['no vectors here'], grant_access_to=me)
        session.execute(
            text(
                'DELETE FROM transcript_embeddings WHERE transcript_block_id IN '
                '(SELECT id FROM transcript_blocks WHERE media_details_id = :m)'
            ),
            {'m': captions},
        )
        session.commit()

        return SimpleNamespace(
            me=me,
            other=other,
            lecture_transcript=lecture_transcript,
            lecture_video=lecture_video,
            podcast=podcast,
            kept=kept,
            secret=secret,
            nameless=nameless,
            captions=captions,
        )
    finally:
        session.close()


async def test_admin_view_counts_every_transcript(library):
    result = await stats_repo.get_transcript_storage_stats()
    channels = _by_name(result)

    assert set(channels) == {'Lectures', 'Podcasts', 'Secret', 'Unknown', 'Captions'}
    assert channels['Lectures']['transcript_count'] == 2
    assert channels['Lectures']['block_count'] == 6
    assert channels['Podcasts']['transcript_count'] == 2
    assert result['total_bytes'] == sum(c['total_bytes'] for c in result['by_channel'])
    assert result['total_bytes'] == result['text_bytes'] + result['embedding_bytes']
    assert result['database_bytes'] >= result['total_bytes'] > 0


async def test_a_channel_with_no_embeddings_has_only_text(library):
    captions = _by_name(await stats_repo.get_transcript_storage_stats())['Captions']

    assert captions['embedding_bytes'] == 0
    assert captions['text_bytes'] > 0


async def test_a_user_sees_what_they_can_access_or_own(library):
    result = await stats_repo.get_transcript_storage_stats(user_id=library.me)
    channels = _by_name(result)

    assert set(channels) == {'Lectures', 'Podcasts', 'Unknown', 'Captions'}
    assert channels['Podcasts']['transcript_count'] == 2, 'the deleted item keeps its transcript'
    assert result['database_bytes'] is None


async def test_another_users_view_is_theirs_alone(library):
    result = await stats_repo.get_transcript_storage_stats(user_id=library.other)

    assert list(_by_name(result)) == ['Secret']


async def test_a_channel_filter_keeps_that_channels_share(library):
    everything = await stats_repo.get_transcript_storage_stats()
    filtered = await stats_repo.get_transcript_storage_stats(channel='Lectures')

    assert filtered['by_channel'] == [_by_name(everything)['Lectures']]
    assert filtered['total_bytes'] < everything['total_bytes']


async def test_a_playlist_filter_counts_only_its_media(library):
    session = db.get_sync_session()
    try:
        playlist = Playlist(name='Listening', user_id=library.me)
        empty = Playlist(name='Nothing yet', user_id=library.me)
        session.add_all([playlist, empty])
        session.commit()
        session.add(
            PlaylistMedia(playlist_id=playlist.id, media_details_id=library.podcast, position=1)
        )
        session.commit()
        playlist_id, empty_id = playlist.id, empty.id
    finally:
        session.close()

    result = await stats_repo.get_transcript_storage_stats(playlist_id=playlist_id)
    assert [(c['channel'], c['transcript_count']) for c in result['by_channel']] == [
        ('Podcasts', 1)
    ]

    result = await stats_repo.get_transcript_storage_stats(playlist_id=empty_id)
    assert result == stats_repo._empty_transcript_storage()


# --- Caching ----------------------------------------------------------------------------


@pytest.fixture
def snapshot_loads(monkeypatch):
    """Counts how often the expensive snapshot query actually runs."""
    loads = []
    real = stats_repo._load_transcript_storage_snapshot

    async def counting(session):
        loads.append(1)
        await asyncio.sleep(0.05)  # long enough for a concurrent request to arrive
        return await real(session)

    monkeypatch.setattr(stats_repo, '_load_transcript_storage_snapshot', counting)
    return loads


async def test_every_view_shares_one_snapshot(library, snapshot_loads):
    await stats_repo.get_transcript_storage_stats()
    await stats_repo.get_transcript_storage_stats(user_id=library.me)
    await stats_repo.get_transcript_storage_stats(channel='Lectures')

    assert len(snapshot_loads) == 1


async def test_concurrent_cold_requests_load_it_once(library, snapshot_loads):
    await asyncio.gather(
        stats_repo.get_transcript_storage_stats(),
        stats_repo.get_transcript_storage_stats(user_id=library.me),
    )

    assert len(snapshot_loads) == 1


async def test_an_expired_snapshot_is_loaded_again(library, snapshot_loads):
    await stats_repo.get_transcript_storage_stats()
    stats_repo._transcript_storage_cache.clear()
    stats_repo._cache.clear()
    await stats_repo.get_transcript_storage_stats()

    assert len(snapshot_loads) == 2


# --- Endpoint ---------------------------------------------------------------------------


def test_endpoint_reports_database_size_only_in_admin_view(authenticated_client, pgvector_schema):
    resp = authenticated_client.get('/stats/transcript-storage')
    assert resp.status_code == 200
    assert resp.json()['database_bytes'] is None

    resp = authenticated_client.get('/stats/transcript-storage', params={'admin_view': 'true'})
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body['database_bytes'], int)
    assert body['total_bytes'] > 0
