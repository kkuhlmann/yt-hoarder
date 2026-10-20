"""The MCP tools, called through the SDK's in-memory client against real pgvector SQL.

The principal is set the way the HTTP endpoint sets it, around the client: the client's
server tasks are created inside that context, so the tools see it.
"""

import logging
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from mcp import Client

from database import db
from mcp_server.context import McpPrincipal, acting_as
from mcp_server.server import build_mcp_server
from models import MediaTag, Tag, TaskStatus
from services import transcript as transcript_service
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


async def test_overview_for_a_channel_lists_recent_videos(server, library):
    result = await call(server, library.owner, 'library_overview', {'channel': 'nasa official'})

    overview = result.structured_content
    assert overview['channel'] == 'NASA Official'
    assert [v['media_id'] for v in overview['recent_videos']] == [library.nasa, library.deleted]
    assert overview['recent_videos'][1]['app_url'] is None


async def test_overview_for_an_unknown_channel_is_an_error(server, library):
    result = await call(server, library.owner, 'library_overview', {'channel': 'Discovery'})

    assert result.is_error
    assert 'NASA Official' in result.content[0].text


async def test_overview_of_an_empty_library(server, library):
    result = await call(server, library.stranger, 'library_overview')

    assert result.structured_content['videos_with_transcripts'] == 0
    assert result.structured_content['channels'] == []


# --- server surface ---


async def test_tools_are_read_only_and_the_prompt_exists(server):
    with acting_as(McpPrincipal(user_id=1, username='u', token_id=1, base_url=BASE)):
        async with Client(server) as client:
            tools = (await client.list_tools()).tools
            prompt = await client.get_prompt('research')

    assert {t.name for t in tools} == {'library_overview', 'search_transcripts', 'get_transcript'}
    assert all(t.annotations.read_only_hint for t in tools)
    assert 'evidence table' in prompt.messages[0].content.text


def test_building_the_server_leaves_the_root_logger_alone():
    root = logging.getLogger()
    before = (list(root.handlers), root.level)

    build_mcp_server(embedder_factory=StubEmbedder)

    assert (list(root.handlers), root.level) == before
