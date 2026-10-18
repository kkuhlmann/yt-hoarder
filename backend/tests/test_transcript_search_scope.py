"""Transcript search is scoped to the media the library filter currently selects.

Every case here goes through real SQL against a real pgvector index, because the whole
class of bug this guards is invisible to a mock: a filtered vector search that returns
too few rows returns them successfully.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from database import db
from models import (
    MediaDetails,
    MediaType,
    TaskStatus,
    TranscriptBlock,
)
from services import transcript as transcript_service
from tests.transcript_seed import StubEmbedder, seed_media, seed_user


@pytest.fixture(autouse=True)
def _clear_search_cache():
    transcript_service.semantic_cache.clear()
    yield
    transcript_service.semantic_cache.clear()


@pytest.fixture
def library(clean_database, pgvector_schema):
    """Two channels in two different years, plus an unrelated second user."""
    session = db.get_sync_session()
    try:
        owner_id = seed_user(session, 'owner')
        stranger_id = seed_user(session, 'stranger')

        nasa_id = seed_media(
            session,
            owner_id=owner_id,
            url='https://example.com/nasa',
            channel='NASA',
            title='Lunar Lander Update',
            released=datetime(2024, 3, 4, tzinfo=UTC),
            blocks=['the spacecraft lander touches down', 'spacecraft orbital insertion burn'],
            grant_access_to=owner_id,
        )
        esa_id = seed_media(
            session,
            owner_id=owner_id,
            url='https://example.com/esa',
            channel='ESA',
            title='Ariane Launch Recap',
            released=datetime(2025, 7, 9, tzinfo=UTC),
            blocks=['the spacecraft launch window opens', 'spacecraft stage separation'],
            grant_access_to=owner_id,
        )
        return SimpleNamespace(
            owner_id=owner_id, stranger_id=stranger_id, nasa_id=nasa_id, esa_id=esa_id
        )
    finally:
        session.close()


async def _search(**kwargs):
    return await transcript_service.get_hybrid_search_results(
        StubEmbedder(), 'spacecraft', **kwargs
    )


def _media_ids(hits) -> set[int]:
    return {hit['media_details']['id'] for hit in hits}


WEIGHTS = [1.0, 0.0, 0.5]


@pytest.mark.parametrize('weight', WEIGHTS)
async def test_unscoped_search_spans_the_library(library, weight):
    hits = await _search(
        semantic_weight=weight, user_id=library.owner_id, rating_user_id=library.owner_id
    )

    assert _media_ids(hits) == {library.nasa_id, library.esa_id}


@pytest.mark.parametrize('weight', WEIGHTS)
async def test_channel_scope_excludes_the_other_channel(library, weight):
    hits = await _search(
        semantic_weight=weight,
        user_id=library.owner_id,
        rating_user_id=library.owner_id,
        channel='NASA',
    )

    assert _media_ids(hits) == {library.nasa_id}


@pytest.mark.parametrize('weight', WEIGHTS)
async def test_year_alone_scopes_to_the_whole_year(library, weight):
    hits = await _search(
        semantic_weight=weight,
        user_id=library.owner_id,
        rating_user_id=library.owner_id,
        date_field='released',
        date_year=2024,
    )

    assert _media_ids(hits) == {library.nasa_id}


@pytest.mark.parametrize('weight', WEIGHTS)
async def test_month_narrows_further_than_the_year(library, weight):
    in_month = await _search(
        semantic_weight=weight,
        user_id=library.owner_id,
        rating_user_id=library.owner_id,
        date_field='released',
        date_year=2024,
        date_month=3,
    )
    other_month = await _search(
        semantic_weight=weight,
        user_id=library.owner_id,
        rating_user_id=library.owner_id,
        date_field='released',
        date_year=2024,
        date_month=4,
    )

    assert _media_ids(in_month) == {library.nasa_id}
    assert other_month == []


@pytest.mark.parametrize('weight', WEIGHTS)
async def test_standard_search_honours_the_boolean_operators(library, weight):
    both = await _search(
        semantic_weight=weight,
        user_id=library.owner_id,
        rating_user_id=library.owner_id,
        standard_search='NASA || Ariane',
    )
    neither = await _search(
        semantic_weight=weight,
        user_id=library.owner_id,
        rating_user_id=library.owner_id,
        standard_search='NASA && Ariane',
    )

    assert _media_ids(both) == {library.nasa_id, library.esa_id}
    assert neither == []


@pytest.mark.parametrize('weight', WEIGHTS)
async def test_a_user_with_no_access_and_no_ownership_sees_nothing(library, weight):
    hits = await _search(
        semantic_weight=weight,
        user_id=library.stranger_id,
        rating_user_id=library.stranger_id,
    )

    assert hits == []


@pytest.mark.parametrize('weight', WEIGHTS)
async def test_owner_can_search_kept_transcripts_of_their_deleted_media(
    clean_database, pgvector_schema, weight
):
    """A soft delete drops every media_access row, the owner's included.

    Without the owner branch in the scope the transcripts survive on disk and in the
    index but become unreachable to the only person entitled to them.
    """
    session = db.get_sync_session()
    try:
        owner_id = seed_user(session, 'owner')
        stranger_id = seed_user(session, 'stranger')
        deleted_id = seed_media(
            session,
            owner_id=owner_id,
            url='https://example.com/gone',
            channel='NASA',
            title='Artemis Recap',
            released=datetime(2024, 3, 4, tzinfo=UTC),
            blocks=['the spacecraft crew module separated cleanly'],
            status=TaskStatus.DELETED.value,
            grant_access_to=None,
        )
    finally:
        session.close()

    as_owner = await _search(semantic_weight=weight, user_id=owner_id, rating_user_id=owner_id)
    as_stranger = await _search(
        semantic_weight=weight, user_id=stranger_id, rating_user_id=stranger_id
    )

    assert _media_ids(as_owner) == {deleted_id}
    assert as_stranger == []


@pytest.mark.parametrize('weight', WEIGHTS)
async def test_a_narrow_scope_still_returns_every_matching_block(
    clean_database, pgvector_schema, weight
):
    """A scope surrounded by nearer non-matching vectors still yields all of its blocks.

    The needle sits further from the query vector than all 60 haystack media, so any
    plan that ranks first and filters afterwards loses it. (At this size the planner
    picks a filtered scan whatever the query shape, so this cannot exercise pgvector's
    HNSW post-filter itself — `test_scoped_vector_cte_is_materialized` pins that.)
    """
    session = db.get_sync_session()
    try:
        owner_id = seed_user(session, 'owner')
        # The haystack sits nearer the query vector than the needle, so a post-filtered
        # scan would fill its candidate list before ever reaching the needle.
        for index in range(60):
            seed_media(
                session,
                owner_id=owner_id,
                url=f'https://example.com/noise-{index}',
                channel='Noise',
                title=f'Noise {index}',
                released=datetime(2025, 1, 1, tzinfo=UTC),
                blocks=['unrelated spacecraft chatter about the weather'],
                grant_access_to=owner_id,
                embedding_value=0.05,
            )
        needle_id = seed_media(
            session,
            owner_id=owner_id,
            url='https://example.com/needle',
            channel='Needle',
            title='Needle',
            released=datetime(2024, 6, 1, tzinfo=UTC),
            blocks=[f'the spacecraft lander touches down again {n}' for n in range(12)],
            grant_access_to=owner_id,
            embedding_value=-0.05,
        )
    finally:
        session.close()

    hits = await _search(
        semantic_weight=weight,
        user_id=owner_id,
        rating_user_id=owner_id,
        channel='Needle',
    )

    assert _media_ids(hits) == {needle_id}
    if weight == 1.0:
        assert len(hits) == 12


async def test_scope_is_part_of_the_cache_key(library):
    unscoped = await _search(
        semantic_weight=1.0, user_id=library.owner_id, rating_user_id=library.owner_id
    )
    scoped = await _search(
        semantic_weight=1.0,
        user_id=library.owner_id,
        rating_user_id=library.owner_id,
        channel='NASA',
    )

    assert _media_ids(unscoped) == {library.nasa_id, library.esa_id}
    assert _media_ids(scoped) == {library.nasa_id}


@pytest.mark.parametrize('weight', WEIGHTS)
async def test_channels_list_keeps_only_the_named_channels(library, weight):
    hits = await _search(
        semantic_weight=weight,
        user_id=library.owner_id,
        rating_user_id=library.owner_id,
        channels=['ESA'],
    )

    assert _media_ids(hits) == {library.esa_id}


@pytest.mark.parametrize('weight', WEIGHTS)
async def test_an_empty_channels_list_matches_nothing(library, weight):
    hits = await _search(
        semantic_weight=weight,
        user_id=library.owner_id,
        rating_user_id=library.owner_id,
        channels=[],
    )

    assert hits == []


@pytest.mark.parametrize('weight', WEIGHTS)
async def test_release_range_is_inclusive_of_from_and_exclusive_of_to(library, weight):
    hits = await _search(
        semantic_weight=weight,
        user_id=library.owner_id,
        rating_user_id=library.owner_id,
        released_from=datetime(2025, 7, 9),
        released_to=datetime(2025, 7, 10),
    )
    before = await _search(
        semantic_weight=weight,
        user_id=library.owner_id,
        rating_user_id=library.owner_id,
        released_to=datetime(2025, 7, 9),
    )

    assert _media_ids(hits) == {library.esa_id}
    assert _media_ids(before) == {library.nasa_id}


async def test_a_release_range_excludes_undated_media(library):
    session = db.get_sync_session()
    try:
        undated = MediaDetails(
            url='https://example.com/undated',
            media_type=MediaType.VIDEO,
            channel='NASA',
            title='Undated',
            status=TaskStatus.COMPLETE.value,
            owner_id=library.owner_id,
        )
        session.add(undated)
        session.commit()
        session.add(
            TranscriptBlock(
                start_time=0.0, end_time=10.0, text='spacecraft', media_details_id=undated.id
            )
        )
        session.commit()
    finally:
        session.close()

    hits = await _search(
        semantic_weight=0.0,
        user_id=library.owner_id,
        rating_user_id=library.owner_id,
        released_from=datetime(2000, 1, 1),
    )

    assert undated.id not in _media_ids(hits)


@pytest.mark.parametrize('weight', WEIGHTS)
async def test_channels_cannot_widen_a_pinned_channel(library, weight):
    hits = await _search(
        semantic_weight=weight,
        user_id=library.owner_id,
        rating_user_id=library.owner_id,
        channel='NASA',
        channels=['ESA'],
    )

    assert hits == []


@pytest.mark.parametrize('weight', [0.0, 0.5])
async def test_fts_text_drives_the_keyword_side_independently_of_the_query(library, weight):
    # 'lander' appears only in the NASA transcript; the embedded query matches both.
    keyword_only = await _search(
        semantic_weight=weight,
        user_id=library.owner_id,
        rating_user_id=library.owner_id,
        fts_text='lander',
    )

    assert {hit['media_details']['id'] for hit in keyword_only if hit['fts_rank']} == {
        library.nasa_id
    }


async def test_new_filters_are_part_of_the_cache_key(library):
    esa = await _search(
        semantic_weight=1.0,
        user_id=library.owner_id,
        rating_user_id=library.owner_id,
        channels=['ESA'],
    )
    nasa = await _search(
        semantic_weight=1.0,
        user_id=library.owner_id,
        rating_user_id=library.owner_id,
        channels=['NASA'],
    )
    nasa_2025 = await _search(
        semantic_weight=1.0,
        user_id=library.owner_id,
        rating_user_id=library.owner_id,
        channels=['NASA'],
        released_from=datetime(2025, 1, 1),
    )

    assert _media_ids(esa) == {library.esa_id}
    assert _media_ids(nasa) == {library.nasa_id}
    assert nasa_2025 == []


async def test_hits_carry_the_upload_date(library):
    hits = await _search(
        semantic_weight=1.0,
        user_id=library.owner_id,
        rating_user_id=library.owner_id,
        channels=['NASA'],
    )

    assert {hit['media_details']['release_timestamp'] for hit in hits} == {datetime(2024, 3, 4)}
