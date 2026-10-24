"""The HNSW index path returns as many rows as asked for, scoped or not.

The planner never picks the index at test scale, so these tests take the index path
on purpose: every scope counts as too large for the exact path, and the transaction
that sets up the iterative scan also turns off the plans that compete with the index.
The control case proves the index really ran: without the iterative scan the same
search stops at hnsw.ef_search rows.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from database import db
from services import transcript as transcript_service
from tests.transcript_seed import StubEmbedder, seed_media, seed_user, spread_vectors

FORCE_INDEX = (
    "SELECT set_config('enable_seqscan', 'off', true), set_config('enable_sort', 'off', true)"
)


@pytest.fixture(autouse=True)
def _clear_search_cache():
    transcript_service.semantic_cache.clear()
    yield
    transcript_service.semantic_cache.clear()


@pytest.fixture
def index_path(monkeypatch):
    """Every scope takes the index path; returns a switch for the iterative scan."""
    monkeypatch.setattr(transcript_service, '_EXACT_SCOPE_MEDIA_LIMIT', 0)
    real = transcript_service._use_iterative_index_scan
    state = {'iterative': True}

    async def forced(session, depth):
        await session.execute(text(FORCE_INDEX))
        if state['iterative']:
            await real(session, depth)

    monkeypatch.setattr(transcript_service, '_use_iterative_index_scan', forced)
    return state


@pytest.fixture
def library(clean_database, pgvector_schema):
    """150 blocks leaning toward the stub query, and 12 'needle' blocks far from it.

    The index is rebuilt from these rows alone: earlier tests leave the session-wide
    graph full of deleted, identical vectors, which leaves some live rows unreachable
    and would make what the index returns depend on test order.
    """
    session = db.get_sync_session()
    try:
        owner = seed_user(session, 'owner')
        noise = seed_media(
            session,
            owner_id=owner,
            url='https://example.com/noise',
            channel='Noise',
            title='Noise',
            released=datetime(2024, 1, 1, tzinfo=UTC),
            blocks=[f'noise {n} spacecraft' for n in range(150)],
            block_vectors=spread_vectors(150, seed=1, toward=0.05),
            grant_access_to=owner,
        )
        needle = seed_media(
            session,
            owner_id=owner,
            url='https://example.com/needle',
            channel='Needle',
            title='Needle',
            released=datetime(2024, 1, 2, tzinfo=UTC),
            blocks=[f'needle {n}' for n in range(12)],
            block_vectors=spread_vectors(12, seed=2, toward=-0.05),
            grant_access_to=owner,
        )
        session.execute(text('REINDEX INDEX ix_transcript_embeddings_embedding'))
        session.commit()
        return owner, noise, needle
    finally:
        session.close()


async def _search(owner, **kwargs):
    return await transcript_service.get_hybrid_search_results(
        StubEmbedder(), 'spacecraft', user_id=owner, rating_user_id=owner, **kwargs
    )


async def test_the_index_path_returns_the_full_limit(library, index_path):
    owner, _, _ = library

    hits = await _search(owner, semantic_weight=1.0, limit=100)

    assert len(hits) == 100


async def test_without_the_iterative_scan_the_index_stops_at_ef_search(library, index_path):
    owner, _, _ = library
    index_path['iterative'] = False

    hits = await _search(owner, semantic_weight=1.0, limit=100)

    assert len(hits) <= transcript_service._HNSW_EF_SEARCH_DEFAULT


async def test_a_narrow_scope_on_the_index_path_keeps_every_block(library, index_path):
    owner, _, needle = library

    hits = await _search(owner, semantic_weight=1.0, limit=50, channels=['Needle'])

    assert {hit['media_details']['id'] for hit in hits} == {needle}
    assert len(hits) == 12


async def test_the_hybrid_index_path_fills_its_candidates(library, index_path):
    owner, _, _ = library

    # No block contains the keyword, so every hit comes from the vector side.
    hits = await _search(owner, semantic_weight=0.5, limit=100, fts_text='zzzz')

    assert len(hits) == 100


async def test_iterative_scan_settings_last_only_for_the_transaction(pgvector_schema):
    async def setting(conn, name):
        return (await conn.execute(text(f"SELECT current_setting('{name}')"))).scalar_one()

    async with db._async_engine.connect() as conn:
        # Loading pgvector first registers its settings, so their defaults can be read.
        await conn.execute(text("SELECT '[1]'::vector"))
        await conn.commit()

        await transcript_service._use_iterative_index_scan(conn, 400)
        assert await setting(conn, 'hnsw.ef_search') == '400'
        assert await setting(conn, 'hnsw.iterative_scan') == 'strict_order'
        await conn.commit()

        assert await setting(conn, 'hnsw.ef_search') == '40'
        assert await setting(conn, 'hnsw.iterative_scan') == 'off'


@pytest.mark.parametrize(('depth', 'expected'), [(5, 40), (400, 400), (5000, 1000)])
def test_ef_search_is_clamped(depth, expected):
    assert transcript_service._hnsw_ef_search(depth) == expected
