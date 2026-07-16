"""Tests for InfraSignalSource enrichment source."""

import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

sys.modules.setdefault('logger', MagicMock(setup_logger=MagicMock(return_value=MagicMock())))
sys.modules.setdefault('repositories.connection', MagicMock())
sys.modules.setdefault('psycopg2', MagicMock())
sys.modules.setdefault('psycopg2.pool', MagicMock())

from enrichment.sources.infra_signals import InfraSignalSource


def _make_config():
    config = MagicMock()
    config.k8s.application_name = 'rhoai-v3-5'
    config.k8s.namespace = 'rhoai-v3-5-tenant'
    return config


class TestInfraSignalSourceProperties:

    def test_source_name(self):
        src = InfraSignalSource(_make_config())
        assert src.source_name() == 'infra_signals'

    def test_requires_external_api_is_false(self):
        src = InfraSignalSource(_make_config())
        assert src.requires_external_api is False

    def test_timeout_seconds(self):
        src = InfraSignalSource(_make_config())
        assert src.timeout_seconds == 5


class TestInfraSignalPatternMatching:

    def test_exit_code_137_in_logs(self):
        src = InfraSignalSource(_make_config())
        result = src.fetch({
            'component_name': 'pytorch-rocm-v3-5',
            'error_type': 'BuildFailed',
            'build_logs': 'step-prepare exited with code 137\nno further output',
            'failed_step_name': 'step-prepare',
        })
        assert result is not None
        signals = result['infra_signals']['signals']
        assert len(signals) == 1
        assert signals[0]['signal'] == 'oom_or_sigkill'
        assert signals[0]['rebuild_candidate'] is True
        assert '137' in signals[0]['evidence']

    def test_exit_code_255_in_logs(self):
        src = InfraSignalSource(_make_config())
        result = src.fetch({
            'component_name': 'odh-operator-v3-5',
            'error_type': 'BuildFailed',
            'build_logs': 'step-prepare-sboms exited with code 255',
            'failed_step_name': 'step-prepare-sboms',
        })
        assert result is not None
        signals = result['infra_signals']['signals']
        assert signals[0]['signal'] == 'fatal_unhandled'
        assert signals[0]['rebuild_candidate'] is False

    def test_container_status_unknown(self):
        src = InfraSignalSource(_make_config())
        result = src.fetch({
            'component_name': 'test-component',
            'error_type': 'ContainerStatusUnknown',
            'build_logs': '',
            'failed_step_name': '',
        })
        assert result is not None
        assert result['infra_signals']['signals'][0]['signal'] == 'pod_lost'

    def test_failed_to_provision_host(self):
        src = InfraSignalSource(_make_config())
        result = src.fetch({
            'component_name': 'minimal-cuda-v3-5',
            'error_type': 'BuildFailed',
            'build_logs': 'Error allocating host for arm64 build: no capacity',
            'failed_step_name': 'step-build',
        })
        assert result is not None
        assert result['infra_signals']['signals'][0]['signal'] == 'host_provision_failure'
        assert result['infra_signals']['has_rebuild_candidates'] is True

    def test_disk_pressure(self):
        src = InfraSignalSource(_make_config())
        result = src.fetch({
            'component_name': 'test',
            'error_type': 'BuildFailed',
            'build_logs': 'The node was low on resource: ephemeral-storage',
            'failed_step_name': '',
        })
        assert result is not None
        assert result['infra_signals']['signals'][0]['signal'] == 'disk_pressure'

    def test_rhsm_transient(self):
        src = InfraSignalSource(_make_config())
        result = src.fetch({
            'component_name': 'test',
            'error_type': 'BuildFailed',
            'build_logs': 'subscription-manager repos: exit status 70\nretry failed',
            'failed_step_name': '',
        })
        assert result is not None
        assert result['infra_signals']['signals'][0]['signal'] == 'rhsm_transient'
        assert result['infra_signals']['has_rebuild_candidates'] is True

    def test_pipeline_timeout(self):
        src = InfraSignalSource(_make_config())
        result = src.fetch({
            'component_name': 'test',
            'error_type': 'PipelineRunTimeout',
            'build_logs': 'PipelineRun timed out',
            'failed_step_name': '',
        })
        assert result is not None
        assert result['infra_signals']['signals'][0]['signal'] == 'pipeline_timeout'
        assert result['infra_signals']['signals'][0]['rebuild_candidate'] is False

    def test_no_match_returns_none(self):
        src = InfraSignalSource(_make_config())
        result = src.fetch({
            'component_name': 'test',
            'error_type': 'BuildFailed',
            'build_logs': 'ModuleNotFoundError: No module named "foo"',
            'failed_step_name': '',
        })
        assert result is None

    def test_multiple_signals(self):
        src = InfraSignalSource(_make_config())
        result = src.fetch({
            'component_name': 'test',
            'error_type': 'BuildFailed',
            'build_logs': ('DiskPressure detected on node\n'
                           'step-build exited with code 137'),
            'failed_step_name': '',
        })
        assert result is not None
        assert result['infra_signals']['signal_count'] == 2

    def test_empty_failure_returns_none(self):
        src = InfraSignalSource(_make_config())
        result = src.fetch({})
        assert result is None

    def test_readonly_filesystem(self):
        src = InfraSignalSource(_make_config())
        result = src.fetch({
            'component_name': 'test',
            'error_type': 'BuildFailed',
            'build_logs': 'OSError: [Errno 30] read-only file system: /tmp/build',
            'failed_step_name': '',
        })
        assert result is not None
        assert result['infra_signals']['signals'][0]['signal'] == 'readonly_filesystem'

    def test_memory_pressure(self):
        src = InfraSignalSource(_make_config())
        result = src.fetch({
            'component_name': 'test',
            'error_type': 'BuildFailed',
            'build_logs': 'node condition MemoryPressure is true',
            'failed_step_name': '',
        })
        assert result is not None
        assert result['infra_signals']['signals'][0]['signal'] == 'memory_pressure'

    def test_init_container_failure(self):
        src = InfraSignalSource(_make_config())
        result = src.fetch({
            'component_name': 'test',
            'error_type': 'BuildFailed',
            'build_logs': 'init container failed to start: prepare step error',
            'failed_step_name': '',
        })
        assert result is not None
        assert result['infra_signals']['signals'][0]['signal'] == 'init_container_failure'
        assert result['infra_signals']['signals'][0]['rebuild_candidate'] is False
