"""Tests for nightly build chain correlation logic."""

from datetime import datetime

from proactive.health_monitor import _correlate_nightly_chain


def _make_status(gha_conclusion='success', gha_created_at=None,
                 fbc_status='Succeeded', blockers=None, pcc_status='fresh',
                 pcc_cached=12):
    """Build a minimal get_nightly_status()-shaped dict."""
    gha = None
    if gha_conclusion is not None:
        gha = {
            'conclusion': gha_conclusion,
            'status': 'completed',
            'created_at': gha_created_at or '2026-07-13T00:15:00Z',
            'url': 'https://github.com/red-hat-data-services/rhods-devops-infra/actions/runs/123',
        }
    fbc_health = None
    if fbc_status is not None:
        fbc_health = {'current_status': fbc_status, 'health_score': 100}
    pcc = None
    if pcc_status is not None:
        pcc = {'status': pcc_status, 'cached_versions': pcc_cached}
    return {
        'application': 'rhoai-v3-5',
        'fbc_component': 'rhoai-fbc-fragment-v3-5',
        'fbc_health': fbc_health,
        'fbc_image': 'quay.io/rhoai/fbc-fragment:latest',
        'fbc_conforma': None,
        'blockers': blockers or [],
        'blockers_count': len(blockers or []),
        'gha_validation': gha,
        'pcc_freshness': pcc,
    }


def _make_history(builds=None):
    """Build a minimal get_nightly_history()-shaped dict."""
    return {
        'application': 'rhoai-v3-5',
        'nightly_builds': builds or [],
        'fbc_fragments': [],
        'days': 14,
    }


def _make_build(component, status='Succeeded', time_str='2026-07-13T00:42:00Z'):
    return {
        'component_name': component,
        'status': status,
        'build_completion_time': datetime.fromisoformat(time_str.replace('Z', '+00:00')),
        'pipelinerun_name': f'build-{component}-abc',
        'build_date': datetime(2026, 7, 13).date(),
        'commit_sha': 'abc123',
    }


class TestChainHealthy:
    def test_all_steps_pass(self):
        status = _make_status()
        history = _make_history(builds=[
            _make_build('odh-operator-v3-5'),
            _make_build('odh-dashboard-v3-5'),
        ])
        result = _correlate_nightly_chain(status, history)
        assert result['chain_status'] == 'healthy'
        assert result['break_point'] is None
        assert len(result['steps']) == 4
        assert all(s['status'] == 'pass' for s in result['steps'])

    def test_chain_date_from_gha(self):
        status = _make_status(gha_created_at='2026-07-13T00:15:00Z')
        history = _make_history(builds=[_make_build('odh-operator-v3-5')])
        result = _correlate_nightly_chain(status, history)
        assert result['chain_date'] == '2026-07-13'


class TestChainBroken:
    def test_gha_failure(self):
        status = _make_status(gha_conclusion='failure')
        history = _make_history()
        result = _correlate_nightly_chain(status, history)
        assert result['chain_status'] == 'broken'
        assert result['break_point'] == 'GHA Trigger'
        assert result['steps'][0]['status'] == 'fail'

    def test_operator_build_failure(self):
        status = _make_status()
        history = _make_history(builds=[
            _make_build('odh-operator-v3-5', status='Failed'),
            _make_build('odh-dashboard-v3-5'),
        ])
        result = _correlate_nightly_chain(status, history)
        assert result['chain_status'] == 'broken'
        assert result['break_point'] == 'Operator Build'
        assert result['steps'][1]['status'] == 'fail'

    def test_fbc_failure_with_blockers(self):
        blockers = [
            {'component': 'odh-trustyai-v3-5', 'error_type': 'build_error'},
            {'component': 'odh-vllm-v3-5', 'error_type': 'dependency_issue'},
        ]
        status = _make_status(fbc_status='Failed', blockers=blockers)
        history = _make_history(builds=[
            _make_build('odh-operator-v3-5'),
            _make_build('rhoai-fbc-fragment-v3-5', status='Failed',
                        time_str='2026-07-13T01:18:00Z'),
        ])
        result = _correlate_nightly_chain(status, history)
        assert result['chain_status'] == 'broken'
        assert result['break_point'] == 'FBC Fragment'
        fbc_step = result['steps'][2]
        assert fbc_step['status'] == 'fail'
        assert fbc_step.get('blockers') == ['odh-trustyai-v3-5', 'odh-vllm-v3-5']

    def test_pcc_stale(self):
        status = _make_status(pcc_status='stale')
        history = _make_history(builds=[_make_build('odh-operator-v3-5')])
        result = _correlate_nightly_chain(status, history)
        assert result['chain_status'] == 'broken'
        assert result['break_point'] == 'PCC Cache'
        assert result['steps'][3]['status'] == 'fail'


class TestChainDegradation:
    def test_no_gha_token(self):
        status = _make_status(gha_conclusion=None)
        history = _make_history(builds=[_make_build('odh-operator-v3-5')])
        result = _correlate_nightly_chain(status, history)
        assert result['steps'][0]['status'] == 'skip'
        assert result['chain_status'] == 'healthy'

    def test_no_nightly_builds(self):
        status = _make_status()
        history = _make_history(builds=[])
        result = _correlate_nightly_chain(status, history)
        assert result['steps'][1]['status'] == 'skip'
        assert result['steps'][1]['detail'] == 'no recent nightly builds'

    def test_all_skip_is_unknown(self):
        status = _make_status(gha_conclusion=None, fbc_status=None, pcc_status=None)
        history = _make_history(builds=[])
        result = _correlate_nightly_chain(status, history)
        assert result['chain_status'] == 'unknown'
        assert all(s['status'] == 'skip' for s in result['steps'])

    def test_no_pcc_data(self):
        status = _make_status(pcc_status=None)
        history = _make_history(builds=[_make_build('odh-operator-v3-5')])
        result = _correlate_nightly_chain(status, history)
        assert result['steps'][3]['status'] == 'skip'


class TestStepDetails:
    def test_gha_step_has_url(self):
        status = _make_status()
        history = _make_history(builds=[_make_build('odh-operator-v3-5')])
        result = _correlate_nightly_chain(status, history)
        gha = result['steps'][0]
        assert gha['name'] == 'GHA Trigger'
        assert gha['url'] is not None

    def test_operator_build_count_in_detail(self):
        status = _make_status()
        history = _make_history(builds=[
            _make_build('odh-operator-v3-5'),
            _make_build('odh-dashboard-v3-5'),
        ])
        result = _correlate_nightly_chain(status, history)
        assert '2/2 passed' in result['steps'][1]['detail']

    def test_operator_build_partial_failure(self):
        status = _make_status()
        history = _make_history(builds=[
            _make_build('odh-operator-v3-5', status='Failed'),
            _make_build('odh-dashboard-v3-5'),
            _make_build('odh-modelmesh-v3-5'),
        ])
        result = _correlate_nightly_chain(status, history)
        assert '1/3 failed' in result['steps'][1]['detail']

    def test_pcc_fresh_detail(self):
        status = _make_status(pcc_status='fresh', pcc_cached=15)
        history = _make_history(builds=[_make_build('odh-operator-v3-5')])
        result = _correlate_nightly_chain(status, history)
        assert '15 versions cached' in result['steps'][3]['detail']
