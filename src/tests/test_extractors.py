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


class TestExtractDependencyUpdate(unittest.TestCase):

    def _extract(self, duc):
        from clients.konflux_client import KonfluxClient
        return KonfluxClient.extract_dependency_update(duc)

    def test_basic_update(self):
        duc = {
            'metadata': {
                'name': 'duc-123',
                'creationTimestamp': '2025-05-25T10:00:00Z',
                'labels': {'appstudio.openshift.io/component': 'vllm'},
            },
            'spec': {
                'packageName': 'golang.org/x/net',
                'currentVersion': 'v0.17.0',
                'newVersion': 'v0.20.0',
            },
            'status': {
                'pullRequestUrl': 'https://github.com/org/repo/pull/456',
                'merged': True,
            },
        }
        result = self._extract(duc)
        self.assertEqual(result['component'], 'vllm')
        self.assertEqual(result['package'], 'golang.org/x/net')
        self.assertEqual(result['from_version'], 'v0.17.0')
        self.assertEqual(result['to_version'], 'v0.20.0')
        self.assertTrue(result['merged'])
        self.assertIn('456', result['pr_url'])

    def test_empty_duc(self):
        result = self._extract({})
        self.assertEqual(result['component'], '')
        self.assertEqual(result['package'], '')
        self.assertFalse(result['merged'])

    def test_pending_update(self):
        duc = {
            'metadata': {'name': 'duc-456', 'labels': {}},
            'spec': {'packageName': 'pip', 'currentVersion': '23.0', 'newVersion': '24.0'},
            'status': {'merged': False},
        }
        result = self._extract(duc)
        self.assertFalse(result['merged'])
        self.assertEqual(result['package'], 'pip')


class TestRegistryClientParseImageRef(unittest.TestCase):

    def _parse(self, url):
        from clients.registry_client import RegistryClient
        return RegistryClient.parse_image_ref(url)

    def test_tag_format(self):
        registry, repo, tag = self._parse('quay.io/rh-osbs/vllm:v3.5')
        self.assertEqual(registry, 'quay.io')
        self.assertEqual(repo, 'rh-osbs/vllm')
        self.assertEqual(tag, 'v3.5')

    def test_digest_format(self):
        registry, repo, digest = self._parse('quay.io/rh/image@sha256:abc123')
        self.assertEqual(registry, 'quay.io')
        self.assertEqual(repo, 'rh/image')
        self.assertEqual(digest, 'sha256:abc123')

    def test_with_https_prefix(self):
        registry, repo, tag = self._parse('https://quay.io/org/image:latest')
        self.assertEqual(registry, 'quay.io')
        self.assertEqual(repo, 'org/image')
        self.assertEqual(tag, 'latest')

    def test_no_tag(self):
        registry, repo, tag = self._parse('quay.io/org/image')
        self.assertEqual(registry, 'quay.io')
        self.assertEqual(repo, 'org/image')
        self.assertEqual(tag, 'latest')


class TestRegistryClientParseSarif(unittest.TestCase):

    def _parse(self, data):
        from clients.registry_client import RegistryClient
        import json
        return RegistryClient._parse_sarif(json.dumps(data).encode())

    def test_basic_sarif(self):
        sarif = {
            'runs': [{
                'tool': {'driver': {'rules': [
                    {'id': 'CVE-2024-1234', 'properties': {
                        'package': 'openssl', 'fixed_version': '3.0.14',
                    }},
                ]}},
                'results': [{
                    'ruleId': 'CVE-2024-1234',
                    'level': 'error',
                    'message': {'text': 'Critical vulnerability in openssl'},
                }],
            }],
        }
        results = self._parse(sarif)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['ruleId'], 'CVE-2024-1234')
        self.assertEqual(results[0]['level'], 'error')
        self.assertEqual(results[0]['package'], 'openssl')
        self.assertEqual(results[0]['fix_version'], '3.0.14')

    def test_multiple_results(self):
        sarif = {
            'runs': [{
                'tool': {'driver': {'rules': []}},
                'results': [
                    {'ruleId': 'CVE-1', 'level': 'error', 'message': {'text': 'a'}},
                    {'ruleId': 'CVE-2', 'level': 'warning', 'message': {'text': 'b'}},
                    {'ruleId': 'CVE-3', 'level': 'note', 'message': {'text': 'c'}},
                ],
            }],
        }
        results = self._parse(sarif)
        self.assertEqual(len(results), 3)

    def test_empty_sarif(self):
        results = self._parse({'runs': []})
        self.assertEqual(results, [])

    def test_invalid_json(self):
        from clients.registry_client import RegistryClient
        results = RegistryClient._parse_sarif(b'not json')
        self.assertEqual(results, [])


class TestRegistryClientFormatSarifSummary(unittest.TestCase):

    def _format(self, results, max_chars=2000):
        from clients.registry_client import RegistryClient
        return RegistryClient.format_sarif_summary(results, max_chars)

    def test_basic_formatting(self):
        results = [
            {'ruleId': 'CVE-1', 'level': 'error', 'message': 'crit vuln',
             'package': 'openssl', 'fix_version': '3.0.14'},
            {'ruleId': 'CVE-2', 'level': 'warning', 'message': 'high vuln',
             'package': 'curl', 'fix_version': '8.5.0'},
        ]
        text = self._format(results)
        self.assertIn('=== Structured Scan Results (SARIF) ===', text)
        self.assertIn('Critical: 1', text)
        self.assertIn('High: 1', text)
        self.assertIn('CVE-1', text)
        self.assertIn('openssl', text)
        self.assertIn('fix: 3.0.14', text)

    def test_empty_results(self):
        text = self._format([])
        self.assertEqual(text, '')

    def test_truncation(self):
        results = [
            {'ruleId': 'CVE-{}'.format(i), 'level': 'warning', 'message': 'x' * 100,
             'package': 'pkg', 'fix_version': ''}
            for i in range(50)
        ]
        text = self._format(results, max_chars=500)
        self.assertLessEqual(len(text), 520)


class TestExtractLogsFromTarball(unittest.TestCase):

    def test_valid_tarball(self):
        import io
        import tarfile
        from clients.registry_client import RegistryClient

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode='w:gz') as tar:
            content = b'--- LOGS FOR build-task ---\nbuilding...\n--- LOGS FOR test-task ---\ntesting...\n'
            info = tarfile.TarInfo(name='all-logs.txt')
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
        buf.seek(0)

        result = RegistryClient._extract_logs_from_tarball(buf.read())
        self.assertIn('LOGS FOR build-task', result)
        self.assertIn('LOGS FOR test-task', result)

    def test_invalid_data(self):
        from clients.registry_client import RegistryClient
        result = RegistryClient._extract_logs_from_tarball(b'not a tarball')
        self.assertEqual(result, '')


if __name__ == '__main__':
    unittest.main()
