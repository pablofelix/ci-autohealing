"""Integration tests for structural failure detection in build_failure_analyzer."""

import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

sys.modules.setdefault('logger', MagicMock(setup_logger=MagicMock(return_value=MagicMock())))
sys.modules.setdefault('repositories.connection', MagicMock())
sys.modules.setdefault('psycopg2', MagicMock())
sys.modules.setdefault('psycopg2.pool', MagicMock())


class TestEnsureEnrichmentRegistersComponentHealth:

    @patch('enrichment.enrichment_orchestrator.EnrichmentOrchestrator', autospec=True)
    def test_registers_component_health_source(self, mock_orch_class):
        from analyzers.build_failure_analyzer import BuildFailureAnalyzer

        mock_orch = MagicMock()
        mock_result = MagicMock()
        mock_result.success = False
        mock_orch.enrich_failure.return_value = mock_result
        mock_orch_class.return_value = mock_orch

        config = MagicMock()
        config.k8s.application_name = 'rhoai-v3-5'
        config.k8s.namespace = 'rhoai-v3-5-tenant'
        config.llm.provider = 'anthropic'
        config.llm.model_name = 'claude-sonnet-4-5-20250929'
        db = MagicMock()
        db.connection.return_value.__enter__.return_value = MagicMock()

        analyzer = BuildFailureAnalyzer(config, db, llm=MagicMock())
        analyzer._ensure_enrichment({'id': 1, 'component_name': 'test'})

        registered_names = [
            call.args[0].source_name()
            for call in mock_orch.register_source.call_args_list
        ]
        assert 'component_health' in registered_names


class TestFormatComponentHealth:

    def _get_analyzer(self):
        from analyzers.build_failure_analyzer import BuildFailureAnalyzer
        config = MagicMock()
        config.k8s.application_name = 'rhoai-v3-5'
        config.k8s.namespace = 'rhoai-v3-5-tenant'
        config.llm.provider = 'anthropic'
        config.llm.model_name = 'claude-sonnet-4-5-20250929'
        db = MagicMock()
        db.connection.return_value.__enter__.return_value = MagicMock()
        return BuildFailureAnalyzer(config, db, llm=MagicMock())

    def test_structural_failure_in_prompt(self):
        analyzer = self._get_analyzer()
        enriched = {
            'component_health': {
                'has_ever_succeeded': False,
                'total_builds': 5,
                'successful_builds': 0,
                'failed_builds': 5,
                'consecutive_failures': 5,
                'last_success_date': None,
                'last_success_sha': None,
            }
        }

        result = analyzer._format_commit_context(None, enriched)
        assert 'STRUCTURAL FAILURE' in result
        assert 'NEVER built successfully' in result
        assert 'do not recommend rebuild' in result.lower() or 'do NOT recommend rebuild' in result

    def test_healthy_component_in_prompt(self):
        analyzer = self._get_analyzer()
        enriched = {
            'component_health': {
                'has_ever_succeeded': True,
                'total_builds': 20,
                'successful_builds': 18,
                'failed_builds': 2,
                'consecutive_failures': 1,
                'last_success_date': '2026-07-10T10:00:00Z',
                'last_success_sha': 'abc123',
            }
        }

        result = analyzer._format_commit_context(None, enriched)
        assert 'STRUCTURAL FAILURE' not in result
        assert 'Has ever built successfully: Yes' in result
        assert 'abc123' in result

    def test_no_component_health_data(self):
        analyzer = self._get_analyzer()
        enriched = {}

        result = analyzer._format_commit_context(None, enriched)
        assert 'Component Build Health' not in result

    def test_none_enriched_context(self):
        analyzer = self._get_analyzer()
        result = analyzer._format_commit_context(None, None)
        assert 'Component Build Health' not in result


class TestBuildAnalysisPromptStructural:

    def _get_analyzer(self):
        from analyzers.build_failure_analyzer import BuildFailureAnalyzer
        config = MagicMock()
        config.k8s.application_name = 'rhoai-v3-5'
        config.k8s.namespace = 'rhoai-v3-5-tenant'
        config.llm.provider = 'anthropic'
        config.llm.model_name = 'claude-sonnet-4-5-20250929'
        db = MagicMock()
        db.connection.return_value.__enter__.return_value = MagicMock()
        analyzer = BuildFailureAnalyzer(config, db, llm=MagicMock())
        analyzer.pattern_service = MagicMock()
        analyzer.pattern_service.get_matches_for_prompt.return_value = ''
        return analyzer

    def test_structural_warning_in_full_prompt(self):
        analyzer = self._get_analyzer()
        failure = {
            'id': 1,
            'component_name': 'odh-trustyai-service-v3-5',
            'error_message': 'curl: (6) Could not resolve host: cdn-ubi.redhat.com',
            'error_type': 'Build Error',
            'build_logs': 'Step build: curl failed',
            'failed_step': 'build-container',
            'commit_sha': 'abc123',
            'repository_url': 'https://github.com/org/repo',
            'branch': 'rhoai-v3.5',
            'enriched_context': {
                'component_health': {
                    'has_ever_succeeded': False,
                    'total_builds': 8,
                    'successful_builds': 0,
                    'failed_builds': 8,
                    'consecutive_failures': 8,
                    'last_success_date': None,
                    'last_success_sha': None,
                }
            },
        }

        _, user_prompt = analyzer.build_analysis_prompt(failure)
        assert 'STRUCTURAL FAILURE' in user_prompt
