"""Payload shape contract for transcript-search hits.

Three query builders feed one assembler, so a column present in `_row_to_dict`
but missing from a SELECT raises AttributeError only for whichever search mode
the user happens to land on. These tests pin both halves together.
"""

import inspect
from datetime import datetime
from types import SimpleNamespace

from services.transcript import (
    _hybrid_rrf_search,
    _keyword_search,
    _row_to_dict,
    _semantic_search,
)

EXPECTED_MEDIA_DETAILS_KEYS = {
    'id',
    'url',
    'title',
    'channel',
    'media_type',
    'status',
    'duration',
    'thumbnail_path',
    'release_timestamp',
}


def _fake_row(**overrides) -> SimpleNamespace:
    row = {
        'transcript_block_id': 7,
        'text': 'hello there',
        'score': 0.83,
        'fts_rank': None,
        'start_time': 12.5,
        'end_time': 18.0,
        'md_id': 3,
        'url': 'https://youtube.com/watch?v=abc',
        'title': 'A Video',
        'channel': 'Chan',
        'media_type': 'VIDEO',
        'status': 'COMPLETE',
        'duration': 612.34,
        'thumbnail_path': '/mnt/video/abc.jpg',
        'release_timestamp': datetime(2025, 3, 14),
    }
    row.update(overrides)
    return SimpleNamespace(**row)


def test_hit_carries_every_media_field_the_player_surfaces_need():
    result = _row_to_dict(_fake_row())

    assert set(result['media_details']) == EXPECTED_MEDIA_DETAILS_KEYS
    # A hit opens the video and the clip editor keys its range and zoom off
    # duration; without it the whole editor degenerates to a zero-length range.
    assert result['media_details']['duration'] == 612.34
    assert result['media_details']['thumbnail_path'] == '/mnt/video/abc.jpg'
    # MCP results cite each passage with its upload date.
    assert result['media_details']['release_timestamp'] == datetime(2025, 3, 14)


def test_null_duration_passes_through_rather_than_becoming_a_number():
    result = _row_to_dict(_fake_row(duration=None, thumbnail_path=None))

    assert result['media_details']['duration'] is None
    assert result['media_details']['thumbnail_path'] is None


def test_every_query_builder_projects_what_the_assembler_reads():
    for builder in (_semantic_search, _keyword_search, _hybrid_rrf_search):
        source = inspect.getsource(builder)
        for column in ('md.duration', 'md.thumbnail_path', 'md.release_timestamp'):
            assert column in source, f'{builder.__name__} does not project {column}'


def test_semantic_search_projects_the_media_fields_on_both_paths():
    """_semantic_search carries two SELECTs — the exact scoped one and the index one.

    A column added to only one of them raises AttributeError for whichever scope size
    the user happens to land on, which is the same trap the test above guards.
    """
    source = inspect.getsource(_semantic_search)

    for column in ('md.duration', 'md.thumbnail_path', 'md.release_timestamp'):
        assert source.count(column) == 2, f'_semantic_search projects {column} on only one path'
