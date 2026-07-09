"""Comprehensive tests for TektonResultsClient and _derive_tekton_results_url."""

import base64
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from unittest.mock import MagicMock, patch

import pytest
import requests

from clients.tekton_results import TektonResultsClient, _derive_tekton_results_url

# ---------------------------------------------------------------------------
# Helper: create a TektonResultsClient with all external deps mocked
# ---------------------------------------------------------------------------

def _make_client(**kwargs):
    with patch('clients.tekton_results.discover_openshift_api_url', return_value='https://api.test.example.com:6443'), \
         patch('clients.tekton_results.get_openshift_token', return_value='test-token'), \
         patch('clients.tekton_results.create_authenticated_session') as mock_session:
        mock_session.return_value = MagicMock()
        c = TektonResultsClient(namespace='test-ns', api_url='https://tr.example.com/apis/results.tekton.dev/v1alpha2', **kwargs)
        return c


def _encode_record(data_dict):
    """Encode a dict into the base64 record format used by Tekton Results."""
    raw = json.dumps(data_dict).encode('utf-8')
    return base64.b64encode(raw).decode('utf-8')


# ===========================================================================
# _derive_tekton_results_url
# ===========================================================================

class TestDeriveUrl:

    def test_valid_api_domain(self):
        url = _derive_tekton_results_url('https://api.cluster.example.com:6443')
        assert url == (
            'https://tekton-results-tekton-results.apps.cluster.example.com'
            '/apis/results.tekton.dev/v1alpha2'
        )

    def test_valid_api_domain_no_port(self):
        url = _derive_tekton_results_url('https://api.my.domain.org')
        assert url == (
            'https://tekton-results-tekton-results.apps.my.domain.org'
            '/apis/results.tekton.dev/v1alpha2'
        )

    def test_invalid_hostname_no_api_prefix(self):
        with pytest.raises(ValueError, match="Cannot derive"):
            _derive_tekton_results_url('https://cluster.example.com:6443')

    def test_invalid_hostname_empty(self):
        with pytest.raises(ValueError, match="Cannot derive"):
            _derive_tekton_results_url('not-a-url')


# ===========================================================================
# TektonResultsClient.__init__
# ===========================================================================

class TestConstructor:

    def test_explicit_api_url(self):
        client = _make_client()
        assert client.api_url == 'https://tr.example.com/apis/results.tekton.dev/v1alpha2'
        assert client.namespace == 'test-ns'

    def test_auto_derive_api_url(self):
        with patch('clients.tekton_results.discover_openshift_api_url', return_value='https://api.auto.example.com:6443'), \
             patch('clients.tekton_results.get_openshift_token', return_value='tok'), \
             patch('clients.tekton_results.create_authenticated_session') as mock_sess:
            mock_sess.return_value = MagicMock()
            client = TektonResultsClient(namespace='ns1')
        assert 'apps.auto.example.com' in client.api_url

    def test_token_failure_raises(self):
        with patch('clients.tekton_results.discover_openshift_api_url', return_value='https://api.x.com:6443'), \
             patch('clients.tekton_results.get_openshift_token', return_value=None), \
             patch('clients.tekton_results.create_authenticated_session'):
            with pytest.raises(RuntimeError, match="Failed to get OpenShift token"):
                TektonResultsClient(namespace='ns1', api_url='https://tr.example.com/apis/results.tekton.dev/v1alpha2')

    def test_session_verify_disabled(self):
        client = _make_client()
        assert client.session.verify is False

    def test_none_namespace(self):
        client = _make_client()
        # namespace can be None by default; ensure constructor doesn't crash
        with patch('clients.tekton_results.discover_openshift_api_url', return_value='https://api.x.com:6443'), \
             patch('clients.tekton_results.get_openshift_token', return_value='t'), \
             patch('clients.tekton_results.create_authenticated_session') as ms:
            ms.return_value = MagicMock()
            c = TektonResultsClient(api_url='https://tr.example.com/apis/results.tekton.dev/v1alpha2')
        assert c.namespace is None


# ===========================================================================
# _query_records
# ===========================================================================

class TestQueryRecords:

    def test_200_with_records(self):
        client = _make_client()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {'records': [{'name': 'r1'}, {'name': 'r2'}]}
        client.session.get.return_value = mock_resp

        result = client._query_records('some_filter')
        assert result == [{'name': 'r1'}, {'name': 'r2'}]

    def test_200_empty_records(self):
        client = _make_client()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {}
        client.session.get.return_value = mock_resp

        result = client._query_records('f')
        assert result == []

    def test_non_200_returns_empty(self):
        client = _make_client()
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        client.session.get.return_value = mock_resp

        result = client._query_records('f')
        assert result == []

    def test_request_exception_returns_empty(self):
        client = _make_client()
        client.session.get.side_effect = requests.RequestException("timeout")

        result = client._query_records('f')
        assert result == []

    def test_page_size_clamped_low(self):
        client = _make_client()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {'records': []}
        client.session.get.return_value = mock_resp

        client._query_records('f', page_size=1)
        call_args = client.session.get.call_args
        assert call_args[1]['params']['page_size'] == 5

    def test_page_size_clamped_high(self):
        client = _make_client()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {'records': []}
        client.session.get.return_value = mock_resp

        client._query_records('f', page_size=99999)
        call_args = client.session.get.call_args
        assert call_args[1]['params']['page_size'] == 10000

    def test_page_size_within_range_unchanged(self):
        client = _make_client()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {'records': []}
        client.session.get.return_value = mock_resp

        client._query_records('f', page_size=50)
        call_args = client.session.get.call_args
        assert call_args[1]['params']['page_size'] == 50

    def test_order_by_passed(self):
        client = _make_client()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {'records': []}
        client.session.get.return_value = mock_resp

        client._query_records('f', order_by='update_time asc')
        call_args = client.session.get.call_args
        assert call_args[1]['params']['order_by'] == 'update_time asc'


# ===========================================================================
# _decode_record
# ===========================================================================

class TestDecodeRecord:

    def test_valid_base64_json(self):
        payload = {'metadata': {'name': 'tr-1'}, 'status': {}}
        record = {'data': {'value': _encode_record(payload)}}
        result = TektonResultsClient._decode_record(record)
        assert result == payload

    def test_missing_data_key(self):
        assert TektonResultsClient._decode_record({}) is None

    def test_missing_value_key(self):
        assert TektonResultsClient._decode_record({'data': {}}) is None

    def test_empty_value(self):
        assert TektonResultsClient._decode_record({'data': {'value': ''}}) is None

    def test_invalid_base64(self):
        record = {'data': {'value': '!!!not-base64!!!'}}
        assert TektonResultsClient._decode_record(record) is None

    def test_valid_base64_invalid_json(self):
        raw = base64.b64encode(b'not json at all').decode('utf-8')
        record = {'data': {'value': raw}}
        assert TektonResultsClient._decode_record(record) is None


# ===========================================================================
# query_pipelinerun_records
# ===========================================================================

class TestQueryPipelinerunRecords:

    def test_with_component(self):
        client = _make_client()
        payload = {'metadata': {'name': 'pr-1'}}
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            'records': [{'data': {'value': _encode_record(payload)}}]
        }
        client.session.get.return_value = mock_resp

        results = client.query_pipelinerun_records('my-app', component='my-comp')
        assert len(results) == 1
        assert results[0]['metadata']['name'] == 'pr-1'
        # Verify component appears in filter
        filter_used = client.session.get.call_args[1]['params']['filter']
        assert 'my-comp' in filter_used

    def test_without_component(self):
        client = _make_client()
        payload = {'metadata': {'name': 'pr-2'}}
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            'records': [{'data': {'value': _encode_record(payload)}}]
        }
        client.session.get.return_value = mock_resp

        results = client.query_pipelinerun_records('my-app')
        assert len(results) == 1
        filter_used = client.session.get.call_args[1]['params']['filter']
        assert 'component' not in filter_used

    def test_empty_results(self):
        client = _make_client()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {'records': []}
        client.session.get.return_value = mock_resp

        assert client.query_pipelinerun_records('app') == []

    def test_skips_undecoded_records(self):
        client = _make_client()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            'records': [
                {'data': {'value': _encode_record({'metadata': {'name': 'good'}})}},
                {'data': {}},  # will fail to decode
            ]
        }
        client.session.get.return_value = mock_resp

        results = client.query_pipelinerun_records('app')
        assert len(results) == 1
        assert results[0]['metadata']['name'] == 'good'


# ===========================================================================
# query_taskrun_records
# ===========================================================================

class TestQueryTaskrunRecords:

    def test_returns_tuples(self):
        client = _make_client()
        tr_payload = {'metadata': {'name': 'tr-abc'}, 'status': {}}
        record = {
            'data': {'value': _encode_record(tr_payload)},
            'name': 'ns/results/res-id/records/rec-id',
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {'records': [record]}
        client.session.get.return_value = mock_resp

        results = client.query_taskrun_records('pr-name')
        assert len(results) == 1
        decoded, rec_name = results[0]
        assert decoded['metadata']['name'] == 'tr-abc'
        assert rec_name == 'ns/results/res-id/records/rec-id'

    def test_empty_records(self):
        client = _make_client()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {'records': []}
        client.session.get.return_value = mock_resp

        assert client.query_taskrun_records('pr-x') == []

    def test_skips_bad_decode(self):
        client = _make_client()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            'records': [
                {'data': {}, 'name': 'ns/results/1/records/1'},
            ]
        }
        client.session.get.return_value = mock_resp

        assert client.query_taskrun_records('pr-y') == []


# ===========================================================================
# get_taskrun_logs
# ===========================================================================

class TestGetTaskrunLogs:

    def test_valid_record_name_200(self):
        client = _make_client()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = 'step-build log output here'
        client.session.get.return_value = mock_resp

        result = client.get_taskrun_logs('ns/results/abc-123/records/def-456')
        assert result == 'step-build log output here'
        url_used = client.session.get.call_args[0][0]
        assert '/results/abc-123/logs/def-456' in url_used

    def test_short_record_name_returns_none(self):
        client = _make_client()
        assert client.get_taskrun_logs('too/short') is None
        assert client.get_taskrun_logs('a/b/c') is None
        assert client.get_taskrun_logs('') is None

    def test_exactly_five_parts(self):
        client = _make_client()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = 'logs'
        client.session.get.return_value = mock_resp

        result = client.get_taskrun_logs('a/b/c/d/e')
        assert result == 'logs'

    def test_non_200_returns_none(self):
        client = _make_client()
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        client.session.get.return_value = mock_resp

        assert client.get_taskrun_logs('ns/results/r1/records/r2') is None

    def test_request_exception_returns_none(self):
        client = _make_client()
        client.session.get.side_effect = requests.RequestException('conn error')

        assert client.get_taskrun_logs('ns/results/r1/records/r2') is None


# ===========================================================================
# get_taskrun_logs_by_name
# ===========================================================================

class TestGetTaskrunLogsByName:

    def test_found_record(self):
        client = _make_client()

        # First call: _query_records returns a record
        query_resp = MagicMock()
        query_resp.status_code = 200
        query_resp.json.return_value = {
            'records': [{'name': 'ns/results/r1/records/r2'}]
        }
        # Second call: get_taskrun_logs fetches the logs
        logs_resp = MagicMock()
        logs_resp.status_code = 200
        logs_resp.text = 'the task logs'
        client.session.get.side_effect = [query_resp, logs_resp]

        result = client.get_taskrun_logs_by_name('my-taskrun-abc')
        assert result == 'the task logs'

    def test_no_records_found(self):
        client = _make_client()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {'records': []}
        client.session.get.return_value = mock_resp

        assert client.get_taskrun_logs_by_name('nonexistent') is None

    def test_record_with_empty_name(self):
        client = _make_client()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {'records': [{'name': ''}]}
        client.session.get.return_value = mock_resp

        # Empty name leads to short split, returns None
        assert client.get_taskrun_logs_by_name('tr-x') is None


# ===========================================================================
# find_failed_taskrun
# ===========================================================================

class TestFindFailedTaskrun:

    def _setup_taskrun_query(self, client, taskruns_data):
        """Set up mock to return taskrun records from query_taskrun_records."""
        records = []
        for tr_dict, rec_name in taskruns_data:
            records.append({
                'data': {'value': _encode_record(tr_dict)},
                'name': rec_name,
            })
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {'records': records}
        return mock_resp

    def test_failed_tr_with_logs(self):
        client = _make_client()
        tr = {
            'metadata': {'name': 'tr-fail', 'labels': {'tekton.dev/pipelineTask': 'build'}},
            'status': {'conditions': [{'status': 'False', 'message': 'err', 'reason': 'Failed'}]},
        }
        query_resp = self._setup_taskrun_query(client, [(tr, 'ns/results/r1/records/r2')])
        logs_resp = MagicMock()
        logs_resp.status_code = 200
        logs_resp.text = 'error: OOM killed'
        client.session.get.side_effect = [query_resp, logs_resp]

        task_name, logs, rec_name = client.find_failed_taskrun('pr-1')
        assert task_name == 'build'
        assert logs == 'error: OOM killed'
        assert rec_name == 'ns/results/r1/records/r2'

    def test_failed_tr_fallback_condition_message(self):
        client = _make_client()
        tr = {
            'metadata': {'name': 'tr-fail', 'labels': {'tekton.dev/pipelineTask': 'deploy'}},
            'status': {'conditions': [{'status': 'False', 'message': 'pod creation failed', 'reason': 'PodCreationFailed'}]},
        }
        query_resp = self._setup_taskrun_query(client, [(tr, 'ns/results/r1/records/r2')])
        logs_resp = MagicMock()
        logs_resp.status_code = 404  # no logs available
        client.session.get.side_effect = [query_resp, logs_resp]

        task_name, logs, rec_name = client.find_failed_taskrun('pr-1')
        assert task_name == 'deploy'
        assert logs == 'PodCreationFailed: pod creation failed'

    def test_no_failed_taskruns(self):
        client = _make_client()
        tr = {
            'metadata': {'name': 'tr-ok', 'labels': {'tekton.dev/pipelineTask': 'build'}},
            'status': {'conditions': [{'status': 'True', 'message': 'ok'}]},
        }
        query_resp = self._setup_taskrun_query(client, [(tr, 'ns/results/r1/records/r2')])
        client.session.get.return_value = query_resp

        task_name, logs, rec_name = client.find_failed_taskrun('pr-ok')
        assert task_name is None
        assert logs is None
        assert rec_name is None

    def test_no_conditions_skipped(self):
        client = _make_client()
        tr = {
            'metadata': {'name': 'tr-pending', 'labels': {}},
            'status': {},
        }
        query_resp = self._setup_taskrun_query(client, [(tr, 'ns/results/r1/records/r2')])
        client.session.get.return_value = query_resp

        assert client.find_failed_taskrun('pr-x') == (None, None, None)

    def test_failed_tr_no_logs_no_condition_message(self):
        client = _make_client()
        tr = {
            'metadata': {'name': 'tr-fail', 'labels': {'tekton.dev/pipelineTask': 'test'}},
            'status': {'conditions': [{'status': 'False', 'message': '', 'reason': ''}]},
        }
        query_resp = self._setup_taskrun_query(client, [(tr, 'ns/results/r1/records/r2')])
        logs_resp = MagicMock()
        logs_resp.status_code = 500
        client.session.get.side_effect = [query_resp, logs_resp]

        task_name, logs, rec_name = client.find_failed_taskrun('pr-1')
        assert task_name == 'test'
        # No logs fetched and empty condition_msg means logs stays None
        assert logs is None
        assert rec_name == 'ns/results/r1/records/r2'


# ===========================================================================
# query_component_build_history
# ===========================================================================

class TestQueryComponentBuildHistory:

    def test_filters_to_push_incoming(self):
        client = _make_client()
        push_pr = {'metadata': {'name': 'pr-push', 'labels': {'pipelinesascode.tekton.dev/event-type': 'push'}}}
        incoming_pr = {'metadata': {'name': 'pr-inc', 'labels': {'pipelinesascode.tekton.dev/event-type': 'incoming'}}}
        pull_pr = {'metadata': {'name': 'pr-pull', 'labels': {'pipelinesascode.tekton.dev/event-type': 'pull_request'}}}
        no_label_pr = {'metadata': {'name': 'pr-no', 'labels': {}}}

        records = [
            {'data': {'value': _encode_record(push_pr)}},
            {'data': {'value': _encode_record(incoming_pr)}},
            {'data': {'value': _encode_record(pull_pr)}},
            {'data': {'value': _encode_record(no_label_pr)}},
        ]
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {'records': records}
        client.session.get.return_value = mock_resp

        results = client.query_component_build_history('app', 'comp')
        assert len(results) == 2
        names = [r['metadata']['name'] for r in results]
        assert 'pr-push' in names
        assert 'pr-inc' in names
        assert 'pr-pull' not in names

    def test_empty_results(self):
        client = _make_client()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {'records': []}
        client.session.get.return_value = mock_resp

        assert client.query_component_build_history('app', 'comp') == []

    def test_filter_includes_component(self):
        client = _make_client()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {'records': []}
        client.session.get.return_value = mock_resp

        client.query_component_build_history('my-app', 'my-comp', page_size=5)
        filter_used = client.session.get.call_args[1]['params']['filter']
        assert 'my-comp' in filter_used
        assert 'my-app' in filter_used


# ===========================================================================
# query_conforma_records
# ===========================================================================

class TestQueryConformaRecords:

    def test_with_component(self):
        client = _make_client()
        payload = {'metadata': {'name': 'ec-1'}}
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            'records': [{'data': {'value': _encode_record(payload)}}]
        }
        client.session.get.return_value = mock_resp

        results = client.query_conforma_records('app', component='comp')
        assert len(results) == 1
        filter_used = client.session.get.call_args[1]['params']['filter']
        assert "type']=='test'" in filter_used
        assert 'comp' in filter_used

    def test_without_component(self):
        client = _make_client()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {'records': []}
        client.session.get.return_value = mock_resp

        client.query_conforma_records('app')
        filter_used = client.session.get.call_args[1]['params']['filter']
        assert 'component' not in filter_used


# ===========================================================================
# get_pipelinerun_logs
# ===========================================================================

class TestGetPipelinerunLogs:

    def _make_tr(self, name, task_label, failed=False):
        status_val = 'False' if failed else 'True'
        return {
            'metadata': {
                'name': name,
                'labels': {'tekton.dev/pipelineTask': task_label},
            },
            'status': {
                'conditions': [{
                    'status': status_val,
                    'message': 'some error' if failed else 'ok',
                    'reason': 'Failed' if failed else 'Succeeded',
                }],
            },
        }

    def _make_query_response(self, tr_list):
        records = []
        for tr_dict, rec_name in tr_list:
            records.append({
                'data': {'value': _encode_record(tr_dict)},
                'name': rec_name,
            })
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {'records': records}
        return resp

    def test_combined_logs(self):
        client = _make_client()
        tr1 = self._make_tr('tr-1', 'clone')
        tr2 = self._make_tr('tr-2', 'build')

        query_resp = self._make_query_response([
            (tr1, 'ns/results/r1/records/rec1'),
            (tr2, 'ns/results/r1/records/rec2'),
        ])

        logs_resp_1 = MagicMock()
        logs_resp_1.status_code = 200
        logs_resp_1.text = 'clone logs'

        logs_resp_2 = MagicMock()
        logs_resp_2.status_code = 200
        logs_resp_2.text = 'build logs'

        client.session.get.side_effect = [query_resp, logs_resp_1, logs_resp_2]

        result = client.get_pipelinerun_logs('pr-1')
        assert 'clone logs' in result
        assert 'build logs' in result
        assert 'TaskRun: tr-1' in result
        assert 'Task: clone' in result

    def test_failed_only_true(self):
        client = _make_client()
        failed_tr = self._make_tr('tr-fail', 'build', failed=True)
        ok_tr = self._make_tr('tr-ok', 'clone', failed=False)

        query_resp = self._make_query_response([
            (ok_tr, 'ns/results/r1/records/rec-ok'),
            (failed_tr, 'ns/results/r1/records/rec-fail'),
        ])

        logs_resp = MagicMock()
        logs_resp.status_code = 200
        logs_resp.text = 'failure logs'

        client.session.get.side_effect = [query_resp, logs_resp]

        result = client.get_pipelinerun_logs('pr-1', failed_only=True)
        assert 'failure logs' in result
        # Should NOT contain the ok TR's logs since we only fetched failed
        assert 'clone' not in result

    def test_no_taskruns_returns_none(self):
        client = _make_client()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {'records': []}
        client.session.get.return_value = mock_resp

        assert client.get_pipelinerun_logs('pr-empty') is None

    def test_max_log_size_truncation(self):
        client = _make_client()
        tr1 = self._make_tr('tr-1', 'step1')
        tr2 = self._make_tr('tr-2', 'step2')

        query_resp = self._make_query_response([
            (tr1, 'ns/results/r1/records/rec1'),
            (tr2, 'ns/results/r1/records/rec2'),
        ])

        # Each log section will be: header + logs, exceeding a small max
        logs_resp_1 = MagicMock()
        logs_resp_1.status_code = 200
        logs_resp_1.text = 'A' * 100

        logs_resp_2 = MagicMock()
        logs_resp_2.status_code = 200
        logs_resp_2.text = 'B' * 100

        client.session.get.side_effect = [query_resp, logs_resp_1, logs_resp_2]

        # First section: header (~42 chars) + 100 'A's = ~142 total
        # Set max_log_size so first fits but both together exceed it
        result = client.get_pipelinerun_logs('pr-1', max_log_size=200)
        # First section fits; second would push past 200 so it is dropped
        assert 'A' * 100 in result
        assert 'B' * 100 not in result

    def test_failed_tr_without_logs_falls_back_to_condition(self):
        client = _make_client()
        failed_tr = self._make_tr('tr-fail', 'deploy', failed=True)

        query_resp = self._make_query_response([
            (failed_tr, 'ns/results/r1/records/rec-fail'),
        ])

        # Logs request returns 404
        logs_resp = MagicMock()
        logs_resp.status_code = 404
        client.session.get.side_effect = [query_resp, logs_resp]

        result = client.get_pipelinerun_logs('pr-1', failed_only=True)
        assert 'Failed: some error' in result

    def test_all_logs_empty_returns_none(self):
        client = _make_client()
        tr = self._make_tr('tr-1', 'step1')

        query_resp = self._make_query_response([
            (tr, 'ns/results/r1/records/rec1'),
        ])

        # Logs request fails
        logs_resp = MagicMock()
        logs_resp.status_code = 500
        client.session.get.side_effect = [query_resp, logs_resp]

        # Non-failed TR, logs fail -> no fallback, result is None
        assert client.get_pipelinerun_logs('pr-1') is None

    def test_connection_failure_during_query(self):
        client = _make_client()
        client.session.get.side_effect = requests.RequestException('connection refused')

        # query_taskrun_records returns [], so get_pipelinerun_logs returns None
        assert client.get_pipelinerun_logs('pr-fail') is None


# ===========================================================================
# Edge cases
# ===========================================================================

class TestEdgeCases:

    def test_empty_record_list_from_query(self):
        client = _make_client()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {'records': []}
        client.session.get.return_value = mock_resp

        assert client.query_pipelinerun_records('app') == []
        assert client.query_taskrun_records('pr') == []
        assert client.query_conforma_records('app') == []
        assert client.query_component_build_history('app', 'comp') == []

    def test_none_namespace_in_url(self):
        """Namespace=None produces a URL with 'None' — tests that no crash occurs."""
        with patch('clients.tekton_results.discover_openshift_api_url', return_value='https://api.x.com:6443'), \
             patch('clients.tekton_results.get_openshift_token', return_value='t'), \
             patch('clients.tekton_results.create_authenticated_session') as ms:
            ms.return_value = MagicMock()
            c = TektonResultsClient(api_url='https://tr.example.com/apis/results.tekton.dev/v1alpha2')

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {'records': []}
        c.session.get.return_value = mock_resp

        # Should not raise even though namespace is None
        c._query_records('f')
        url_called = c.session.get.call_args[0][0]
        assert 'None' in url_called

    def test_decode_record_nested_empty(self):
        assert TektonResultsClient._decode_record({'data': {'value': None}}) is None
