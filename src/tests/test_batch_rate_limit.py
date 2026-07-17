"""Tests for batch analysis rate limiting."""

import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

sys.modules.setdefault('logger', MagicMock(setup_logger=MagicMock(return_value=MagicMock())))
sys.modules.setdefault('repositories.connection', MagicMock())
sys.modules.setdefault('psycopg2', MagicMock())
sys.modules.setdefault('psycopg2.pool', MagicMock())


def test_max_per_run_respects_env_override():
    with patch.dict(os.environ, {'AUTO_ANALYZE_MAX_PER_CYCLE': '3'}):
        with patch('services.batch_analysis_service.BuildFailureAnalyzer'):
            from services.batch_analysis_service import BatchAnalysisService
            config = MagicMock()
            config.batch_analysis = MagicMock(max_per_run=20, enabled=True)
            config.llm_provider = 'vertex_ai'
            svc = BatchAnalysisService(config)
            assert svc.max_per_run <= 3


def test_max_per_run_uses_config_when_no_env():
    env = os.environ.copy()
    env.pop('AUTO_ANALYZE_MAX_PER_CYCLE', None)
    with patch.dict(os.environ, env, clear=True):
        with patch('services.batch_analysis_service.BuildFailureAnalyzer'):
            from services.batch_analysis_service import BatchAnalysisService
            config = MagicMock()
            config.batch_analysis = MagicMock(max_per_run=20, enabled=True)
            config.llm_provider = 'vertex_ai'
            svc = BatchAnalysisService(config)
            assert svc.max_per_run == 20
