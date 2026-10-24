"""The citation text MCP results teach the model to copy, and how results page."""

from mcp_server import formatting
from mcp_server.formatting import (
    AppliedSearch,
    TranscriptHit,
    _answer_format,
    _Span,
    build_search_result,
    citation,
)


def _hit(**overrides) -> TranscriptHit:
    fields = {
        'block_id': 69,
        'media_id': 1,
        'title': 'The Year of Pluto',
        'channel': 'NASA',
        'uploaded': '2015-06-12',
        'start_seconds': 1489,
        'end_seconds': 1509,
        'timestamp': '24:49',
        'text': 'velocity at launch was the fastest ever',
        'score': 0.42,
        'source_url': 'https://www.youtube.com/watch?v=abc&t=1489',
        'app_url': 'http://nas:8000/?media=1&t=1489',
    }
    fields.update(overrides)
    return TranscriptHit(**fields)


def test_citation_links_the_source_first():
    assert citation(69, 'https://y.t/w?v=a&t=5', 'http://nas/?media=1&t=5') == (
        '[B69](https://y.t/w?v=a&t=5)'
    )


def test_citation_falls_back_to_the_app_then_to_a_bare_id():
    assert citation(69, None, 'http://nas/?media=1&t=5') == '[B69](http://nas/?media=1&t=5)'
    assert citation(69, None, None) == '[B69]'


def test_citation_keeps_the_markdown_link_intact():
    # An unescaped ")" or space would end the link target early.
    assert citation(1, 'https://example.com/a (b)', None) == '[B1](https://example.com/a%20%28b%29)'


def test_answer_format_row_escapes_table_pipes():
    footer = _answer_format(_hit(title='Part 1 | Part 2', text='this | that'))

    assert 'Part 1 \\| Part 2' in footer
    assert '"this \\| that"' in footer


def test_answer_format_time_cell_links_both_places():
    footer = _answer_format(_hit())

    assert (
        '| [24:49](https://www.youtube.com/watch?v=abc&t=1489) · '
        '[▶ app](http://nas:8000/?media=1&t=1489) |'
    ) in footer


def test_answer_format_without_an_app_link_offers_only_the_source():
    footer = _answer_format(_hit(app_url=None))

    assert '▶ app' not in footer
    assert '[24:49](https://www.youtube.com/watch?v=abc&t=1489)' in footer


def test_answer_format_without_passages_only_points_at_get_transcript():
    assert _answer_format(None) == (
        'get_transcript(media_id, start_seconds) reads the surrounding transcript.'
    )


def _spans(count: int) -> list[_Span]:
    media = {
        'id': 1,
        'title': 'Talk',
        'channel': 'NASA',
        'url': 'https://www.youtube.com/watch?v=abc',
        'status': 'COMPLETE',
    }
    return [
        _Span(
            media=media,
            rank=n,
            start=n * 60.0,
            end=n * 60.0 + 20,
            block_ids=[n],
            texts=[f'passage {n} ' + 'word ' * 50],
            score=0.5,
        )
        for n in range(count)
    ]


def _applied() -> AppliedSearch:
    return AppliedSearch(
        query='q',
        keywords=None,
        mode='semantic',
        embedding_model='m',
        channels=None,
        tags=None,
        min_rating=None,
        released_from=None,
        released_to=None,
    )


def test_a_page_starts_at_its_offset_and_points_at_the_next():
    result, text = build_search_result(_applied(), _spans(5), 2, 'http://nas', offset=2)

    assert [hit.block_id for hit in result.results] == [2, 3]
    assert result.next_offset == 4
    assert 'Passages 3-4 of 5 found' in text
    assert 'Continues: offset=4' in text


def test_the_last_page_says_it_is_the_last():
    result, text = build_search_result(_applied(), _spans(5), 2, 'http://nas', offset=4)

    assert [hit.block_id for hit in result.results] == [4]
    assert result.next_offset is None
    assert 'That is every passage this search found.' in text


def test_next_offset_counts_only_the_passages_that_fit(monkeypatch):
    monkeypatch.setattr(formatting, 'RESPONSE_MAX_CHARS', 0)

    result, _ = build_search_result(_applied(), _spans(5), 4, 'http://nas', offset=1)

    # Even with no budget a page renders its first passage, so paging always advances.
    assert [hit.block_id for hit in result.results] == [1]
    assert result.next_offset == 2


def test_a_search_that_filled_its_depth_says_more_may_rank_below():
    result, text = build_search_result(
        _applied(), _spans(3), 5, 'http://nas', more_beyond_depth=True
    )

    assert result.next_offset is None
    assert result.truncated
    assert 'of 3+ found' in text
    assert 'as deep as this search ranks' in text
