"""Tests for infrastructure signals integration in BuildFailureAnalyzer."""

import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

sys.modules.setdefault('logger', MagicMock(setup_logger=MagicMock(return_value=MagicMock())))
sys.modules.setdefault('repositories.connection', MagicMock())
sys.modules.setdefault('psycopg2', MagicMock())
sys.modules.setdefault('psycopg2.pool', MagicMock())

# Mock all heavy deps before importing analyzer
for mod in ['anthropic', 'langfuse', 'langfuse.decorators', 'neo4j',
            'knowledge.graph_provider', 'prompt_loader']:
    sys.modules.setdefault(mod, MagicMock())

# Mock load_prompt to return a simple string
sys.modules['prompt_loader'].load_prompt = MagicMock(return_value='system prompt')

from analyzers.build_failure_analyzer import BuildFailureAnalyzer


class TestInfraSignalsPromptFormatting:

    def _make_analyzer(self):
        config = MagicMock()
        config.k8s.namespace = 'rhoai-v3-5-tenant'
        config.k8s.application_name = 'rhoai-v3-5'
        config.llm = MagicMock()
        config.llm.provider = 'anthropic'
        db = MagicMock()
        analyzer = BuildFailureAnalyzer(config, db)
        return analyzer

    def test_infra_signals_in_prompt(self):
        analyzer = self._make_analyzer()
        enriched = {
            'infra_signals': {
                'signals': [
                    {
                        'signal': 'oom_or_sigkill',
                        'description': 'Process killed by SIGKILL (exit code 137), likely OOM',
                        'evidence': 'exited with code 137',
                        'rebuild_candidate': True,
                    }
                ],
                'signal_count': 1,
                'has_rebuild_candidates': True,
            }
        }
        result = analyzer._format_commit_context(None, enriched)
        assert 'Infrastructure Signals' in result
        assert 'oom_or_sigkill' in result
        assert 'Rebuild candidate: Yes' in result

    def test_kubernetes_events_in_prompt(self):
        analyzer = self._make_analyzer()
        enriched = {
            'kubernetes_events': {
                'events': [
                    {
                        'reason': 'Evicted',
                        'message': 'low on ephemeral-storage',
                        'timestamp': '2026-07-16T10:21:00+00:00',
                        'involved_object': 'my-pod',
                    }
                ],
                'event_count': 1,
                'has_infra_events': True,
            }
        }
        result = analyzer._format_commit_context(None, enriched)
        assert 'Infrastructure Signals' in result
        assert 'Evicted' in result
        assert 'ephemeral-storage' in result

    def test_no_infra_signals_no_section(self):
        analyzer = self._make_analyzer()
        enriched = {
            'component_health': {'total_builds': 5},
        }
        result = analyzer._format_commit_context(None, enriched)
        assert 'Infrastructure Signals' not in result

    def test_both_sources_combined(self):
        analyzer = self._make_analyzer()
        enriched = {
            'infra_signals': {
                'signals': [
                    {
                        'signal': 'oom_or_sigkill',
                        'description': 'OOM',
                        'evidence': 'code 137',
                        'rebuild_candidate': True,
                    }
                ],
                'signal_count': 1,
                'has_rebuild_candidates': True,
            },
            'kubernetes_events': {
                'events': [
                    {
                        'reason': 'OOMKilling',
                        'message': 'memory limit exceeded',
                        'timestamp': '2026-07-16T10:21:00+00:00',
                        'involved_object': 'pod',
                    }
                ],
                'event_count': 1,
                'has_infra_events': True,
            }
        }
        result = analyzer._format_commit_context(None, enriched)
        assert 'Kubernetes Events' in result
        assert 'Log Pattern Signals' in result
