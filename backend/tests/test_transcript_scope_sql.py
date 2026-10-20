"""The compiled media-scope subquery the transcript search splices into raw SQL.

`build_media_scope_subquery` bridges two dialects: SQLAlchemy conditions on one side,
hand-written `text()` on the other. These pin the properties that make the splice safe,
none of which the type system can express.
"""

import inspect
from datetime import datetime

from repositories.media_details import build_media_scope_subquery
from services.transcript import (
    _hybrid_rrf_search,
    _MediaScope,
    _scoped_vector_cte,
    _semantic_search,
)

# Every name the three search builders bind by hand. A collision would have the
# scope silently overwrite one of them.
RESERVED_PARAM_NAMES = {
    'query',
    'embedding',
    'limit',
    'search',
    'user_id',
    'fts_query',
    'semantic_weight',
    'keyword_weight',
    'candidate_limit',
}


def test_no_filters_yields_no_subquery():
    assert build_media_scope_subquery(rating_user_id=1) is None


def test_param_names_never_collide_with_the_hand_bound_ones():
    _sql, params = build_media_scope_subquery(
        user_id=3,
        rating_user_id=3,
        status='COMPLETE',
        search='lofi && mix || cats',
        tag_ids=[1, 2],
        min_rating=4,
        channel='NASA',
        date_field='released',
        date_year=2024,
        date_month=3,
        include_owned=True,
        channels=['NASA', 'ESA'],
        released_from=datetime(2024, 1, 1),
        released_to=datetime(2025, 1, 1),
    )

    assert set(params).isdisjoint(RESERVED_PARAM_NAMES)


def test_expanding_bindparam_is_rendered():
    sql, params = build_media_scope_subquery(rating_user_id=1, tag_ids=[7, 8])

    # Without render_postcompile this is a [POSTCOMPILE_tag_id_1] token no driver binds.
    assert 'POSTCOMPILE' not in sql
    assert sorted(v for k, v in params.items() if k.startswith('tag_id')) == [7, 8]


def test_channels_expand_and_are_bound_never_inlined():
    sql, params = build_media_scope_subquery(rating_user_id=1, channels=['NASA', "O'Brien"])

    assert 'POSTCOMPILE' not in sql
    assert "O'Brien" not in sql
    assert {'NASA', "O'Brien"} <= set(params.values())


def test_title_text_is_bound_never_inlined():
    sql, params = build_media_scope_subquery(rating_user_id=1, title_contains="x'; DROP TABLE y;--")

    assert 'DROP TABLE' not in sql
    assert any('DROP TABLE' in str(v) for v in params.values())
    assert 'media_details.channel' not in sql.split('WHERE', 1)[1]


def test_an_empty_channel_list_narrows_to_nothing_rather_than_nothing_filtered():
    # A caller whose channel names all failed to resolve must not get the whole library.
    assert build_media_scope_subquery(rating_user_id=1, channels=[]) is not None


def test_search_text_is_bound_never_inlined():
    sql, params = build_media_scope_subquery(rating_user_id=1, search="O'Brien %_")

    assert "O'Brien" not in sql
    assert any(v == "%O'Brien %_%" for v in params.values())


def test_include_owned_widens_access_to_owned_media():
    without, _ = build_media_scope_subquery(user_id=5, rating_user_id=5)
    with_owned, _ = build_media_scope_subquery(user_id=5, rating_user_id=5, include_owned=True)

    # The listed row's own owner_id. The supersede condition reads the full-media row's
    # owner too, but through an alias (media_details_1), which this deliberately skips.
    assert 'media_details.owner_id' not in without
    # A soft delete drops every media_access row, the owner's included, so the
    # access subquery alone would hide an owner's own kept transcripts.
    assert 'media_details.owner_id' in with_owned


def test_scoped_vector_cte_is_materialized():
    """AS MATERIALIZED is the fix, not an optimization.

    Without it Postgres flattens the CTE into the outer ORDER BY and can answer from
    the HNSW index, which post-filters: it returns far fewer rows than the LIMIT asks
    for whenever the scope is narrow, and reports success. No dataset a test can seed
    is large enough to make the planner choose that path, so the keyword is pinned
    here rather than by a query.
    """
    scope = _MediaScope(subquery='SELECT 1', params={}, exact=True, empty=False)

    cte = _scoped_vector_cte(scope, 'query')

    assert 'AS MATERIALIZED' in cte
    # Keyed on the block so the scope applies before the media_details join.
    assert 'tb.media_details_id IN' in cte


def test_both_vector_builders_have_an_exact_path():
    for builder in (_semantic_search, _hybrid_rrf_search):
        source = inspect.getsource(builder)
        assert 'scope.exact' in source, f'{builder.__name__} does not branch on scope.exact'
        assert '_scoped_vector_cte' in source, f'{builder.__name__} does not build the exact path'
