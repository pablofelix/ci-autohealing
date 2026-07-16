"""Infra signal pattern matching source.

Detects known infrastructure failure patterns in build logs and error types.
No external API calls — works entirely from data already in the failure dict.
"""

import re
from typing import Any, Dict, List, Optional

from enrichment.context_source import ContextSource
from logger import setup_logger

logger = setup_logger(__name__)

INFRA_PATTERNS = [
    {
        'pattern': re.compile(r'exit(?:ed with)? code 137', re.IGNORECASE),
        'fields': ['build_logs', 'error_message'],
        'signal': 'oom_or_sigkill',
        'description': 'Process killed by SIGKILL (exit code 137), likely OOM',
        'rebuild_candidate': True,
    },
    {
        'pattern': re.compile(r'exit(?:ed with)? code 255', re.IGNORECASE),
        'fields': ['build_logs', 'error_message'],
        'signal': 'fatal_unhandled',
        'description': 'Fatal unhandled error (exit code 255), needs investigation',
        'rebuild_candidate': False,
    },
    {
        'pattern': re.compile(r'ContainerStatusUnknown', re.IGNORECASE),
        'fields': ['error_type', 'build_logs', 'error_message'],
        'signal': 'pod_lost',
        'description': 'Pod lost contact with the node (ContainerStatusUnknown)',
        'rebuild_candidate': True,
    },
    {
        'pattern': re.compile(r'failed to provision host|Error allocating host', re.IGNORECASE),
        'fields': ['build_logs', 'error_message'],
        'signal': 'host_provision_failure',
        'description': 'Build host provisioning failed (capacity or scheduling issue)',
        'rebuild_candidate': True,
    },
    {
        'pattern': re.compile(r'DiskPressure|ephemeral-storage', re.IGNORECASE),
        'fields': ['build_logs', 'error_message'],
        'signal': 'disk_pressure',
        'description': 'Node under disk pressure or low on ephemeral storage',
        'rebuild_candidate': True,
    },
    {
        'pattern': re.compile(r'MemoryPressure', re.IGNORECASE),
        'fields': ['build_logs', 'error_message'],
        'signal': 'memory_pressure',
        'description': 'Node under memory pressure',
        'rebuild_candidate': True,
    },
    {
        'pattern': re.compile(r'subscription-manager.*exit status 70', re.IGNORECASE),
        'fields': ['build_logs'],
        'signal': 'rhsm_transient',
        'description': 'RHSM subscription-manager transient failure',
        'rebuild_candidate': True,
    },
    {
        'pattern': re.compile(r'read-only file system', re.IGNORECASE),
        'fields': ['build_logs', 'error_message'],
        'signal': 'readonly_filesystem',
        'description': 'Filesystem became read-only (node or volume issue)',
        'rebuild_candidate': True,
    },
    {
        'pattern': re.compile(r'PipelineRunTimeout|PipelineRun.*timed? out', re.IGNORECASE),
        'fields': ['error_type', 'build_logs', 'error_message'],
        'signal': 'pipeline_timeout',
        'description': 'PipelineRun exceeded its timeout',
        'rebuild_candidate': False,
    },
    {
        'pattern': re.compile(r'init container failed.*prepare', re.IGNORECASE),
        'fields': ['build_logs', 'error_message'],
        'signal': 'init_container_failure',
        'description': 'Init container failed during preparation step',
        'rebuild_candidate': False,
    },
]


class InfraSignalSource(ContextSource):
    """Detects infrastructure failure signals from existing failure data."""

    def fetch(self, failure: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            signals = self._match_patterns(failure)
            if not signals:
                return None

            has_rebuild = any(s['rebuild_candidate'] for s in signals)
            return {
                'infra_signals': {
                    'signals': signals,
                    'signal_count': len(signals),
                    'has_rebuild_candidates': has_rebuild,
                }
            }
        except Exception as e:
            logger.warning("InfraSignalSource error: %s", e)
            return None

    def _match_patterns(self, failure: Dict[str, Any]) -> List[Dict[str, Any]]:
        matched = []
        seen_signals = set()

        for pat_def in INFRA_PATTERNS:
            if pat_def['signal'] in seen_signals:
                continue

            for field in pat_def['fields']:
                text = failure.get(field) or ''
                m = pat_def['pattern'].search(text)
                if m:
                    matched.append({
                        'signal': pat_def['signal'],
                        'description': pat_def['description'],
                        'evidence': m.group(0),
                        'rebuild_candidate': pat_def['rebuild_candidate'],
                    })
                    seen_signals.add(pat_def['signal'])
                    break

        return matched

    def source_name(self) -> str:
        return 'infra_signals'

    @property
    def timeout_seconds(self) -> int:
        return 5

    @property
    def requires_external_api(self) -> bool:
        return False
