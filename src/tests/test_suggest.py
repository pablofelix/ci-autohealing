"""Tests for fuzzy matching and suggestions."""

from cli.suggest import _edit_distance, format_suggestion, suggest_match


class TestEditDistance:
    def test_same_string(self):
        assert _edit_distance('abc', 'abc') == 0

    def test_one_char_diff(self):
        assert _edit_distance('abc', 'abx') == 1

    def test_insertion(self):
        assert _edit_distance('abc', 'abcd') == 1

    def test_deletion(self):
        assert _edit_distance('abcd', 'abc') == 1

    def test_empty(self):
        assert _edit_distance('', 'abc') == 3


class TestSuggestMatch:
    COMPONENTS = [
        'odh-operator-v3-5-ea-2',
        'odh-dashboard-v3-5-ea-2',
        'odh-notebook-controller-v3-5-ea-2',
        'rhoai-fbc-fragment-v3-5-ea-2',
    ]

    def test_substring_match(self):
        results = suggest_match('operator', self.COMPONENTS)
        assert len(results) >= 1
        assert results[0][0] == 'odh-operator-v3-5-ea-2'
        assert results[0][1] == 0

    def test_typo_match(self):
        results = suggest_match('odh-dashbord-v3-5-ea-2', self.COMPONENTS)
        assert len(results) >= 1
        assert 'dashboard' in results[0][0]

    def test_no_match(self):
        results = suggest_match('completely-different-thing', self.COMPONENTS, max_distance=3)
        assert len(results) == 0

    def test_max_suggestions(self):
        results = suggest_match('odh', self.COMPONENTS, max_suggestions=2)
        assert len(results) <= 2


class TestFormatSuggestion:
    def test_single_match(self):
        result = format_suggestion('operator', ['odh-operator-v3-5-ea-2'])
        assert "Did you mean" in result
        assert 'odh-operator-v3-5-ea-2' in result

    def test_no_match(self):
        result = format_suggestion('xyz', ['abc'], resource_type='app')
        assert result is None or "Did you mean" in result

    def test_empty_candidates(self):
        assert format_suggestion('test', []) is None
