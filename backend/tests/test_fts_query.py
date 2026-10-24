"""Unit tests for _build_fts_query, the tsquery input builder used by hybrid search.

Pure unit tests — no database. The expected strings here were validated against
Postgres 17: every one of them parses under to_tsquery('english', ...).
"""

from services.transcript import _build_fts_query


class TestBuildFtsQuery:
    def test_plain_terms_are_or_joined(self):
        assert _build_fts_query('rocket launch') == "'rocket' | 'launch'"

    def test_punctuation_stays_inside_the_quoted_lexeme(self):
        # Unquoted, '!' is tsquery's NOT operator and "'" opens a lexeme, so a
        # query like this one aborts the whole statement with a syntax error.
        assert _build_fts_query("liftoff! we're on our way to the moon!") == (
            "'liftoff!' | 'we''re' | 'on' | 'our' | 'way' | 'to' | 'the' | 'moon!'"
        )

    def test_apostrophe_is_doubled(self):
        assert _build_fts_query("don't") == "'don''t'"

    def test_backslash_is_doubled(self):
        assert _build_fts_query(r'back\slash') == r"'back\\slash'"

    def test_trailing_backslash_cannot_escape_the_closing_quote(self):
        assert _build_fts_query('abc\\') == "'abc\\\\'"

    def test_lone_apostrophe_term(self):
        assert _build_fts_query("'") == "''''"

    def test_empty_query(self):
        assert _build_fts_query('') == ''

    def test_whitespace_only_query(self):
        assert _build_fts_query('   \t\n ') == ''

    def test_a_double_quoted_phrase_is_one_lexeme(self):
        # A multi-word quoted lexeme is to_tsquery's phrase: 'carbon' <-> 'tax'.
        assert _build_fts_query('"carbon tax" emissions') == "'carbon tax' | 'emissions'"

    def test_an_unpaired_quote_stays_a_plain_word(self):
        assert _build_fts_query('"carbon tax') == "'\"carbon' | 'tax'"

    def test_empty_quotes_are_dropped(self):
        assert _build_fts_query('"" launch " "') == "'launch'"
