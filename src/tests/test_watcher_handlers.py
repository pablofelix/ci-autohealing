"""Tests for watcher event handlers."""

import unittest
from unittest.mock import MagicMock

from watcher.handlers import BuildEventHandler, ComponentEventHandler


def _make_pipelinerun(name, uid, component, status_val, reason,
                      event_type='push', app='test-app'):
    return {
        'metadata': {
            'name': name,
            'uid': uid,
            'labels': {
                'appstudio.openshift.io/component': component,
                'appstudio.openshift.io/application': app,
                'pipelinesascode.tekton.dev/event-type': event_type,
                'pipelines.appstudio.openshift.io/type': 'build',
            },
            'annotations': {},
        },
        'spec': {
            'params': [
                {'name': 'git-url', 'value': 'https://github.com/org/repo'},
                {'name': 'revision', 'value': 'main'},
            ],
        },
        'status': {
            'conditions': [
                {
                    'type': 'Succeeded',
                    'status': status_val,
                    'reason': reason,
                }
            ],
            'childReferences': [],
            'startTime': '2026-05-27T10:00:00Z',
            'completionTime': '2026-05-27T10:05:00Z',
        },
    }


class TestBuildEventHandler(unittest.TestCase):

    def setUp(self):
        self.build_repo = MagicMock()
        self.handler = BuildEventHandler(
            application='test-app',
            namespace='test-ns',
            build_repo=self.build_repo,
            pipeline_client=None,
            analyzer=None,
            auto_analyze=False,
        )

    def test_ignores_non_terminal_event(self):
        pr = _make_pipelinerun('pr-1', 'uid-1', 'comp-a', 'Unknown', 'Running')
        self.handler.handle({'type': 'MODIFIED', 'object': pr})
        self.build_repo.upsert_failure.assert_not_called()
        self.build_repo.record_successful_build.assert_not_called()

    def test_ignores_deleted_event(self):
        pr = _make_pipelinerun('pr-1', 'uid-1', 'comp-a', 'False', 'Failed')
        self.handler.handle({'type': 'DELETED', 'object': pr})
        self.build_repo.upsert_failure.assert_not_called()

    def test_handles_failure(self):
        pr = _make_pipelinerun('pr-1', 'uid-1', 'comp-a', 'False', 'Failed')
        self.build_repo.upsert_failure.return_value = True
        self.handler.handle({'type': 'MODIFIED', 'object': pr})
        self.build_repo.upsert_failure.assert_called_once()
        call_kwargs = self.build_repo.upsert_failure.call_args
        self.assertEqual(call_kwargs.kwargs['pr_name'], 'pr-1')
        self.assertEqual(call_kwargs.kwargs['component_name'], 'comp-a')
        self.assertEqual(call_kwargs.kwargs['status'], 'Failed')

    def test_handles_success(self):
        self.build_repo.get_unresolved_failure.return_value = {
            'id': 1,
            'failure_nature': None,
            'failed_step_name': '',
        }
        pr = _make_pipelinerun('pr-2', 'uid-2', 'comp-b', 'True', 'Succeeded')
        self.handler.handle({'type': 'MODIFIED', 'object': pr})
        self.build_repo.record_successful_build.assert_called_once()
        self.build_repo.mark_resolved.assert_called_once()

    def test_deduplication_by_uid(self):
        pr = _make_pipelinerun('pr-3', 'uid-3', 'comp-c', 'False', 'Failed')
        self.build_repo.upsert_failure.return_value = True
        self.handler.handle({'type': 'MODIFIED', 'object': pr})
        self.handler.handle({'type': 'MODIFIED', 'object': pr})
        self.assertEqual(self.build_repo.upsert_failure.call_count, 1)

    def test_ignores_event_without_component(self):
        pr = _make_pipelinerun('pr-4', 'uid-4', '', 'False', 'Failed')
        self.handler.handle({'type': 'MODIFIED', 'object': pr})
        self.build_repo.upsert_failure.assert_not_called()

    def test_handles_timeout(self):
        pr = _make_pipelinerun('pr-5', 'uid-5', 'comp-d', 'False', 'PipelineRunTimeout')
        self.build_repo.upsert_failure.return_value = True
        self.handler.handle({'type': 'MODIFIED', 'object': pr})
        self.build_repo.upsert_failure.assert_called_once()

    def test_calculates_duration(self):
        pr = _make_pipelinerun('pr-6', 'uid-6', 'comp-e', 'False', 'Failed')
        self.build_repo.upsert_failure.return_value = True
        self.handler.handle({'type': 'MODIFIED', 'object': pr})
        call_kwargs = self.build_repo.upsert_failure.call_args.kwargs
        self.assertEqual(call_kwargs['duration'], 300)

    def test_triggers_ai_analysis_on_new_failure(self):
        analyzer = MagicMock()
        handler = BuildEventHandler(
            application='test-app',
            namespace='test-ns',
            build_repo=self.build_repo,
            pipeline_client=MagicMock(get_pipelinerun_logs=MagicMock(return_value='ERROR: something broke')),
            analyzer=analyzer,
            auto_analyze=True,
        )
        pr = _make_pipelinerun('pr-7', 'uid-7', 'comp-f', 'False', 'Failed')
        self.build_repo.upsert_failure.return_value = True
        handler.handle({'type': 'MODIFIED', 'object': pr})
        analyzer.analyze.assert_called_once_with('comp-f', 'test-app')

    def test_no_analysis_when_not_new(self):
        analyzer = MagicMock()
        handler = BuildEventHandler(
            application='test-app',
            namespace='test-ns',
            build_repo=self.build_repo,
            pipeline_client=MagicMock(get_pipelinerun_logs=MagicMock(return_value='ERROR: x')),
            analyzer=analyzer,
            auto_analyze=True,
        )
        pr = _make_pipelinerun('pr-8', 'uid-8', 'comp-g', 'False', 'Failed')
        self.build_repo.upsert_failure.return_value = False
        handler.handle({'type': 'MODIFIED', 'object': pr})
        analyzer.analyze.assert_not_called()

    def test_structural_failure_not_auto_resolved(self):
        """Structural failure (failure_nature='structural') must NOT be resolved on success."""
        self.build_repo.get_unresolved_failure.return_value = {
            'id': 10,
            'failure_nature': 'structural',
            'failed_step_name': '',
        }
        pr = _make_pipelinerun('pr-ok', 'uid-ok', 'comp-a', 'True', 'Succeeded')
        self.handler.handle({'type': 'MODIFIED', 'object': pr})
        self.build_repo.record_successful_build.assert_called_once()
        self.build_repo.mark_resolved.assert_not_called()

    def test_step_mismatch_not_auto_resolved(self):
        """If the failed step didn't run in the success build, do NOT resolve."""
        self.build_repo.get_unresolved_failure.return_value = {
            'id': 11,
            'failure_nature': None,
            'failed_step_name': 'fips-check',
        }
        pr = _make_pipelinerun('pr-ok', 'uid-ok', 'comp-a', 'True', 'Succeeded')
        pr['status']['childReferences'] = [
            {'name': 'pr-ok-build-container', 'pipelineTaskName': 'build-container'},
            {'name': 'pr-ok-prefetch', 'pipelineTaskName': 'prefetch-dependencies'},
        ]
        self.handler.handle({'type': 'MODIFIED', 'object': pr})
        self.build_repo.record_successful_build.assert_called_once()
        self.build_repo.mark_resolved.assert_not_called()

    def test_normal_failure_auto_resolved(self):
        """Normal transient failure (unknown nature, no step mismatch) resolves as before."""
        self.build_repo.get_unresolved_failure.return_value = {
            'id': 12,
            'failure_nature': 'unknown',
            'failed_step_name': 'prefetch-dependencies',
        }
        pr = _make_pipelinerun('pr-ok', 'uid-ok', 'comp-a', 'True', 'Succeeded')
        pr['status']['childReferences'] = [
            {'name': 'pr-ok-prefetch', 'pipelineTaskName': 'prefetch-dependencies'},
            {'name': 'pr-ok-build', 'pipelineTaskName': 'build-container'},
        ]
        self.handler.handle({'type': 'MODIFIED', 'object': pr})
        self.build_repo.mark_resolved.assert_called_once()

    def test_no_failed_step_auto_resolved(self):
        """Failure with no failed_step_name resolves as before (backwards compat)."""
        self.build_repo.get_unresolved_failure.return_value = {
            'id': 13,
            'failure_nature': None,
            'failed_step_name': '',
        }
        pr = _make_pipelinerun('pr-ok', 'uid-ok', 'comp-a', 'True', 'Succeeded')
        self.handler.handle({'type': 'MODIFIED', 'object': pr})
        self.build_repo.mark_resolved.assert_called_once()

    def test_no_unresolved_failure_still_records_success(self):
        """When there's no unresolved failure, record success but skip mark_resolved."""
        self.build_repo.get_unresolved_failure.return_value = None
        pr = _make_pipelinerun('pr-ok', 'uid-ok', 'comp-a', 'True', 'Succeeded')
        self.handler.handle({'type': 'MODIFIED', 'object': pr})
        self.build_repo.record_successful_build.assert_called_once()
        self.build_repo.mark_resolved.assert_not_called()


class TestComponentEventHandler(unittest.TestCase):

    def test_logs_component_event(self):
        handler = ComponentEventHandler(
            application='test-app',
            namespace='test-ns',
        )
        event = {
            'type': 'ADDED',
            'object': {
                'metadata': {'name': 'my-component'},
                'spec': {
                    'source': {
                        'git': {
                            'url': 'https://github.com/org/repo',
                            'revision': 'main',
                        }
                    }
                },
            },
        }
        handler.handle(event)

    def test_handles_deleted_component(self):
        handler = ComponentEventHandler(
            application='test-app',
            namespace='test-ns',
        )
        event = {
            'type': 'DELETED',
            'object': {
                'metadata': {'name': 'deleted-component'},
                'spec': {},
            },
        }
        handler.handle(event)


if __name__ == '__main__':
    unittest.main()
