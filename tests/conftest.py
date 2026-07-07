import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

FIXTURES_DIR = Path(__file__).parent / 'fixtures'


def _load_fixture(name):
    with open(FIXTURES_DIR / name) as f:
        return json.load(f)


@pytest.fixture
def sample_pipelinerun():
    return _load_fixture('pipelinerun.json')


@pytest.fixture
def sample_release():
    return _load_fixture('release.json')


@pytest.fixture
def sample_snapshot():
    return _load_fixture('snapshot.json')


@pytest.fixture
def sample_component():
    return _load_fixture('component.json')


@pytest.fixture
def sample_conforma_report():
    return _load_fixture('conforma_report.json')


@pytest.fixture
def mock_db():
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cursor)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = None
    cursor.rowcount = 0
    return conn, cursor


@pytest.fixture
def mock_k8s(sample_component, sample_pipelinerun):
    k8s = MagicMock()
    k8s.list_components.return_value = [
        {
            'name': sample_component['metadata']['name'],
            'application': 'rhoai-v3-5-ea-2',
            'git_url': 'https://github.com/opendatahub-io/odh-model-controller',
            'revision': 'rhoai-v3.5',
        }
    ]
    k8s.list_recent_pipelineruns.return_value = [
        {
            'name': sample_pipelinerun['metadata']['name'],
            'component': 'odh-model-controller-v3-5',
            'status': 'Succeeded',
            'start_time': '2026-06-30T10:00:00Z',
            'completion_time': '2026-06-30T10:15:00Z',
        }
    ]
    k8s.list_pac_repositories.return_value = []
    return k8s


@pytest.fixture
def mock_kfx(sample_snapshot, sample_release):
    kfx = MagicMock()
    kfx.get_snapshots.return_value = [sample_snapshot]
    kfx.get_releases.return_value = [sample_release]
    kfx.get_release_plan_admissions.return_value = []
    return kfx


@pytest.fixture
def mock_llm():
    llm = MagicMock()
    llm.create_message.return_value = {
        'content': [{'type': 'text', 'text': '{"result": "test"}'}],
        'usage': {'input_tokens': 100, 'output_tokens': 50},
        'model': 'claude-sonnet-4-5-20250929',
    }
    llm.model = 'claude-sonnet-4-5-20250929'
    llm.cost_per_1k_input = 0.003
    llm.cost_per_1k_output = 0.015
    return llm


@pytest.fixture
def api_client():
    os.environ.setdefault('DATABASE_URL', 'postgresql://test:test@localhost/test')
    with patch.dict(os.environ, {'DATABASE_URL': 'postgresql://test:test@localhost/test'}):
        try:
            from fastapi.testclient import TestClient

            from api.routes import create_app
            app = create_app()
            return TestClient(app)
        except ImportError:
            pytest.skip('fastapi not installed')
