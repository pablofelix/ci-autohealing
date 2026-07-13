"""Tests for tekton_parsers pure functions."""

from models import BuildStatus
from tekton_parsers import (
    build_taskrun_detail,
    classify_build_status,
    classify_pipelinerun_status,
    extract_all_errors_from_logs,
    extract_conforma_component_info,
    extract_error_from_logs,
    extract_failed_step_from_logs,
    extract_failed_step_names,
    extract_failed_task_names_from_conditions,
    extract_pipelinerun_metadata,
    extract_pr_number_from_annotations,
    extract_taskrun_names,
    extract_verify_taskrun_name,
    pick_primary_failure,
)

# -- extract_taskrun_names --

def test_extract_taskrun_names_from_child_refs():
    pr = {'status': {'childReferences': [
        {'kind': 'TaskRun', 'name': 'tr-1'},
        {'kind': 'Run', 'name': 'run-1'},
        {'kind': 'TaskRun', 'name': 'tr-2'},
    ]}}
    assert extract_taskrun_names(pr) == ['tr-1', 'tr-2']


def test_extract_taskrun_names_empty():
    assert extract_taskrun_names({}) == []
    assert extract_taskrun_names({'status': {}}) == []
    assert extract_taskrun_names({'status': {'childReferences': []}}) == []


# -- extract_failed_task_names_from_conditions --

def test_extract_failed_task_names_from_conditions():
    msg = 'Tasks Completed: 10 (Failed: 2, Cancelled 0): "fips-check", "coverity-availability-check"'
    result = extract_failed_task_names_from_conditions(msg)
    assert result == ['fips-check', 'coverity-availability-check']


def test_extract_failed_task_names_no_failures():
    msg = 'Tasks Completed: 10 (Failed: 0, Cancelled 0)'
    assert extract_failed_task_names_from_conditions(msg) == []


def test_extract_failed_task_names_empty():
    assert extract_failed_task_names_from_conditions('') == []
    assert extract_failed_task_names_from_conditions(None) == []


# -- extract_failed_step_names --

def test_extract_failed_step_names_mixed():
    tr = {'status': {'steps': [
        {'name': 'clone', 'terminated': {'exitCode': 0}},
        {'name': 'build', 'terminated': {'exitCode': 1}},
        {'name': 'test', 'terminated': {'exitCode': 137}},
    ]}}
    assert extract_failed_step_names(tr) == ['build', 'test']


def test_extract_failed_step_names_all_pass():
    tr = {'status': {'steps': [
        {'name': 'clone', 'terminated': {'exitCode': 0}},
    ]}}
    assert extract_failed_step_names(tr) == []


def test_extract_failed_step_names_no_terminated():
    tr = {'status': {'steps': [{'name': 'running'}]}}
    assert extract_failed_step_names(tr) == []


# -- build_taskrun_detail --

def test_build_taskrun_detail():
    tr = {
        'metadata': {
            'name': 'tr-build-123',
            'labels': {'tekton.dev/pipelineTask': 'build'},
        },
        'spec': {'taskRef': {'name': 'buildah'}},
        'status': {
            'podName': 'pod-abc',
            'startTime': '2026-04-20T10:00:00Z',
            'completionTime': '2026-04-20T10:05:00Z',
            'steps': [
                {
                    'name': 'build',
                    'container': 'step-build',
                    'terminated': {
                        'exitCode': 1,
                        'reason': 'Error',
                        'startedAt': '2026-04-20T10:01:00Z',
                        'finishedAt': '2026-04-20T10:04:00Z',
                    }
                }
            ]
        }
    }
    detail = build_taskrun_detail(tr)
    assert detail['name'] == 'tr-build-123'
    assert detail['pod_name'] == 'pod-abc'
    assert detail['task_name'] == 'buildah'
    assert detail['pipeline_task'] == 'build'
    assert detail['failed_steps'] == ['build']
    assert len(detail['steps']) == 1
    assert detail['steps'][0]['exit_code'] == 1


# -- extract_error_from_logs --

def test_extract_error_from_logs_build_error():
    msg, typ = extract_error_from_logs("ERROR: package not found")
    assert typ == "Build Error"
    assert "package not found" in msg


def test_extract_error_from_logs_none():
    assert extract_error_from_logs(None) == (None, None)
    assert extract_error_from_logs("") == (None, None)


def test_extract_error_from_logs_no_match():
    assert extract_error_from_logs("all good") == (None, None)


def test_extract_error_from_logs_timeout():
    _, typ = extract_error_from_logs("timed out waiting")
    assert typ == "Timeout"


def test_extract_error_from_logs_truncates():
    msg, _ = extract_error_from_logs("ERROR: " + "x" * 1000)
    assert len(msg) <= 510


# -- extract_all_errors_from_logs --

def test_extract_all_errors_multiple():
    logs = (
        'time="12:00" level=info msg="starting"\n'
        'FAILED: Coverity license expired\n'
        'time="12:01" level=info msg="checking fips"\n'
        'ERROR: executable is not dynamically linked\n'
    )
    errors = extract_all_errors_from_logs(logs)
    assert len(errors) == 2
    assert 'Coverity license expired' in errors[0][0]
    assert errors[0][1] == 'Test Failure'
    assert 'not dynamically linked' in errors[1][0]
    assert errors[1][1] == 'Build Error'


def test_extract_all_errors_none():
    assert extract_all_errors_from_logs('all good\nno errors') == []
    assert extract_all_errors_from_logs('') == []
    assert extract_all_errors_from_logs(None) == []


def test_extract_all_errors_skips_metadata():
    logs = 'Slack Message: FAILED build\nERROR: real error here'
    errors = extract_all_errors_from_logs(logs)
    assert len(errors) == 1
    assert 'real error' in errors[0][0]


# -- extract_failed_step_from_logs --

def test_extract_failed_step_from_logs_found():
    logs = "===== TaskRun: build / Step: compile ====="
    assert extract_failed_step_from_logs(logs) == "compile"


def test_extract_failed_step_from_logs_none():
    assert extract_failed_step_from_logs(None) is None
    assert extract_failed_step_from_logs("no markers") is None


# -- extract_pr_number_from_annotations --

def test_pr_number_found():
    ann = {'pipelinesascode.tekton.dev/pull-request-url': 'https://github.com/org/repo/pull/42'}
    assert extract_pr_number_from_annotations(ann) == 42


def test_pr_number_missing():
    assert extract_pr_number_from_annotations({}) is None


def test_pr_number_not_a_pull():
    ann = {'pipelinesascode.tekton.dev/pull-request-url': 'https://github.com/org/repo/issues/5'}
    assert extract_pr_number_from_annotations(ann) is None


# -- extract_pipelinerun_metadata --

def test_extract_metadata_none_input():
    assert extract_pipelinerun_metadata(None, 'ns', 'app') == {}


def test_extract_metadata_empty():
    result = extract_pipelinerun_metadata({}, 'ns', 'app')
    assert result.get('commit_sha') is None


def test_extract_metadata_full():
    pr = {
        'metadata': {
            'name': 'pr-123',
            'namespace': 'test-ns',
            'annotations': {
                'build.appstudio.redhat.com/commit_sha': 'abc123',
                'pipelinesascode.tekton.dev/sha-title': 'Fix bug',
                'pipelinesascode.tekton.dev/sender': 'user1',
                'pipelinesascode.tekton.dev/branch': 'main',
            }
        },
        'spec': {'params': [{'name': 'git-url', 'value': 'https://github.com/org/repo.git'}]},
        'status': {
            'startTime': '2026-04-20T10:00:00Z',
            'completionTime': '2026-04-20T10:15:00Z',
            'childReferences': [],
        }
    }
    result = extract_pipelinerun_metadata(pr, 'test-ns', 'test-app')
    assert result['commit_sha'] == 'abc123'
    assert result['commit_short_sha'] == 'abc123'  # only 6 chars, takes first 8
    assert result['commit_message'] == 'Fix bug'
    assert result['commit_author'] == 'user1'
    assert result['branch'] == 'main'


def test_extract_pipelinerun_metadata_failed_tasks():
    pr_data = {
        'metadata': {'annotations': {}, 'name': 'pr-1', 'namespace': 'ns'},
        'spec': {'params': []},
        'status': {
            'childReferences': [
                {'kind': 'TaskRun', 'name': 'tr-1', 'pipelineTaskName': 'build-container'},
                {'kind': 'TaskRun', 'name': 'tr-2', 'pipelineTaskName': 'fips-check'},
                {'kind': 'TaskRun', 'name': 'tr-3', 'pipelineTaskName': 'coverity-availability-check'},
            ],
            'conditions': [
                {'status': 'False', 'message': 'Tasks Completed: 3 (Failed: 1): "fips-check"'}
            ],
        },
    }
    result = extract_pipelinerun_metadata(pr_data, 'ns', 'app')
    assert result['failed_tasks'] == ['fips-check']


# -- classify_pipelinerun_status --

def test_classify_cancelled():
    assert classify_pipelinerun_status('PipelineRunCancelled') == 'Cancelled'


def test_classify_timeout():
    assert classify_pipelinerun_status('PipelineRunTimeout') == 'Timeout'
    assert classify_pipelinerun_status('TaskRunTimeout') == 'Timeout'


def test_classify_succeeded():
    assert classify_pipelinerun_status('Succeeded') == 'Succeeded'
    assert classify_pipelinerun_status('Completed') == 'Succeeded'


def test_classify_failed():
    assert classify_pipelinerun_status('Failed') == 'Failed'


def test_classify_running():
    assert classify_pipelinerun_status('Running') == 'Running'


def test_classify_unknown():
    assert classify_pipelinerun_status('SomeNewReason') == 'Pending'


# -- classify_build_status --

def test_build_status_failed():
    assert classify_build_status('Failed') == BuildStatus.FAILED


def test_build_status_cancelled_maps_to_failed():
    assert classify_build_status('PipelineRunCancelled') == BuildStatus.FAILED


def test_build_status_timeout_maps_to_failed():
    assert classify_build_status('PipelineRunTimeout') == BuildStatus.FAILED


def test_build_status_succeeded():
    assert classify_build_status('Succeeded') == BuildStatus.SUCCEEDED
    assert classify_build_status('Completed') == BuildStatus.SUCCEEDED


def test_build_status_running():
    assert classify_build_status('Running') == BuildStatus.RUNNING


def test_build_status_unknown_maps_to_pending():
    assert classify_build_status('SomeNewReason') == BuildStatus.PENDING


# -- extract_conforma_component_info --

def test_conforma_component_info_full():
    pr = {
        'metadata': {
            'labels': {'appstudio.openshift.io/snapshot': 'snap-1'},
            'annotations': {
                'pipelinesascode.tekton.dev/repo-url': 'https://github.com/org/repo',
                'build.appstudio.redhat.com/commit_sha': 'deadbeef',
            }
        },
        'spec': {'params': [{'name': 'IMAGES', 'value': 'quay.io/img:v1'}]}
    }
    info = extract_conforma_component_info(pr, 'comp')
    assert info['snapshot_name'] == 'snap-1'
    assert info['container_image'] == 'quay.io/img:v1'
    assert info['commit_sha'] == 'deadbeef'
    assert 'deadbeef' in info['commit_url']


def test_conforma_component_info_uses_fallback_repo():
    pr = {
        'metadata': {'labels': {}, 'annotations': {}},
        'spec': {'params': []}
    }
    info = extract_conforma_component_info(pr, 'comp', repo_url_fallback='https://github.com/org/fallback')
    assert info['repository_url'] == 'https://github.com/org/fallback'


# -- extract_verify_taskrun_name --

def test_verify_taskrun_found():
    pr = {'status': {'childReferences': [
        {'kind': 'TaskRun', 'pipelineTaskName': 'init', 'name': 'tr-init'},
        {'kind': 'TaskRun', 'pipelineTaskName': 'verify', 'name': 'tr-verify-456'},
    ]}}
    assert extract_verify_taskrun_name(pr) == 'tr-verify-456'


def test_verify_taskrun_not_found():
    assert extract_verify_taskrun_name({}) is None
    assert extract_verify_taskrun_name({'status': {'childReferences': []}}) is None


# -- pick_primary_failure --

def test_pick_primary_failure_single():
    failures = [('fips-check', 'patchelf not linked', 'full logs')]
    assert pick_primary_failure(failures, {}) == ('fips-check', 'patchelf not linked', 'full logs')


def test_pick_primary_failure_prefers_build_over_sideeffect():
    failures = [
        ('coverity-availability-check', 'license expired', 'coverity logs'),
        ('fips-check', 'patchelf not linked', 'fips logs'),
    ]
    result = pick_primary_failure(failures, {})
    assert result[0] == 'fips-check'


def test_pick_primary_failure_prefers_build_container():
    failures = [
        ('sast-snyk-check', 'snyk error', 'snyk logs'),
        ('build-container', 'OOM killed', 'build logs'),
    ]
    result = pick_primary_failure(failures, {})
    assert result[0] == 'build-container'


def test_pick_primary_failure_condition_message_match():
    pr_data = {'status': {'conditions': [
        {'status': 'False', 'message': 'Tasks Completed: 5 (Failed: 1): "fips-check"'}
    ]}}
    failures = [
        ('coverity-availability-check', 'license expired', 'coverity logs'),
        ('fips-check', 'patchelf not linked', 'fips logs'),
    ]
    result = pick_primary_failure(failures, pr_data)
    assert result[0] == 'fips-check'


def test_pick_primary_failure_prefers_logs_over_no_logs():
    failures = [
        ('fips-check', 'condition msg only', None),
        ('clamav-scan', 'virus found', 'full scan logs here'),
    ]
    result = pick_primary_failure(failures, {})
    # Same priority tier — prefer the one with logs
    assert result[0] == 'clamav-scan'


def test_pick_primary_failure_empty():
    assert pick_primary_failure([], {}) == (None, None, None)
