"""The MCP tools, called through the SDK's in-memory client against real pgvector SQL.

The principal is set the way the HTTP endpoint sets it, around the client: the client's
server tasks are created inside that context, so the tools see it.
"""

import logging
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from mcp import Client
from sqlalchemy import text

from database import db
from mcp_server import formatting
from mcp_server import server as mcp_server_module
from mcp_server.context import McpPrincipal, acting_as
from mcp_server.server import build_mcp_server
from models import MediaTag, Tag, TaskStatus
from services import transcript as transcript_service
from services.transcript import _aggregate_segments_into_blocks
from tests.transcript_seed import StubEmbedder, seed_media, seed_user

BASE = 'http://nas:8000'


@pytest.fixture(autouse=True)
def _clear_search_cache():
    transcript_service.semantic_cache.clear()
    yield
    transcript_service.semantic_cache.clear()


@pytest.fixture
def server():
    return build_mcp_server(embedder_factory=StubEmbedder)


@pytest.fixture
def library(clean_database, pgvector_schema):
    """The owner's two channels, a deleted video, and a video a friend shared with them."""
    session = db.get_sync_session()
    try:
        owner = seed_user(session, 'owner')
        friend = seed_user(session, 'friend')
        stranger = seed_user(session, 'stranger')
        nasa = seed_media(
            session,
            owner_id=owner,
            url='https://www.youtube.com/watch?v=nasa1',
            channel='NASA Official',
            title='Lunar Lander Update',
            released=datetime(2024, 3, 4, tzinfo=UTC),
            blocks=[
                'the spacecraft lander touches down',
                'spacecraft orbital insertion burn',
                'and now the weather',
            ],
            grant_access_to=owner,
        )
        esa = seed_media(
            session,
            owner_id=owner,
            url='https://www.youtube.com/watch?v=esa1',
            channel='European Space Agency',
            title='Ariane Launch Recap',
            released=datetime(2025, 7, 9, tzinfo=UTC),
            blocks=['the spacecraft launch window opens'],
            grant_access_to=owner,
        )
        deleted = seed_media(
            session,
            owner_id=owner,
            url='https://www.youtube.com/watch?v=gone1',
            channel='NASA Official',
            title='Artemis Archive',
            released=datetime(2023, 5, 1, tzinfo=UTC),
            blocks=['spacecraft archive footage'],
            status=TaskStatus.DELETED.value,
        )
        shared = seed_media(
            session,
            owner_id=friend,
            url='https://www.youtube.com/watch?v=friend1',
            channel='Friend Channel',
            title='Hobby Build',
            released=datetime(2025, 1, 2, tzinfo=UTC),
            blocks=['a spacecraft hobby build'],
            grant_access_to=owner,
        )
        tag = Tag(user_id=owner, name='Space')
        session.add(tag)
        session.commit()
        session.add(MediaTag(user_id=owner, media_details_id=nasa, tag_id=tag.id))
        session.commit()
        return SimpleNamespace(
            owner=owner,
            friend=friend,
            stranger=stranger,
            nasa=nasa,
            esa=esa,
            deleted=deleted,
            shared=shared,
        )
    finally:
        session.close()


async def call(server, user_id: int, tool: str, args: dict | None = None):
    principal = McpPrincipal(user_id=user_id, username='u', token_id=1, base_url=BASE)
    with acting_as(principal):
        async with Client(server) as client:
            return await client.call_tool(tool, args or {})


def _media_ids(result) -> set[int]:
    return {hit['media_id'] for hit in result.structured_content['results']}


# --- search_transcripts ---


async def test_search_sees_own_shared_and_own_deleted_media(server, library):
    result = await call(server, library.owner, 'search_transcripts', {'query': 'spacecraft'})

    assert not result.is_error
    assert _media_ids(result) == {library.nasa, library.esa, library.deleted, library.shared}


async def test_search_sees_nothing_it_has_no_access_to(server, library):
    result = await call(server, library.stranger, 'search_transcripts', {'query': 'spacecraft'})

    assert _media_ids(result) == set()


async def test_friend_does_not_see_the_owners_media(server, library):
    result = await call(server, library.friend, 'search_transcripts', {'query': 'spacecraft'})

    assert _media_ids(result) == {library.shared}


async def test_channels_resolve_loosely_and_report_what_was_used(server, library):
    result = await call(
        server, library.owner, 'search_transcripts', {'query': 'spacecraft', 'channels': ['nasa']}
    )

    applied = result.structured_content['applied']
    assert applied['channels'] == ['NASA Official']
    assert _media_ids(result) == {library.nasa, library.deleted}


async def test_an_unmatched_channel_searches_nothing_instead_of_everything(server, library):
    result = await call(
        server,
        library.owner,
        'search_transcripts',
        {'query': 'spacecraft', 'channels': ['Discovery']},
    )

    applied = result.structured_content['applied']
    assert result.structured_content['results'] == []
    assert applied['unmatched_channels'] == ['Discovery']
    assert any('Nothing was searched' in note for note in applied['notes'])


async def test_a_partly_matched_channel_list_searches_the_matches(server, library):
    result = await call(
        server,
        library.owner,
        'search_transcripts',
        {'query': 'spacecraft', 'channels': ['european space', 'Discovery']},
    )

    applied = result.structured_content['applied']
    assert applied['channels'] == ['European Space Agency']
    assert applied['unmatched_channels'] == ['Discovery']
    assert _media_ids(result) == {library.esa}


async def test_title_contains_narrows_to_matching_titles_and_is_reported(server, library):
    result = await call(
        server,
        library.owner,
        'search_transcripts',
        {'query': 'spacecraft', 'title_contains': 'ariane'},
    )

    assert _media_ids(result) == {library.esa}
    assert result.structured_content['applied']['title_contains'] == 'ariane'
    assert 'title contains: ariane' in result.content[0].text


async def test_title_contains_that_matches_nothing_finds_nothing(server, library):
    result = await call(
        server,
        library.owner,
        'search_transcripts',
        {'query': 'spacecraft', 'title_contains': 'no such title'},
    )

    assert result.structured_content['results'] == []


async def test_title_contains_without_terms_is_an_error_the_model_can_read(server, library):
    result = await call(
        server,
        library.owner,
        'search_transcripts',
        {'query': 'spacecraft', 'title_contains': ' && '},
    )

    assert result.is_error
    assert 'no search terms' in result.content[0].text


async def test_dates_accept_partial_forms_and_bound_inclusively(server, library):
    from_2025 = await call(
        server,
        library.owner,
        'search_transcripts',
        {'query': 'spacecraft', 'released_from': '2025'},
    )
    to_march_2024 = await call(
        server,
        library.owner,
        'search_transcripts',
        {'query': 'spacecraft', 'released_to': '2024-03'},
    )

    assert _media_ids(from_2025) == {library.esa, library.shared}
    assert from_2025.structured_content['applied']['released_from'] == '2025-01-01'
    assert _media_ids(to_march_2024) == {library.nasa, library.deleted}
    assert to_march_2024.structured_content['applied']['released_to'] == '2024-03-31'


async def test_a_malformed_date_is_an_error_the_model_can_read(server, library):
    result = await call(
        server,
        library.owner,
        'search_transcripts',
        {'query': 'spacecraft', 'released_from': 'last March'},
    )

    assert result.is_error
    assert 'YYYY-MM-DD' in result.content[0].text


async def test_tags_filter_by_the_users_own_tag_names(server, library):
    tagged = await call(
        server, library.owner, 'search_transcripts', {'query': 'spacecraft', 'tags': ['space']}
    )
    unknown = await call(
        server, library.owner, 'search_transcripts', {'query': 'spacecraft', 'tags': ['nope']}
    )

    assert _media_ids(tagged) == {library.nasa}
    assert unknown.structured_content['results'] == []
    assert unknown.structured_content['applied']['unmatched_tags'] == ['nope']


async def test_results_link_to_the_source_and_into_the_app(server, library):
    result = await call(
        server, library.owner, 'search_transcripts', {'query': 'spacecraft', 'channels': ['NASA']}
    )

    hits = {hit['media_id']: hit for hit in result.structured_content['results']}
    nasa = hits[library.nasa]
    assert nasa['source_url'] == 'https://www.youtube.com/watch?v=nasa1&t=0'
    assert nasa['app_url'] == f'{BASE}/?media={library.nasa}&t=0'
    assert nasa['uploaded'] == '2024-03-04'
    # A deleted file cannot be played in the app, so only the source link is offered.
    assert hits[library.deleted]['app_url'] is None
    assert hits[library.deleted]['source_url'] is not None


async def test_adjacent_hits_in_one_video_merge_into_one_passage(server, library):
    result = await call(
        server, library.owner, 'search_transcripts', {'query': 'spacecraft', 'channels': ['NASA']}
    )

    nasa_passages = [
        hit for hit in result.structured_content['results'] if hit['media_id'] == library.nasa
    ]
    assert len(nasa_passages) == 1
    assert 'lander touches down' in nasa_passages[0]['text']
    assert 'orbital insertion' in nasa_passages[0]['text']
    assert nasa_passages[0]['end_seconds'] > nasa_passages[0]['start_seconds']


async def test_limit_is_clamped_rather_than_rejected(server, library):
    one = await call(
        server, library.owner, 'search_transcripts', {'query': 'spacecraft', 'limit': 0}
    )
    many = await call(
        server, library.owner, 'search_transcripts', {'query': 'spacecraft', 'limit': 999}
    )

    assert len(one.structured_content['results']) == 1
    assert one.structured_content['truncated'] is True
    assert not many.is_error


@pytest.fixture
def ten_videos(clean_database, pgvector_schema):
    """Ten one-block videos, so each hit is its own passage."""
    session = db.get_sync_session()
    try:
        owner = seed_user(session, 'owner')
        for n in range(10):
            seed_media(
                session,
                owner_id=owner,
                url=f'https://www.youtube.com/watch?v=v{n}',
                channel='Talks',
                title=f'Talk {n}',
                released=datetime(2025, 1, n + 1, tzinfo=UTC),
                blocks=[f'spacecraft passage {n}'],
                grant_access_to=owner,
            )
        return owner
    finally:
        session.close()


async def _search_pages(server, owner, **args) -> list[dict]:
    pages = []
    offset = 0
    while offset is not None and len(pages) < 20:
        result = await call(
            server, owner, 'search_transcripts', {'query': 'spacecraft', **args, 'offset': offset}
        )
        pages.append(result.structured_content)
        offset = result.structured_content['next_offset']
    return pages


async def test_search_pages_follow_next_offset_without_repeats(server, ten_videos):
    pages = await _search_pages(server, ten_videos, limit=3)

    blocks = [hit['block_id'] for page in pages for hit in page['results']]
    assert [len(page['results']) for page in pages] == [3, 3, 3, 1]
    assert len(blocks) == len(set(blocks)) == 10
    assert pages[-1]['next_offset'] is None


async def test_search_pages_share_one_search(server, ten_videos, monkeypatch):
    calls = []
    real = transcript_service._semantic_search

    async def counted(*args, **kwargs):
        calls.append(args)
        return await real(*args, **kwargs)

    monkeypatch.setattr(transcript_service, '_semantic_search', counted)

    await _search_pages(server, ten_videos, limit=4)

    assert len(calls) == 1


async def test_a_page_cut_short_by_the_budget_continues_where_it_stopped(
    server, ten_videos, monkeypatch
):
    monkeypatch.setattr(formatting, 'RESPONSE_MAX_CHARS', 0)

    pages = await _search_pages(server, ten_videos, limit=5)

    blocks = [hit['block_id'] for page in pages for hit in page['results']]
    assert len(pages) == 10
    assert len(set(blocks)) == 10


async def test_an_offset_past_the_end_finds_nothing_and_says_so(server, ten_videos):
    result = await call(
        server, ten_videos, 'search_transcripts', {'query': 'spacecraft', 'offset': 50}
    )

    assert result.structured_content['results'] == []
    assert result.structured_content['next_offset'] is None
    assert 'No passages from offset=50: this search found 10.' in result.content[0].text


async def test_a_search_that_fills_its_depth_says_more_may_rank_below(
    server, ten_videos, monkeypatch
):
    monkeypatch.setattr(mcp_server_module, 'SEARCH_FETCH_DEPTH', 3)

    result = await call(
        server, ten_videos, 'search_transcripts', {'query': 'spacecraft', 'limit': 40}
    )

    assert len(result.structured_content['results']) == 3
    assert result.structured_content['more_beyond_depth'] is True
    assert 'as deep as this search ranks' in result.content[0].text


async def test_the_text_the_model_reads_is_citable(server, library):
    result = await call(server, library.owner, 'search_transcripts', {'query': 'spacecraft'})

    text = result.content[0].text
    assert f'(media {library.nasa}' in text
    assert 'uploaded 2024-03-04' in text
    assert '| Uploaded | Channel | Video | Time | Quote |' in text


async def test_citation_ids_carry_their_source_link(server, library):
    result = await call(
        server, library.owner, 'search_transcripts', {'query': 'spacecraft', 'channels': ['NASA']}
    )

    text = result.content[0].text
    hits = {hit['media_id']: hit for hit in result.structured_content['results']}
    nasa = hits[library.nasa]
    assert f'[B{nasa["block_id"]}](https://www.youtube.com/watch?v=nasa1&t=0)' in text
    # The app link stays available beside the source-linked citation.
    assert f'app: {nasa["app_url"]}' in text


async def test_the_answer_format_shows_a_real_evidence_row(server, library):
    result = await call(
        server, library.owner, 'search_transcripts', {'query': 'spacecraft', 'limit': 1}
    )

    best = result.structured_content['results'][0]
    text = result.content[0].text
    footer = text[text.index('Answer format for these passages:') :]
    assert f'[B{best["block_id"]}]({best["source_url"]})' in footer
    assert f'[{best["timestamp"]}]({best["source_url"]})' in footer
    if best['app_url']:
        assert f'[▶ app]({best["app_url"]})' in footer


async def test_no_answer_format_without_passages(server, library):
    result = await call(server, library.stranger, 'search_transcripts', {'query': 'spacecraft'})

    text = result.content[0].text
    assert 'No matching transcript passages.' in text
    assert 'Evidence table' not in text


async def test_hybrid_mode_is_reported_when_keywords_are_given(server, library):
    result = await call(
        server,
        library.owner,
        'search_transcripts',
        {'query': 'landing', 'keywords': 'lander'},
    )

    applied = result.structured_content['applied']
    assert applied['mode'] == 'hybrid'
    assert applied['keywords'] == 'lander'
    assert library.nasa in _media_ids(result)


async def test_keywords_without_a_query_are_a_full_text_search(server, library):
    result = await call(
        server, library.owner, 'search_transcripts', {'keywords': '"orbital insertion"'}
    )

    applied = result.structured_content['applied']
    assert applied['mode'] == 'keyword'
    assert applied['embedding_model'] is None
    assert _media_ids(result) == {library.nasa}
    assert 'Searched (keyword, full-text): keywords "orbital insertion"' in result.content[0].text


async def test_stop_words_alone_are_explained(server, library):
    result = await call(server, library.owner, 'search_transcripts', {'keywords': 'the and'})

    assert result.structured_content['results'] == []
    assert 'Every keyword is a stop word' in result.content[0].text


async def test_neither_query_nor_keywords_is_an_error(server, library):
    result = await call(server, library.owner, 'search_transcripts', {'keywords': '  '})

    assert result.is_error
    assert 'Give query, keywords, or both.' in result.content[0].text


async def test_media_ids_search_inside_a_shortlist(server, library):
    result = await call(
        server,
        library.owner,
        'search_transcripts',
        {'query': 'spacecraft', 'media_ids': [library.esa, library.shared]},
    )

    assert _media_ids(result) == {library.esa, library.shared}
    assert result.structured_content['applied']['media_ids'] == sorted(
        [library.esa, library.shared]
    )
    assert 'media: 2 ids' in result.content[0].text


async def test_media_ids_the_user_cannot_see_find_nothing(server, library):
    result = await call(
        server,
        library.stranger,
        'search_transcripts',
        {'query': 'spacecraft', 'media_ids': [library.nasa]},
    )

    assert result.structured_content['results'] == []
    assert 'media: 1 ids' in result.content[0].text


async def test_too_many_media_ids_is_an_error(server, library):
    result = await call(
        server,
        library.owner,
        'search_transcripts',
        {'query': 'spacecraft', 'media_ids': list(range(1, 102))},
    )

    assert result.is_error


async def test_an_empty_query_is_an_error(server, library):
    result = await call(server, library.owner, 'search_transcripts', {'query': '   '})

    assert result.is_error


# --- get_transcript ---


async def test_get_transcript_reads_a_window_with_timestamps(server, library):
    result = await call(
        server, library.owner, 'get_transcript', {'media_id': library.nasa, 'start_seconds': 10}
    )

    window = result.structured_content
    assert [s['start_seconds'] for s in window['segments']] == [10, 20]
    assert window['next_start_seconds'] is None
    assert window['app_url'] == f'{BASE}/?media={library.nasa}&t=10'
    first = window['segments'][0]
    assert first['app_url'] == f'{BASE}/?media={library.nasa}&t=10'
    # Each line cites itself with a link to its own second.
    assert (
        f'[B{first["block_id"]}](https://www.youtube.com/watch?v=nasa1&t=10) 0:10'
        in result.content[0].text
    )


async def test_get_transcript_pages_by_max_chars(server, clean_database, pgvector_schema):
    session = db.get_sync_session()
    try:
        owner = seed_user(session, 'owner')
        long_id = seed_media(
            session,
            owner_id=owner,
            url='https://www.youtube.com/watch?v=long',
            channel='Talks',
            title='Long Talk',
            released=datetime(2025, 1, 1, tzinfo=UTC),
            blocks=[f'part {n} ' + 'word ' * 80 for n in range(4)],
            grant_access_to=owner,
        )
    finally:
        session.close()

    first = await call(server, owner, 'get_transcript', {'media_id': long_id, 'max_chars': 500})
    second = await call(
        server,
        owner,
        'get_transcript',
        {'media_id': long_id, 'start_seconds': first.structured_content['next_start_seconds']},
    )

    assert len(first.structured_content['segments']) == 1
    assert first.structured_content['next_start_seconds'] == 10
    assert second.structured_content['segments'][0]['text'].startswith('part 1')


def _seed_long_talk(
    spans: list[tuple[float, float]] | None = None, blocks: list[str] | None = None
):
    session = db.get_sync_session()
    try:
        owner = seed_user(session, 'owner')
        blocks = blocks or [f'part {n} ' + 'word ' * 60 for n in range(len(spans or []))]
        media_id = seed_media(
            session,
            owner_id=owner,
            url='https://www.youtube.com/watch?v=long',
            channel='Talks',
            title='Long Talk',
            released=datetime(2025, 1, 1, tzinfo=UTC),
            blocks=blocks,
            spans=spans,
            grant_access_to=owner,
        )
        return owner, media_id
    finally:
        session.close()


def _block_ids(media_id: int) -> list[int]:
    session = db.get_sync_session()
    try:
        rows = session.execute(
            text(
                'SELECT id FROM transcript_blocks WHERE media_details_id = :m '
                'ORDER BY start_time, id'
            ),
            {'m': media_id},
        )
        return [row[0] for row in rows]
    finally:
        session.close()


async def _page_through(server, user_id: int, media_id: int, max_chars: int) -> list[int]:
    """Every block id `get_transcript` hands out when followed page by page to the end."""
    seen: list[int] = []
    cursor = None
    for _ in range(2 * len(_block_ids(media_id))):
        args = {'media_id': media_id, 'max_chars': max_chars}
        if cursor is not None:
            args['start_seconds'] = cursor
        window = (await call(server, user_id, 'get_transcript', args)).structured_content
        seen.extend(segment['block_id'] for segment in window['segments'])
        following = window['next_start_seconds']
        if following is None:
            return seen
        assert cursor is None or following > cursor
        cursor = following
    pytest.fail(f'paging never ended; read {seen}')


async def test_paging_over_overlapping_blocks_reads_each_once(
    server, clean_database, pgvector_schema
):
    # Stored blocks can overrun their successor; two of them exceed max_chars, so each
    # page holds one and a cursor that re-reads the previous block never advances.
    spans = [(0, 25), (20, 45), (40, 66), (60, 84), (80, 100)]
    owner, media_id = _seed_long_talk(spans)

    assert await _page_through(server, owner, media_id, max_chars=500) == _block_ids(media_id)


async def test_paging_over_aggregated_blocks_reads_each_once(
    server, clean_database, pgvector_schema
):
    owner, media_id = _seed_long_talk(blocks=[])
    cues = [(2.0 * i, 2.0 * i + 4.5, f'cue {i} ' + 'word ' * 10) for i in range(60)]
    session = db.get_sync_session()
    try:
        session.add_all(
            _aggregate_segments_into_blocks(cues, media_id, block_duration=20, transcript_model='t')
        )
        session.commit()
    finally:
        session.close()

    for max_chars in (500, 20_000):
        assert await _page_through(server, owner, media_id, max_chars) == _block_ids(media_id)


async def test_a_moment_inside_overlapping_blocks_opens_on_the_latest_block_covering_it(
    server, clean_database, pgvector_schema
):
    owner, media_id = _seed_long_talk([(0, 25), (20, 45), (40, 60)])

    inside_first = await call(
        server, owner, 'get_transcript', {'media_id': media_id, 'start_seconds': 10}
    )
    inside_both = await call(
        server, owner, 'get_transcript', {'media_id': media_id, 'start_seconds': 22}
    )

    assert [s['start_seconds'] for s in inside_first.structured_content['segments']] == [0, 20, 40]
    assert [s['start_seconds'] for s in inside_both.structured_content['segments']] == [20, 40]


async def test_a_moment_in_a_silence_gap_opens_on_the_next_block(
    server, clean_database, pgvector_schema
):
    owner, media_id = _seed_long_talk([(0, 10), (30, 40)])

    result = await call(
        server, owner, 'get_transcript', {'media_id': media_id, 'start_seconds': 15}
    )

    assert [s['start_seconds'] for s in result.structured_content['segments']] == [30]


async def test_get_transcript_from_a_start_reads_as_far_as_max_chars(
    server, clean_database, pgvector_schema
):
    owner, media_id = _seed_long_talk(blocks=[f'block {n}' for n in range(40)])

    result = await call(
        server, owner, 'get_transcript', {'media_id': media_id, 'start_seconds': 10}
    )

    window = result.structured_content
    assert window['segments'][-1]['start_seconds'] == 390
    assert window['next_start_seconds'] is None


async def test_missing_and_forbidden_media_look_the_same(server, library):
    forbidden = await call(server, library.stranger, 'get_transcript', {'media_id': library.nasa})
    missing = await call(server, library.stranger, 'get_transcript', {'media_id': 999_999})

    assert forbidden.is_error
    assert missing.is_error
    assert forbidden.content[0].text.replace(str(library.nasa), 'N') == missing.content[
        0
    ].text.replace('999999', 'N')


async def test_shared_media_transcript_is_readable(server, library):
    result = await call(server, library.owner, 'get_transcript', {'media_id': library.shared})

    assert not result.is_error
    assert result.structured_content['segments'][0]['text'] == 'a spacecraft hobby build'


# --- library_overview ---


async def test_overview_counts_what_the_user_can_see(server, library):
    result = await call(server, library.owner, 'library_overview')

    overview = result.structured_content
    assert overview['videos_with_transcripts'] == 4
    assert overview['channel_count'] == 3
    assert overview['first_upload'] == '2023-05-01'
    assert overview['last_upload'] == '2025-07-09'
    assert overview['channels'][0] == {
        'name': 'NASA Official',
        'videos': 2,
        'first_upload': '2023-05-01',
        'last_upload': '2024-03-04',
    }
    assert {'name': 'Space', 'videos': 1} in overview['tags']


async def test_overview_pages_through_the_channels(server, library, monkeypatch):
    monkeypatch.setattr(mcp_server_module, 'CHANNEL_PAGE', 2)

    first = await call(server, library.owner, 'library_overview')
    rest = await call(server, library.owner, 'library_overview', {'channel_offset': 2})

    assert [c['name'] for c in first.structured_content['channels']] == [
        'NASA Official',
        'European Space Agency',
    ]
    assert first.structured_content['next_channel_offset'] == 2
    assert 'Channels (videos, upload span), 1-2 of 3, largest first:' in first.content[0].text
    assert 'More channels: channel_offset=2.' in first.content[0].text
    assert [c['name'] for c in rest.structured_content['channels']] == ['Friend Channel']
    assert rest.structured_content['next_channel_offset'] is None


async def test_overview_of_an_empty_library(server, library):
    result = await call(server, library.stranger, 'library_overview')

    assert result.structured_content['videos_with_transcripts'] == 0
    assert result.structured_content['channels'] == []


# --- list_videos ---


def _listed(result) -> list[int]:
    return [video['media_id'] for video in result.structured_content['videos']]


async def test_list_videos_lists_what_the_user_can_see_newest_first(server, library):
    result = await call(server, library.owner, 'list_videos')

    assert _listed(result) == [library.esa, library.shared, library.nasa, library.deleted]
    assert result.structured_content['total'] == 4
    assert result.structured_content['next_offset'] is None
    # A deleted file can't play in the app, so only the source link is offered.
    assert result.structured_content['videos'][3]['app_url'] is None
    assert 'Videos 1-4 of 4, newest first:' in result.content[0].text


async def test_list_videos_oldest_first(server, library):
    result = await call(server, library.owner, 'list_videos', {'sort': 'oldest'})

    assert _listed(result) == [library.deleted, library.nasa, library.shared, library.esa]


async def test_list_videos_pages_with_next_offset(server, library):
    first = await call(server, library.owner, 'list_videos', {'limit': 3})
    rest = await call(server, library.owner, 'list_videos', {'limit': 3, 'offset': 3})

    assert _listed(first) == [library.esa, library.shared, library.nasa]
    assert first.structured_content['next_offset'] == 3
    assert 'Continues: offset=3 lists the next videos.' in first.content[0].text
    assert _listed(rest) == [library.deleted]
    assert rest.structured_content['next_offset'] is None


async def test_list_videos_filters_like_search(server, library):
    by_channel = await call(server, library.owner, 'list_videos', {'channels': ['nasa']})
    by_title = await call(server, library.owner, 'list_videos', {'title_contains': 'lander'})
    by_date = await call(server, library.owner, 'list_videos', {'released_from': '2025'})

    assert _listed(by_channel) == [library.nasa, library.deleted]
    assert by_channel.structured_content['applied']['channels'] == ['NASA Official']
    assert _listed(by_title) == [library.nasa]
    assert _listed(by_date) == [library.esa, library.shared]


async def test_list_videos_tag_filter_uses_the_callers_own_tags(server, library):
    tagged = await call(server, library.owner, 'list_videos', {'tags': ['Space']})
    friend = await call(server, library.friend, 'list_videos', {'tags': ['Space']})

    assert _listed(tagged) == [library.nasa]
    assert _listed(friend) == []
    assert friend.structured_content['applied']['unmatched_tags'] == ['Space']


async def test_list_videos_with_an_unmatched_channel_lists_nothing(server, library):
    result = await call(server, library.owner, 'list_videos', {'channels': ['Discovery']})

    assert _listed(result) == []
    assert result.structured_content['applied']['unmatched_channels'] == ['Discovery']
    assert 'No such channel: Discovery' in result.content[0].text


async def test_list_videos_reports_transcript_size_and_length(server, library):
    result = await call(server, library.owner, 'list_videos', {'title_contains': 'lander'})

    video = result.structured_content['videos'][0]
    assert video['transcript_blocks'] == 3
    assert video['transcript_chars'] == len(
        'the spacecraft lander touches downspacecraft orbital insertion burnand now the weather'
    )
    # No duration on the row, so the transcript's end stands in for it.
    assert video['duration_seconds'] == 30
    assert '— 0:30 — 3 blocks, 86 chars —' in result.content[0].text


# --- mention_timeline ---


def _rows(result) -> list[tuple]:
    return [
        (row['period'], row['channel'], row['blocks'], row['videos_with_mentions'], row['videos'])
        for row in result.structured_content['rows']
    ]


async def test_the_timeline_counts_every_mention_by_month(server, library):
    result = await call(server, library.owner, 'mention_timeline', {'terms': ['spacecraft']})

    assert _rows(result) == [
        ('2023-05', None, 1, 1, 1),
        ('2024-03', None, 2, 1, 1),
        ('2025-01', None, 1, 1, 1),
        ('2025-07', None, 1, 1, 1),
    ]
    timeline = result.structured_content
    assert (timeline['total_blocks'], timeline['total_videos_with_mentions']) == (5, 4)
    assert timeline['matched_query'] == "'spacecraft'"
    assert '5 matching blocks in 4 of 4 videos.' in result.content[0].text
    assert '2024-03 | 2 | 1 | 1' in result.content[0].text


async def test_a_timeline_phrase_does_not_match_its_words_reordered(server, library):
    phrase = await call(server, library.owner, 'mention_timeline', {'terms': ['orbital insertion']})
    reordered = await call(
        server, library.owner, 'mention_timeline', {'terms': ['insertion orbital']}
    )

    assert phrase.structured_content['total_blocks'] == 1
    assert reordered.structured_content['total_blocks'] == 0
    assert 'No passage mentions them in 4 videos.' in reordered.content[0].text


async def test_a_block_with_two_of_the_terms_counts_once(server, library):
    result = await call(
        server, library.owner, 'mention_timeline', {'terms': ['spacecraft', 'lander']}
    )

    assert result.structured_content['total_blocks'] == 5


async def test_the_timeline_by_year_and_by_channel(server, library):
    result = await call(
        server,
        library.owner,
        'mention_timeline',
        {'terms': ['spacecraft'], 'interval': 'year', 'by_channel': True},
    )
    lander = await call(
        server, library.owner, 'mention_timeline', {'terms': ['lander'], 'by_channel': True}
    )

    assert _rows(result) == [
        ('2023', 'NASA Official', 1, 1, 1),
        ('2024', 'NASA Official', 2, 1, 1),
        ('2025', 'European Space Agency', 1, 1, 1),
        ('2025', 'Friend Channel', 1, 1, 1),
    ]
    # By channel, only the rows with a mention are listed; the totals still count all.
    assert _rows(lander) == [('2024-03', 'NASA Official', 1, 1, 1)]
    assert lander.structured_content['total_videos'] == 4


async def test_undated_videos_come_last_and_a_date_range_drops_them(server, library):
    session = db.get_sync_session()
    try:
        seed_media(
            session,
            owner_id=library.owner,
            url='https://www.youtube.com/watch?v=undated',
            channel='NASA Official',
            title='Undated',
            released=None,
            blocks=['spacecraft of unknown date'],
            grant_access_to=library.owner,
        )
    finally:
        session.close()

    everything = await call(server, library.owner, 'mention_timeline', {'terms': ['spacecraft']})
    ranged = await call(
        server,
        library.owner,
        'mention_timeline',
        {'terms': ['spacecraft'], 'released_from': '2020'},
    )

    assert _rows(everything)[-1] == ('undated', None, 1, 1, 1)
    assert all(row[0] != 'undated' for row in _rows(ranged))
    assert 'excluded by a date range' in ranged.content[0].text


async def test_the_timeline_counts_only_what_the_user_can_see(server, library):
    result = await call(server, library.stranger, 'mention_timeline', {'terms': ['spacecraft']})

    assert result.structured_content['total_videos'] == 0
    assert result.structured_content['rows'] == []


async def test_a_timeline_of_stop_words_is_an_error(server, library):
    result = await call(server, library.owner, 'mention_timeline', {'terms': ['the', 'and']})

    assert result.is_error
    assert 'stop word' in result.content[0].text


async def test_a_timeline_with_an_unmatched_channel_counts_nothing(server, library):
    result = await call(
        server,
        library.owner,
        'mention_timeline',
        {'terms': ['spacecraft'], 'channels': ['Discovery']},
    )

    assert result.structured_content['total_blocks'] == 0
    assert 'Nothing was searched' in result.content[0].text


async def test_timeline_rows_that_do_not_fit_are_counted_not_dropped(server, library, monkeypatch):
    monkeypatch.setattr(formatting, 'RESPONSE_MAX_CHARS', 0)

    result = await call(server, library.owner, 'mention_timeline', {'terms': ['spacecraft']})

    timeline = result.structured_content
    assert timeline['rows'] == []
    assert timeline['rows_omitted'] == 4
    assert timeline['total_blocks'] == 5
    assert '4 more rows did not fit' in result.content[0].text


async def test_the_timeline_points_at_a_search_of_its_busiest_period(server, library):
    result = await call(server, library.owner, 'mention_timeline', {'terms': ['spacecraft']})

    assert (
        "search_transcripts(keywords='spacecraft', released_from='2024-03', released_to='2024-03')"
    ) in result.content[0].text


def test_the_hint_keywords_reproduce_the_counted_query():
    terms = ['carbon tax', 'emissions', "o'brien said"]

    assert transcript_service._build_fts_query(formatting.keywords_for(terms)) == (
        transcript_service.fts_any_of(terms)
    )


# --- server surface ---


async def test_tools_are_read_only_and_the_prompt_exists(server):
    with acting_as(McpPrincipal(user_id=1, username='u', token_id=1, base_url=BASE)):
        async with Client(server) as client:
            tools = (await client.list_tools()).tools
            prompt = await client.get_prompt('research')

    assert {t.name for t in tools} == {
        'library_overview',
        'list_videos',
        'search_transcripts',
        'mention_timeline',
        'get_transcript',
    }
    assert all(t.annotations.read_only_hint for t in tools)
    assert 'evidence table' in prompt.messages[0].content.text


def test_building_the_server_leaves_the_root_logger_alone():
    root = logging.getLogger()
    before = (list(root.handlers), root.level)

    build_mcp_server(embedder_factory=StubEmbedder)

    assert (list(root.handlers), root.level) == before
