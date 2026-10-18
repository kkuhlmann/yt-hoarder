"""The citation text MCP results teach the model to copy."""

from mcp_server.formatting import TranscriptHit, _answer_format, citation


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
