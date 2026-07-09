"""Comprehensive tests for conforma_reporter_client module-level functions."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import base64
from unittest.mock import MagicMock, mock_open, patch

from clients.conforma_reporter_client import (
    CACHE_DIR,
    CACHE_TTL,
    _cache_key,
    _fetch_file,
    _group_by_component,
    _group_by_rule,
    _infer_scenario,
    _parse_csv,
    _read_cache,
    _report_path,
    _warnings_path,
    _write_cache,
    fetch_reporter_rules,
    fetch_reporter_violations,
    fetch_reporter_warnings,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_CSV = (
    "component_name,image,code,title,solution,type\n"
    "comp-a,registry/comp-a:v1,rule.1,Rule One,Fix it,violation\n"
    "comp-a,registry/comp-a:v2,rule.2,Rule Two,Patch it,violation\n"
    "comp-b,registry/comp-b:v1,rule.1,Rule One,Fix it,violation\n"
)

SAMPLE_CSV_WARNINGS = (
    "component_name,image,code,title,solution,type\n"
    "comp-a,registry/comp-a:v1,warn.1,Warn One,Check it,warning\n"
)


def _b64(text):
    """Base64 encode a string the way GitHub API returns it."""
    return base64.b64encode(text.encode()).decode()


def _mock_response(status_code=200, json_data=None, raise_for_status=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    if raise_for_status:
        resp.raise_for_status.side_effect = raise_for_status
    else:
        resp.raise_for_status.return_value = None
    return resp


# ═══════════════════════════════════════════════════════════════════════
# _report_path
# ═══════════════════════════════════════════════════════════════════════

class TestReportPath:
    def test_stage_latest(self):
        assert _report_path('stage', 'latest') == (
            'stage/future/build_type_latest/conforma-violations-report.csv'
        )

    def test_prod_nightly(self):
        assert _report_path('prod', 'nightly') == (
            'prod/future/build_type_nightly/conforma-violations-report.csv'
        )

    def test_release_day_skips_future(self):
        assert _report_path('prod', 'release_day') == (
            'prod/release_day/conforma-violations-report.csv'
        )

    def test_stage_release_day(self):
        assert _report_path('stage', 'release_day') == (
            'stage/release_day/conforma-violations-report.csv'
        )


# ═══════════════════════════════════════════════════════════════════════
# _warnings_path
# ═══════════════════════════════════════════════════════════════════════

class TestWarningsPath:
    def test_stage_latest(self):
        assert _warnings_path('stage', 'latest') == (
            'stage/future/build_type_latest/conforma-warnings-report.csv'
        )

    def test_prod_nightly(self):
        assert _warnings_path('prod', 'nightly') == (
            'prod/future/build_type_nightly/conforma-warnings-report.csv'
        )

    def test_release_day(self):
        assert _warnings_path('prod', 'release_day') == (
            'prod/release_day/conforma-warnings-report.csv'
        )


# ═══════════════════════════════════════════════════════════════════════
# _cache_key
# ═══════════════════════════════════════════════════════════════════════

class TestCacheKey:
    def test_path_sanitization(self):
        result = _cache_key('rhoai-3.5', 'stage/future/build_type_latest/report.csv')
        expected = os.path.join(CACHE_DIR, 'rhoai-3.5', 'stage_future_build_type_latest_report_csv')
        assert result == expected

    def test_simple_path(self):
        result = _cache_key('main', 'file.csv')
        expected = os.path.join(CACHE_DIR, 'main', 'file_csv')
        assert result == expected


# ═══════════════════════════════════════════════════════════════════════
# _read_cache
# ═══════════════════════════════════════════════════════════════════════

class TestReadCache:
    @patch('clients.conforma_reporter_client.os.path.exists', return_value=False)
    def test_returns_none_when_file_missing(self, _mock_exists):
        assert _read_cache('/tmp/nonexistent') is None

    @patch('clients.conforma_reporter_client.os.path.getmtime', return_value=1000.0)
    @patch('clients.conforma_reporter_client.time.time', return_value=1000.0 + CACHE_TTL + 1)
    @patch('clients.conforma_reporter_client.os.path.exists', return_value=True)
    def test_returns_none_when_expired(self, _exists, _time, _mtime):
        assert _read_cache('/tmp/old_cache') is None

    @patch('builtins.open', mock_open(read_data='{"key": "value"}'))
    @patch('clients.conforma_reporter_client.os.path.getmtime', return_value=5000.0)
    @patch('clients.conforma_reporter_client.time.time', return_value=5000.0 + 100)
    @patch('clients.conforma_reporter_client.os.path.exists', return_value=True)
    def test_returns_data_when_valid(self, _exists, _time, _mtime):
        result = _read_cache('/tmp/valid_cache')
        assert result == {'key': 'value'}

    @patch('clients.conforma_reporter_client.os.path.exists', side_effect=OSError('disk fail'))
    def test_returns_none_on_exception(self, _exists):
        assert _read_cache('/tmp/broken') is None


# ═══════════════════════════════════════════════════════════════════════
# _write_cache
# ═══════════════════════════════════════════════════════════════════════

class TestWriteCache:
    @patch('builtins.open', mock_open())
    @patch('clients.conforma_reporter_client.os.makedirs')
    def test_writes_json(self, mock_mkdirs):
        _write_cache('/tmp/cache/branch/key', {'hello': 'world'})
        mock_mkdirs.assert_called_once_with('/tmp/cache/branch', exist_ok=True)
        handle = open  # noqa: F841 — the mock_open handle
        # Verify open was called with write mode
        open.assert_called_once_with('/tmp/cache/branch/key', 'w')

    @patch('clients.conforma_reporter_client.os.makedirs', side_effect=OSError('perm denied'))
    def test_silently_passes_on_exception(self, _mkdirs):
        # Should not raise
        _write_cache('/tmp/cache/branch/key', {'data': 1})


# ═══════════════════════════════════════════════════════════════════════
# _fetch_file
# ═══════════════════════════════════════════════════════════════════════

class TestFetchFile:
    @patch('clients.conforma_reporter_client.requests.get')
    def test_success_decodes_base64(self, mock_get):
        content = 'Hello CSV content'
        mock_get.return_value = _mock_response(
            json_data={'content': _b64(content)}
        )
        result = _fetch_file('rhoai-3.5', 'stage/report.csv')
        assert result == content
        mock_get.assert_called_once()
        call_url = mock_get.call_args[0][0]
        assert 'rhoai-3.5' in call_url
        assert 'stage/report.csv' in call_url

    @patch('clients.conforma_reporter_client.requests.get')
    def test_404_returns_none(self, mock_get):
        mock_get.return_value = _mock_response(status_code=404)
        # 404 is checked before raise_for_status, so no exception
        mock_get.return_value.raise_for_status.side_effect = Exception('404')
        result = _fetch_file('main', 'missing.csv')
        assert result is None

    @patch('clients.conforma_reporter_client.requests.get')
    def test_http_error_returns_none(self, mock_get):
        mock_get.return_value = _mock_response(status_code=500)
        mock_get.return_value.raise_for_status.side_effect = Exception('Server error')
        result = _fetch_file('main', 'broken.csv')
        assert result is None

    @patch('clients.conforma_reporter_client.requests.get', side_effect=Exception('network'))
    def test_network_exception_returns_none(self, _mock_get):
        result = _fetch_file('main', 'file.csv')
        assert result is None

    @patch('clients.conforma_reporter_client.requests.get')
    def test_token_sets_auth_header(self, mock_get):
        mock_get.return_value = _mock_response(json_data={'content': _b64('x')})
        _fetch_file('main', 'file.csv', token='ghp_abc123')
        headers = mock_get.call_args[1]['headers']
        assert headers['Authorization'] == 'token ghp_abc123'

    @patch('clients.conforma_reporter_client.requests.get')
    def test_no_token_omits_auth_header(self, mock_get):
        mock_get.return_value = _mock_response(json_data={'content': _b64('x')})
        _fetch_file('main', 'file.csv', token=None)
        headers = mock_get.call_args[1]['headers']
        assert 'Authorization' not in headers


# ═══════════════════════════════════════════════════════════════════════
# _parse_csv
# ═══════════════════════════════════════════════════════════════════════

class TestParseCsv:
    def test_valid_csv(self):
        rows = _parse_csv(SAMPLE_CSV)
        assert len(rows) == 3
        assert rows[0]['component_name'] == 'comp-a'
        assert rows[0]['code'] == 'rule.1'
        assert rows[2]['component_name'] == 'comp-b'

    def test_empty_csv_headers_only(self):
        rows = _parse_csv("component_name,image,code\n")
        assert rows == []

    def test_single_row(self):
        csv_content = "name,value\nalpha,1\n"
        rows = _parse_csv(csv_content)
        assert len(rows) == 1
        assert rows[0] == {'name': 'alpha', 'value': '1'}


# ═══════════════════════════════════════════════════════════════════════
# _infer_scenario
# ═══════════════════════════════════════════════════════════════════════

class TestInferScenario:
    def test_fbc_component(self):
        result = _infer_scenario('fbc-rhoai-fragment', 'prod')
        assert result == 'conforma-fbc-rhoai-prod-single-component'

    def test_fbc_case_insensitive(self):
        result = _infer_scenario('FBC-something', 'stage')
        assert result == 'conforma-fbc-rhoai-stage-single-component'

    def test_chart_component(self):
        result = _infer_scenario('my-chart-component', 'prod')
        assert result == 'conforma-registry-rhoai-chart-prod-single-component'

    def test_regular_component(self):
        result = _infer_scenario('odh-dashboard', 'stage')
        assert result == 'conforma-registry-rhoai-stage-single-component'

    def test_regular_component_prod(self):
        result = _infer_scenario('kserve-controller', 'prod')
        assert result == 'conforma-registry-rhoai-prod-single-component'


# ═══════════════════════════════════════════════════════════════════════
# _group_by_component
# ═══════════════════════════════════════════════════════════════════════

class TestGroupByComponent:
    def test_multiple_rows_same_component(self):
        rows = _parse_csv(SAMPLE_CSV)
        result = _group_by_component(rows, env='prod')
        comp_a = [r for r in result if r['component_name'] == 'comp-a'][0]
        assert comp_a['violations_count'] == 2
        assert comp_a['unique_violations'] == 2
        assert comp_a['image_count'] == 2

    def test_different_components(self):
        rows = _parse_csv(SAMPLE_CSV)
        result = _group_by_component(rows, env='prod')
        assert len(result) == 2
        names = [r['component_name'] for r in result]
        assert 'comp-a' in names
        assert 'comp-b' in names

    def test_empty_rows(self):
        result = _group_by_component([], env='prod')
        assert result == []

    def test_rows_missing_component_name_skipped(self):
        rows = [
            {'component_name': '', 'image': 'img', 'code': 'r1', 'type': 'violation'},
            {'component_name': 'valid', 'image': 'img', 'code': 'r1', 'type': 'violation'},
        ]
        result = _group_by_component(rows, env='prod')
        assert len(result) == 1
        assert result[0]['component_name'] == 'valid'

    def test_warning_type_increments_warnings_count(self):
        rows = [
            {'component_name': 'comp-x', 'image': 'img', 'code': 'w1', 'type': 'warning'},
            {'component_name': 'comp-x', 'image': 'img', 'code': 'v1', 'type': 'violation'},
        ]
        result = _group_by_component(rows, env='stage')
        assert len(result) == 1
        assert result[0]['warnings_count'] == 1
        assert result[0]['violations_count'] == 1

    def test_violation_summary_format(self):
        rows = [
            {'component_name': 'comp', 'image': 'i', 'code': 'b_rule', 'type': 'violation'},
            {'component_name': 'comp', 'image': 'i', 'code': 'a_rule', 'type': 'violation'},
        ]
        result = _group_by_component(rows, env='prod')
        summary = result[0]['violation_summary']
        # Rules are sorted alphabetically
        assert summary.startswith('✕ [Violation] a_rule')
        assert 'b_rule' in summary

    def test_scenario_inferred_per_component(self):
        rows = [
            {'component_name': 'fbc-thing', 'image': 'i', 'code': 'r1', 'type': 'violation'},
            {'component_name': 'normal-comp', 'image': 'i', 'code': 'r1', 'type': 'violation'},
        ]
        result = _group_by_component(rows, env='prod')
        fbc = [r for r in result if r['component_name'] == 'fbc-thing'][0]
        normal = [r for r in result if r['component_name'] == 'normal-comp'][0]
        assert 'fbc-rhoai-prod' in fbc['scenario']
        assert 'registry-rhoai-prod' in normal['scenario']


# ═══════════════════════════════════════════════════════════════════════
# _group_by_rule
# ═══════════════════════════════════════════════════════════════════════

class TestGroupByRule:
    def test_groups_by_rule_code(self):
        rows = _parse_csv(SAMPLE_CSV)
        result = _group_by_rule(rows)
        rule_codes = [r['rule'] for r in result]
        assert 'rule.1' in rule_codes
        assert 'rule.2' in rule_codes

    def test_counts_components_per_rule(self):
        rows = _parse_csv(SAMPLE_CSV)
        result = _group_by_rule(rows)
        rule1 = [r for r in result if r['rule'] == 'rule.1'][0]
        # rule.1 appears for comp-a and comp-b
        assert rule1['count'] == 2
        assert sorted(rule1['components']) == ['comp-a', 'comp-b']

    def test_sorted_by_count_desc(self):
        rows = _parse_csv(SAMPLE_CSV)
        result = _group_by_rule(rows)
        # rule.1 has 2 components, rule.2 has 1 component
        assert result[0]['rule'] == 'rule.1'
        assert result[1]['rule'] == 'rule.2'

    def test_empty_code_rows_skipped(self):
        rows = [
            {'code': '', 'component_name': 'comp', 'title': '', 'solution': ''},
            {'code': 'valid', 'component_name': 'comp', 'title': 'T', 'solution': 'S'},
        ]
        result = _group_by_rule(rows)
        assert len(result) == 1
        assert result[0]['rule'] == 'valid'

    def test_violation_rows_counted(self):
        rows = [
            {'code': 'r1', 'component_name': 'a', 'title': '', 'solution': ''},
            {'code': 'r1', 'component_name': 'a', 'title': '', 'solution': ''},
            {'code': 'r1', 'component_name': 'b', 'title': '', 'solution': ''},
        ]
        result = _group_by_rule(rows)
        assert result[0]['violation_rows'] == 3
        assert result[0]['count'] == 2  # 2 unique components


# ═══════════════════════════════════════════════════════════════════════
# fetch_reporter_violations
# ═══════════════════════════════════════════════════════════════════════

class TestFetchReporterViolations:
    @patch('clients.conforma_reporter_client._write_cache')
    @patch('clients.conforma_reporter_client._fetch_file', return_value=SAMPLE_CSV)
    @patch('clients.conforma_reporter_client._read_cache', return_value=None)
    @patch('clients.conforma_reporter_client.os.environ', {'GITHUB_TOKEN': 'tok'})
    def test_cache_miss_fetches_and_caches(self, mock_read, mock_fetch, mock_write):
        result = fetch_reporter_violations('rhoai-3.5', env='prod', build_type='latest')
        assert len(result) == 2  # comp-a and comp-b
        mock_fetch.assert_called_once()
        mock_write.assert_called_once()

    @patch('clients.conforma_reporter_client._read_cache', return_value=[{'cached': True}])
    def test_cache_hit_returns_cached(self, mock_read):
        result = fetch_reporter_violations('rhoai-3.5', env='stage', build_type='latest')
        assert result == [{'cached': True}]

    @patch('clients.conforma_reporter_client._fetch_file', return_value=None)
    @patch('clients.conforma_reporter_client._read_cache', return_value=None)
    @patch('clients.conforma_reporter_client.os.environ', {'GITHUB_TOKEN': ''})
    def test_fetch_returns_none_gives_empty_list(self, mock_read, mock_fetch):
        result = fetch_reporter_violations('rhoai-3.5', env='prod', build_type='latest')
        assert result == []


# ═══════════════════════════════════════════════════════════════════════
# fetch_reporter_rules
# ═══════════════════════════════════════════════════════════════════════

class TestFetchReporterRules:
    @patch('clients.conforma_reporter_client._write_cache')
    @patch('clients.conforma_reporter_client._fetch_file', return_value=SAMPLE_CSV)
    @patch('clients.conforma_reporter_client._read_cache', return_value=None)
    @patch('clients.conforma_reporter_client.os.environ', {'GITHUB_TOKEN': 'tok'})
    def test_cache_miss_fetches_and_groups_by_rule(self, mock_read, mock_fetch, mock_write):
        result = fetch_reporter_rules('rhoai-3.5', env='stage', build_type='latest')
        assert len(result) == 2
        rule_codes = [r['rule'] for r in result]
        assert 'rule.1' in rule_codes
        mock_write.assert_called_once()

    @patch('clients.conforma_reporter_client._read_cache', return_value=[{'rule': 'cached'}])
    def test_cache_hit_returns_cached(self, mock_read):
        result = fetch_reporter_rules('rhoai-3.5')
        assert result == [{'rule': 'cached'}]

    @patch('clients.conforma_reporter_client._fetch_file', return_value=None)
    @patch('clients.conforma_reporter_client._read_cache', return_value=None)
    @patch('clients.conforma_reporter_client.os.environ', {'GITHUB_TOKEN': ''})
    def test_fetch_returns_none_gives_empty_list(self, mock_read, mock_fetch):
        result = fetch_reporter_rules('rhoai-3.5')
        assert result == []


# ═══════════════════════════════════════════════════════════════════════
# fetch_reporter_warnings
# ═══════════════════════════════════════════════════════════════════════

class TestFetchReporterWarnings:
    @patch('clients.conforma_reporter_client._write_cache')
    @patch('clients.conforma_reporter_client._fetch_file', return_value=SAMPLE_CSV_WARNINGS)
    @patch('clients.conforma_reporter_client._read_cache', return_value=None)
    @patch('clients.conforma_reporter_client.os.environ', {'GITHUB_TOKEN': 'tok'})
    def test_cache_miss_fetches_warnings(self, mock_read, mock_fetch, mock_write):
        result = fetch_reporter_warnings('rhoai-3.5', env='stage', build_type='latest')
        assert len(result) == 1
        assert result[0]['component_name'] == 'comp-a'
        mock_write.assert_called_once()

    @patch('clients.conforma_reporter_client._read_cache', return_value=[{'w': True}])
    def test_cache_hit(self, mock_read):
        result = fetch_reporter_warnings('rhoai-3.5')
        assert result == [{'w': True}]

    @patch('clients.conforma_reporter_client._fetch_file', return_value=None)
    @patch('clients.conforma_reporter_client._read_cache', return_value=None)
    @patch('clients.conforma_reporter_client.os.environ', {'GITHUB_TOKEN': ''})
    def test_fetch_returns_none_gives_empty_list(self, mock_read, mock_fetch):
        result = fetch_reporter_warnings('rhoai-3.5')
        assert result == []
