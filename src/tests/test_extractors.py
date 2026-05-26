"""Unit tests for pure static extractors and log filtering."""

import unittest


class TestExtractSnapshotStatus(unittest.TestCase):

    def _extract(self, snapshot):
        from clients.konflux_client import KonfluxClient
        return KonfluxClient.extract_snapshot_status(snapshot)

    def test_basic_snapshot(self):
        snap = {
            'metadata': {'name': 'snap-abc', 'creationTimestamp': '2025-05-20T10:00:00Z'},
            'spec': {
                'application': 'rhoai-v3-5',
                'components': [
                    {'name': 'comp-a', 'containerImage': 'quay.io/a:sha'},
                    {'name': 'comp-b', 'containerImage': 'quay.io/b:sha'},
                ],
            },
            'status': {'conditions': []},
        }
        result = self._extract(snap)
        self.assertEqual(result['name'], 'snap-abc')
        self.assertEqual(result['application'], 'rhoai-v3-5')
        self.assertEqual(result['component_count'], 2)
        self.assertEqual(result['components'], ['comp-a', 'comp-b'])
        self.assertEqual(result['test_results'], {})

    def test_with_test_conditions(self):
        snap = {
            'metadata': {'name': 'snap-1', 'creationTimestamp': '2025-05-20T10:00:00Z'},
            'spec': {'application': 'app', 'components': []},
            'status': {'conditions': [
                {
                    'type': 'AppStudioTestSucceeded',
                    'status': 'True',
                    'reason': 'Passed',
                    'message': 'All tests passed',
                },
                {
                    'type': 'IntegrationTestScenarioValid',
                    'status': 'False',
                    'reason': 'Failed',
                    'message': 'Scenario X failed',
                },
            ]},
        }
        result = self._extract(snap)
        self.assertIn('Passed', result['test_results'])
        self.assertEqual(result['test_results']['Passed']['status'], 'True')

    def test_override_snapshot(self):
        snap = {
            'metadata': {
                'name': 'snap-override',
                'creationTimestamp': '2025-05-20T10:00:00Z',
                'labels': {'test.appstudio.openshift.io/type': 'override'},
            },
            'spec': {'application': 'app', 'components': []},
            'status': {'conditions': []},
        }
        result = self._extract(snap)
        self.assertTrue(result['is_override'])
        self.assertEqual(result['event_type'], 'override')

    def test_push_event_type_default(self):
        snap = {
            'metadata': {'name': 's', 'creationTimestamp': '2025-05-20T10:00:00Z'},
            'spec': {'components': []},
            'status': {'conditions': []},
        }
        result = self._extract(snap)
        self.assertEqual(result['event_type'], 'push')
        self.assertFalse(result['is_override'])

    def test_warning_detection(self):
        snap = {
            'metadata': {'name': 's'},
            'spec': {'components': []},
            'status': {'conditions': [{
                'type': 'AppStudioTestSucceeded',
                'status': 'True',
                'reason': 'fips-check',
                'message': 'Passed with WARNING: 2 binaries skipped',
            }]},
        }
        result = self._extract(snap)
        self.assertEqual(result['warnings'], ['fips-check'])

    def test_latest_transition_time(self):
        snap = {
            'metadata': {'name': 's'},
            'spec': {'components': []},
            'status': {'conditions': [
                {'type': 'AppStudioTestSucceeded', 'status': 'True',
                 'reason': 'a', 'message': '', 'lastTransitionTime': '2025-05-20T10:00:00Z'},
                {'type': 'Validated', 'status': 'True',
                 'reason': 'b', 'message': '', 'lastTransitionTime': '2025-05-20T10:30:00Z'},
            ]},
        }
        result = self._extract(snap)
        self.assertEqual(result['latest_transition'], '2025-05-20T10:30:00Z')

    def test_empty_snapshot(self):
        result = self._extract({})
        self.assertEqual(result['name'], '')
        self.assertEqual(result['component_count'], 0)
        self.assertEqual(result['test_results'], {})

    def test_message_truncation(self):
        snap = {
            'metadata': {'name': 's'},
            'spec': {'components': []},
            'status': {'conditions': [{
                'type': 'AppStudioTestSucceeded',
                'status': 'False',
                'reason': 'err',
                'message': 'x' * 500,
            }]},
        }
        result = self._extract(snap)
        self.assertLessEqual(len(result['test_results']['err']['message']), 300)


class TestExtractReleaseStatus(unittest.TestCase):

    def _extract(self, release):
        from clients.konflux_client import KonfluxClient
        return KonfluxClient.extract_release_status(release)

    def test_basic_release(self):
        rel = {
            'metadata': {'name': 'rel-1', 'creationTimestamp': '2025-05-21T12:00:00Z'},
            'spec': {'snapshot': 'snap-abc', 'releasePlan': 'plan-1'},
            'status': {
                'conditions': [
                    {'type': 'Released', 'status': 'True', 'message': 'ok'},
                ],
                'managedProcessing': {
                    'pipelineRun': 'ns/pr-release-1',
                    'startTime': '2025-05-21T12:01:00Z',
                    'completionTime': '2025-05-21T12:10:00Z',
                },
                'automated': True,
                'validation': {'failedPostValidation': False},
            },
        }
        result = self._extract(rel)
        self.assertEqual(result['name'], 'rel-1')
        self.assertEqual(result['snapshot'], 'snap-abc')
        self.assertEqual(result['release_plan'], 'plan-1')
        self.assertEqual(result['phase'], 'Released')
        self.assertEqual(result['status'], 'True')
        self.assertEqual(result['pipeline_run'], 'ns/pr-release-1')
        self.assertTrue(result['automated'])
        self.assertFalse(result['post_validation_failed'])

    def test_empty_conditions(self):
        rel = {
            'metadata': {'name': 'rel-2'},
            'spec': {},
            'status': {'conditions': []},
        }
        result = self._extract(rel)
        self.assertEqual(result['phase'], 'Unknown')
        self.assertEqual(result['status'], 'Unknown')

    def test_failed_post_validation(self):
        rel = {
            'metadata': {'name': 'rel-3'},
            'spec': {},
            'status': {
                'conditions': [{'type': 'Progressing', 'status': 'False', 'message': 'err'}],
                'validation': {'failedPostValidation': True},
            },
        }
        result = self._extract(rel)
        self.assertTrue(result['post_validation_failed'])

    def test_empty_release(self):
        result = self._extract({})
        self.assertEqual(result['name'], '')
        self.assertEqual(result['snapshot'], '')
        self.assertFalse(result['post_validation_failed'])


class TestExtractItsMetadata(unittest.TestCase):

    def _extract(self, scenario):
        from clients.konflux_client import KonfluxClient
        return KonfluxClient.extract_its_metadata(scenario)

    def test_disabled_scenario(self):
        s = {
            'metadata': {'name': 'test-disabled'},
            'spec': {
                'application': 'app',
                'contexts': [{'name': 'disabled'}],
                'params': [],
                'resolverRef': {'params': []},
            },
        }
        result = self._extract(s)
        self.assertTrue(result['is_disabled'])

    def test_component_scoped(self):
        s = {
            'metadata': {'name': 'test-comp'},
            'spec': {
                'application': 'app',
                'contexts': [{'name': 'component_my-image'}, {'name': 'push'}],
                'params': [],
                'resolverRef': {'params': []},
            },
        }
        result = self._extract(s)
        self.assertIn('component_my-image', result['contexts'])
        self.assertIn('push', result['contexts'])

    def test_conforma_detection(self):
        s = {
            'metadata': {'name': 'my-conforma-test'},
            'spec': {
                'application': 'app',
                'contexts': [],
                'params': [{'name': 'POLICY_CONFIGURATION', 'value': 'ec-policy-prod'}],
                'resolverRef': {'params': [
                    {'name': 'pathInRepo', 'value': 'pipelines/enterprise-contract.yaml'},
                ]},
            },
        }
        result = self._extract(s)
        self.assertTrue(result['is_conforma'])
        self.assertEqual(result['policy_ref'], 'ec-policy-prod')

    def test_future_scenario(self):
        s = {
            'metadata': {'name': 'test-future-validation'},
            'spec': {
                'application': 'app',
                'contexts': [],
                'params': [],
                'resolverRef': {'params': []},
            },
        }
        result = self._extract(s)
        self.assertTrue(result['is_future'])


class TestFilterErrorLines(unittest.TestCase):

    def _make_analyzer(self):
        from analyzers.build_failure_analyzer import BuildFailureAnalyzer
        return BuildFailureAnalyzer.__new__(BuildFailureAnalyzer)

    def test_filters_large_logs(self):
        analyzer = self._make_analyzer()
        lines = ['info line {}'.format(i) for i in range(1000)]
        lines[500] = 'error: something failed'
        logs = '\n'.join(lines)
        result = analyzer._filter_error_lines(logs, context_lines=5)
        self.assertIsNotNone(result)
        self.assertIn('error: something failed', result)
        self.assertIn('... (', result)
        self.assertLess(len(result), len(logs))

    def test_returns_none_when_mostly_errors(self):
        analyzer = self._make_analyzer()
        lines = ['error: line {}'.format(i) for i in range(100)]
        logs = '\n'.join(lines)
        result = analyzer._filter_error_lines(logs, context_lines=5)
        self.assertIsNone(result)

    def test_preserves_first_and_last_lines(self):
        analyzer = self._make_analyzer()
        lines = ['HEADER line {}'.format(i) for i in range(10)]
        lines += ['info line {}'.format(i) for i in range(1000)]
        lines += ['FOOTER line {}'.format(i) for i in range(30)]
        lines[500] = 'fatal error occurred'
        logs = '\n'.join(lines)
        result = analyzer._filter_error_lines(logs, context_lines=3)
        self.assertIsNotNone(result)
        self.assertIn('HEADER line 0', result)
        self.assertIn('FOOTER line 29', result)
        self.assertIn('fatal error occurred', result)


class TestExtractExceptions(unittest.TestCase):

    def _extract(self, policy):
        from clients.konflux_client import KonfluxClient
        return KonfluxClient.extract_exceptions(policy)

    def test_permanent_exclusion(self):
        policy = {
            'metadata': {'name': 'test-policy'},
            'spec': {'sources': [{
                'config': {'exclude': ['rule-a', 'rule-b']},
            }]},
        }
        results = self._extract(policy)
        self.assertEqual(len(results), 2)
        self.assertTrue(results[0]['permanent'])
        self.assertEqual(results[0]['value'], 'rule-a')
        self.assertIsNone(results[0]['days_left'])

    def test_volatile_exclusion_with_expiry(self):
        policy = {
            'metadata': {'name': 'test-policy'},
            'spec': {'sources': [{
                'config': {},
                'volatileConfig': {'exclude': [{
                    'value': 'rule-c',
                    'effectiveUntil': '2025-12-31T23:59:59Z',
                    'reference': 'JIRA-123',
                }]},
            }]},
        }
        results = self._extract(policy)
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0]['permanent'])
        self.assertEqual(results[0]['value'], 'rule-c')
        self.assertEqual(results[0]['reference'], 'JIRA-123')
        self.assertIsNotNone(results[0]['days_left'])

    def test_empty_policy(self):
        results = self._extract({'metadata': {'name': 'empty'}, 'spec': {}})
        self.assertEqual(results, [])


if __name__ == '__main__':
    unittest.main()
