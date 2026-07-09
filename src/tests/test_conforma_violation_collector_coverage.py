"""Comprehensive tests for ConformaViolationCollector.

Covers constructor, collection logic, log extraction, DB persistence,
history queries, resolution flows, and the full run() orchestration.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from unittest.mock import MagicMock, patch

import pytest

from collectors.conforma_violation_collector import ConformaViolationCollector
from config import CollectorConfig, DatabaseConfig, KubernetesConfig

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def config():
    return CollectorConfig(
        db=DatabaseConfig(
            host="localhost", port=5432, user="test",
            password="test", database="testdb"
        ),
        k8s=KubernetesConfig(
            namespace="test-ns", application_name="test-app",
            kubearchive_api_url="https://kubearchive.example.com"
        )
    )


@pytest.fixture
def collector(config):
    return ConformaViolationCollector(
        config,
        db=MagicMock(),
        conforma_repo=MagicMock(),
        kubearchive=MagicMock(),
        k8s=MagicMock(),
        tekton_results=MagicMock(),
    )


def _make_pr(component, scenario, timestamp, status,
             name="pr-1", uid="uid-1", pipeline_type="test",
             conditions=None):
    """Helper to build a minimal PipelineRun dict."""
    if conditions is None:
        conditions = [{"status": status}]
    return {
        "metadata": {
            "name": name,
            "uid": uid,
            "creationTimestamp": timestamp,
            "labels": {
                "appstudio.openshift.io/component": component,
                "test.appstudio.openshift.io/scenario": scenario,
                "pipelines.appstudio.openshift.io/type": pipeline_type,
            },
        },
        "status": {"conditions": conditions},
    }


# ===================================================================
# 1. Constructor
# ===================================================================

class TestConstructor:
    def test_stores_config(self, config):
        c = ConformaViolationCollector(
            config, db=MagicMock(), conforma_repo=MagicMock(),
            kubearchive=MagicMock(), k8s=MagicMock(), tekton_results=MagicMock(),
        )
        assert c.config is config

    def test_uses_injected_deps(self, config):
        repo = MagicMock()
        ka = MagicMock()
        k8s = MagicMock()
        tr = MagicMock()
        c = ConformaViolationCollector(
            config, db=MagicMock(), conforma_repo=repo,
            kubearchive=ka, k8s=k8s, tekton_results=tr,
        )
        assert c.conforma_repo is repo
        assert c.kubearchive is ka
        assert c.k8s is k8s
        assert c.tekton_results is tr


# ===================================================================
# 2. _collect_conforma_latest
# ===================================================================

class TestCollectConformaLatest:
    def test_empty_list(self, collector):
        assert collector._collect_conforma_latest([]) == {}

    def test_single_conforma_pr(self, collector):
        pr = _make_pr("comp-a", "conforma-stage", "2026-06-01T00:00:00Z", "False")
        result = collector._collect_conforma_latest([pr])
        assert ("comp-a", "conforma-stage") in result
        entry = result[("comp-a", "conforma-stage")]
        assert entry["component"] == "comp-a"
        assert entry["scenario"] == "conforma-stage"
        assert entry["status"] == "False"
        assert entry["pr_data"] is pr

    def test_multiple_components_and_scenarios(self, collector):
        prs = [
            _make_pr("comp-a", "conforma-stage", "2026-06-01T00:00:00Z", "False"),
            _make_pr("comp-b", "conforma-prod", "2026-06-01T00:00:00Z", "True"),
        ]
        result = collector._collect_conforma_latest(prs)
        assert len(result) == 2
        assert ("comp-a", "conforma-stage") in result
        assert ("comp-b", "conforma-prod") in result

    def test_non_conforma_scenario_filtered(self, collector):
        pr = _make_pr("comp-a", "integration-test", "2026-06-01T00:00:00Z", "False")
        assert collector._collect_conforma_latest([pr]) == {}

    def test_non_test_pipeline_type_filtered(self, collector):
        pr = _make_pr("comp-a", "conforma-stage", "2026-06-01T00:00:00Z", "False",
                       pipeline_type="build")
        assert collector._collect_conforma_latest([pr]) == {}

    def test_unknown_status_filtered(self, collector):
        pr = _make_pr("comp-a", "conforma-stage", "2026-06-01T00:00:00Z", "Unknown")
        assert collector._collect_conforma_latest([pr]) == {}

    def test_no_conditions_filtered(self, collector):
        pr = _make_pr("comp-a", "conforma-stage", "2026-06-01T00:00:00Z", "False",
                       conditions=[])
        # Override status.conditions to be empty
        pr["status"]["conditions"] = []
        assert collector._collect_conforma_latest([pr]) == {}

    def test_latest_wins_by_timestamp(self, collector):
        older = _make_pr("comp-a", "conforma-stage", "2026-06-01T00:00:00Z", "True",
                         name="old-pr", uid="old-uid")
        newer = _make_pr("comp-a", "conforma-stage", "2026-06-02T00:00:00Z", "False",
                         name="new-pr", uid="new-uid")
        result = collector._collect_conforma_latest([older, newer])
        assert result[("comp-a", "conforma-stage")]["pr_name"] == "new-pr"
        assert result[("comp-a", "conforma-stage")]["status"] == "False"

    def test_no_component_label_filtered(self, collector):
        pr = _make_pr("comp-a", "conforma-stage", "2026-06-01T00:00:00Z", "False")
        del pr["metadata"]["labels"]["appstudio.openshift.io/component"]
        assert collector._collect_conforma_latest([pr]) == {}


# ===================================================================
# 3. get_conforma_pipelineruns
# ===================================================================

class TestGetConformaPipelineruns:
    @patch("collectors.conforma_violation_collector.query_pipelineruns")
    def test_happy_path(self, mock_query, collector):
        pr = _make_pr("comp-a", "conforma-stage", "2026-06-01T00:00:00Z", "False")
        mock_query.return_value = [pr]
        collector.tekton_results.query_conforma_records.return_value = []

        result = collector.get_conforma_pipelineruns()
        assert ("comp-a", "conforma-stage") in result
        mock_query.assert_called_once()

    @patch("collectors.conforma_violation_collector.query_pipelineruns")
    def test_tr_adds_newer_data(self, mock_query, collector):
        old_pr = _make_pr("comp-a", "conforma-stage", "2026-06-01T00:00:00Z", "True",
                          name="old-pr")
        mock_query.return_value = [old_pr]

        new_pr = _make_pr("comp-a", "conforma-stage", "2026-06-02T00:00:00Z", "False",
                          name="new-pr")
        collector.tekton_results.query_conforma_records.return_value = [new_pr]

        result = collector.get_conforma_pipelineruns()
        assert result[("comp-a", "conforma-stage")]["pr_name"] == "new-pr"

    @patch("collectors.conforma_violation_collector.query_pipelineruns")
    def test_tr_exception_does_not_crash(self, mock_query, collector):
        pr = _make_pr("comp-a", "conforma-stage", "2026-06-01T00:00:00Z", "False")
        mock_query.return_value = [pr]
        collector.tekton_results.query_conforma_records.side_effect = RuntimeError("boom")

        result = collector.get_conforma_pipelineruns()
        assert ("comp-a", "conforma-stage") in result


# ===================================================================
# 4. get_verify_taskrun
# ===================================================================

class TestGetVerifyTaskrun:
    @patch("collectors.conforma_violation_collector.extract_verify_taskrun_name")
    def test_delegates_correctly(self, mock_extract, collector):
        pr_data = {"some": "data"}
        mock_extract.return_value = "verify-taskrun-abc"
        result = collector.get_verify_taskrun(pr_data)
        assert result == "verify-taskrun-abc"
        mock_extract.assert_called_once_with(pr_data)


# ===================================================================
# 5. get_step_logs
# ===================================================================

class TestGetStepLogs:
    def test_correct_container_name(self, collector):
        collector.kubearchive.get_pod_logs.return_value = "log content"
        result = collector.get_step_logs("pod-123", "summary")
        collector.kubearchive.get_pod_logs.assert_called_once_with(
            "pod-123", container="step-summary"
        )
        assert result == "log content"

    def test_detailed_report_step(self, collector):
        collector.kubearchive.get_pod_logs.return_value = "details"
        collector.get_step_logs("pod-x", "detailed-report")
        collector.kubearchive.get_pod_logs.assert_called_once_with(
            "pod-x", container="step-detailed-report"
        )


# ===================================================================
# 6. extract_violation_details
# ===================================================================

class TestExtractViolationDetails:
    @patch("collectors.conforma_violation_collector.extract_verify_taskrun_name")
    def test_no_verify_tr(self, mock_extract, collector):
        mock_extract.return_value = None
        result = collector.extract_violation_details("pr-1", {})
        assert result["violations_count"] == 0
        assert result["violation_summary"] == ""
        assert result["violation_details"] is None

    @patch("collectors.conforma_violation_collector.extract_verify_taskrun_name")
    def test_happy_path_pod_available(self, mock_extract, collector):
        mock_extract.return_value = "verify-tr-1"
        collector.kubearchive.get_taskrun.return_value = {
            "status": {"podName": "pod-123"}
        }
        summary_json = json.dumps({"failures": 3, "warnings": 1, "successes": 10})
        detailed_text = "Violation: RPM signatures missing"
        report_json = json.dumps({"components": [{"name": "comp-a"}]})

        def mock_get_pod_logs(pod, container=None):
            if container == "step-summary":
                return summary_json
            elif container == "step-detailed-report":
                return detailed_text
            elif container == "step-report-json":
                return report_json
            return None

        collector.kubearchive.get_pod_logs.side_effect = mock_get_pod_logs

        result = collector.extract_violation_details("pr-1", {"data": True})
        assert result["violations_count"] == 3
        assert result["warnings_count"] == 1
        assert result["successes_count"] == 10
        assert result["violation_summary"] == detailed_text
        assert result["violation_details"] == {"components": [{"name": "comp-a"}]}

    @patch("collectors.conforma_violation_collector.extract_verify_taskrun_name")
    def test_fallback_to_tekton_results(self, mock_extract, collector):
        mock_extract.return_value = "verify-tr-1"
        collector.kubearchive.get_taskrun.return_value = {
            "status": {}  # no podName
        }
        summary_json = json.dumps({"failures": 2, "warnings": 0, "successes": 5})
        combined_logs = (
            "===== TaskRun: ns/verify-tr-1/ Step: summary =====\n"
            f"{summary_json}\n"
            "===== TaskRun: ns/verify-tr-1/ Step: detailed-report =====\n"
            "Some detail\n"
            "===== TaskRun: ns/verify-tr-1/ Step: report-json =====\n"
            '{"items": []}\n'
        )
        collector.tekton_results.get_pipelinerun_logs.return_value = combined_logs

        result = collector.extract_violation_details("pr-1", {})
        assert result["violations_count"] == 2
        assert result["successes_count"] == 5
        assert result["violation_summary"] == "Some detail"
        assert result["violation_details"] == {"items": []}

    @patch("collectors.conforma_violation_collector.extract_verify_taskrun_name")
    def test_summary_json_invalid(self, mock_extract, collector):
        mock_extract.return_value = "verify-tr-1"
        collector.kubearchive.get_taskrun.return_value = {
            "status": {"podName": "pod-123"}
        }

        def mock_logs(pod, container=None):
            if container == "step-summary":
                return "not valid json {"
            return None

        collector.kubearchive.get_pod_logs.side_effect = mock_logs

        result = collector.extract_violation_details("pr-1", {})
        assert result["violations_count"] == 0
        assert result["warnings_count"] == 0

    @patch("collectors.conforma_violation_collector.extract_verify_taskrun_name")
    def test_report_json_invalid(self, mock_extract, collector):
        mock_extract.return_value = "verify-tr-1"
        collector.kubearchive.get_taskrun.return_value = {
            "status": {"podName": "pod-1"}
        }

        def mock_logs(pod, container=None):
            if container == "step-report-json":
                return "broken json"
            return None

        collector.kubearchive.get_pod_logs.side_effect = mock_logs

        result = collector.extract_violation_details("pr-1", {})
        assert result["violation_details"] is None

    @patch("collectors.conforma_violation_collector.extract_verify_taskrun_name")
    def test_no_logs_from_any_source(self, mock_extract, collector):
        mock_extract.return_value = "verify-tr-1"
        collector.kubearchive.get_taskrun.return_value = {"status": {}}
        collector.tekton_results.get_pipelinerun_logs.return_value = None

        result = collector.extract_violation_details("pr-1", {})
        assert result["violations_count"] == 0
        assert result["violation_summary"] == ""
        assert result["violation_details"] is None

    @patch("collectors.conforma_violation_collector.extract_verify_taskrun_name")
    def test_tr_logs_fallback_exception(self, mock_extract, collector):
        mock_extract.return_value = "verify-tr-1"
        collector.kubearchive.get_taskrun.return_value = {"status": {}}
        collector.tekton_results.get_pipelinerun_logs.side_effect = RuntimeError("network")

        result = collector.extract_violation_details("pr-1", {})
        assert result["violations_count"] == 0

    @patch("collectors.conforma_violation_collector.extract_verify_taskrun_name")
    def test_detailed_logs_truncated(self, mock_extract, collector):
        mock_extract.return_value = "verify-tr-1"
        collector.kubearchive.get_taskrun.return_value = {
            "status": {"podName": "pod-1"}
        }
        long_detail = "x" * 200000

        def mock_logs(pod, container=None):
            if container == "step-detailed-report":
                return long_detail
            return None

        collector.kubearchive.get_pod_logs.side_effect = mock_logs

        result = collector.extract_violation_details("pr-1", {})
        assert len(result["violation_summary"]) == 100000

    @patch("collectors.conforma_violation_collector.extract_verify_taskrun_name")
    def test_taskrun_none(self, mock_extract, collector):
        """When get_taskrun returns None, pod_name stays None, triggers fallback."""
        mock_extract.return_value = "verify-tr-1"
        collector.kubearchive.get_taskrun.return_value = None
        collector.tekton_results.get_pipelinerun_logs.return_value = None

        result = collector.extract_violation_details("pr-1", {})
        assert result["violations_count"] == 0


# ===================================================================
# 7. _extract_step_section
# ===================================================================

class TestExtractStepSection:
    def test_match_found(self):
        logs = (
            "===== TaskRun: ns/tr-1/ Step: summary =====\n"
            '{"failures": 2}\n'
            "===== TaskRun: ns/tr-1/ Step: detailed-report =====\n"
            "detail text\n"
        )
        result = ConformaViolationCollector._extract_step_section(logs, "summary")
        assert result == '{"failures": 2}'

    def test_no_match(self):
        logs = "some random logs without step sections"
        result = ConformaViolationCollector._extract_step_section(logs, "summary")
        assert result is None

    def test_multiple_steps_picks_correct_one(self):
        logs = (
            "===== TaskRun: ns/tr-1/ Step: summary =====\n"
            "summary content\n"
            "===== TaskRun: ns/tr-1/ Step: detailed-report =====\n"
            "detail content\n"
            "===== TaskRun: ns/tr-1/ Step: report-json =====\n"
            '{"data": true}\n'
        )
        assert ConformaViolationCollector._extract_step_section(logs, "detailed-report") == "detail content"
        assert ConformaViolationCollector._extract_step_section(logs, "report-json") == '{"data": true}'

    def test_last_step_no_trailing_marker(self):
        logs = (
            "===== TaskRun: ns/tr-1/ Step: report-json =====\n"
            '{"items": [1, 2, 3]}\n'
        )
        result = ConformaViolationCollector._extract_step_section(logs, "report-json")
        assert result == '{"items": [1, 2, 3]}'


# ===================================================================
# 8. get_component_repo_url
# ===================================================================

class TestGetComponentRepoUrl:
    def test_found(self, collector):
        collector.k8s.get_component_metadata.return_value = {
            "repository_url": "https://github.com/org/repo"
        }
        assert collector.get_component_repo_url("comp-a") == "https://github.com/org/repo"

    def test_not_found(self, collector):
        collector.k8s.get_component_metadata.return_value = None
        assert collector.get_component_repo_url("comp-a") == ""


# ===================================================================
# 9. extract_component_info
# ===================================================================

class TestExtractComponentInfo:
    @patch("collectors.conforma_violation_collector.extract_conforma_component_info")
    def test_delegates_correctly(self, mock_extract, collector):
        collector.k8s.get_component_metadata.return_value = {
            "repository_url": "https://github.com/org/repo"
        }
        mock_extract.return_value = {"image": "quay.io/comp-a"}
        pr_data = {"metadata": {"name": "pr-1"}}

        result = collector.extract_component_info(pr_data, "comp-a")

        mock_extract.assert_called_once_with(
            pr_data, "comp-a", "https://github.com/org/repo"
        )
        assert result == {"image": "quay.io/comp-a"}


# ===================================================================
# 10. save_to_db
# ===================================================================

class TestSaveToDb:
    def test_normal_scenario(self, collector):
        collector.conforma_repo.upsert_violation.return_value = {"id": 1}

        result = collector.save_to_db(
            "comp-a", "conforma-stage", "pr-1", "uid-1",
            {"violations_count": 3}, {"image": "img"}
        )

        assert result == {"id": 1}
        collector.conforma_repo.upsert_violation.assert_called_once_with(
            application="test-app",
            component="comp-a", scenario="conforma-stage",
            pr_name="pr-1", pr_uid="uid-1",
            violations={"violations_count": 3}, comp_info={"image": "img"},
            is_future=False, trigger_type='push'
        )

    def test_future_scenario(self, collector):
        collector.conforma_repo.upsert_violation.return_value = {"id": 2}

        result = collector.save_to_db(
            "comp-a", "conforma-stage-future", "pr-1", "uid-1",
            {}, {}
        )

        assert result == {"id": 2}
        call_kwargs = collector.conforma_repo.upsert_violation.call_args
        assert call_kwargs[1]["is_future"] is True

    def test_future_scenario_mid_name(self, collector):
        collector.conforma_repo.upsert_violation.return_value = True

        collector.save_to_db(
            "comp-a", "conforma-future-policy", "pr-1", "uid-1", {}, {}
        )

        call_kwargs = collector.conforma_repo.upsert_violation.call_args
        assert call_kwargs[1]["is_future"] is True

    def test_exception_returns_false(self, collector):
        collector.conforma_repo.upsert_violation.side_effect = Exception("DB error")

        result = collector.save_to_db("comp-a", "conforma-stage", "pr-1", "uid-1", {}, {})
        assert result is False


# ===================================================================
# 11. get_conforma_history
# ===================================================================

class TestGetConformaHistory:
    def test_success_with_mixed_data(self, collector):
        records = [
            _make_pr("comp-a", "conforma-stage", "2026-06-01T00:00:00Z", "True",
                     name="pr-pass"),
            _make_pr("comp-a", "conforma-stage", "2026-06-02T00:00:00Z", "False",
                     name="pr-fail"),
        ]
        collector.tekton_results.query_conforma_records.return_value = records

        result = collector.get_conforma_history("comp-a", limit=10)
        assert len(result) == 2
        assert result[0]["status"] == "Passed"
        assert result[0]["name"] == "pr-pass"
        assert result[1]["status"] == "Failed"
        assert result[1]["name"] == "pr-fail"

    def test_empty_records(self, collector):
        collector.tekton_results.query_conforma_records.return_value = []
        assert collector.get_conforma_history("comp-a") == []

    def test_exception_returns_empty(self, collector):
        collector.tekton_results.query_conforma_records.side_effect = RuntimeError("fail")
        assert collector.get_conforma_history("comp-a") == []

    def test_non_conforma_filtered(self, collector):
        records = [
            _make_pr("comp-a", "integration-test", "2026-06-01T00:00:00Z", "True"),
        ]
        collector.tekton_results.query_conforma_records.return_value = records
        assert collector.get_conforma_history("comp-a") == []

    def test_unknown_status_filtered(self, collector):
        records = [
            _make_pr("comp-a", "conforma-stage", "2026-06-01T00:00:00Z", "Unknown"),
        ]
        collector.tekton_results.query_conforma_records.return_value = records
        assert collector.get_conforma_history("comp-a") == []

    def test_no_conditions_filtered(self, collector):
        pr = _make_pr("comp-a", "conforma-stage", "2026-06-01T00:00:00Z", "True")
        pr["status"]["conditions"] = []
        collector.tekton_results.query_conforma_records.return_value = [pr]
        assert collector.get_conforma_history("comp-a") == []


# ===================================================================
# 12. resolve_fixed_components
# ===================================================================

class TestResolveFixedComponents:
    def test_delegates_correctly(self, collector):
        failing = {("comp-a", "conforma-stage")}
        all_seen = {("comp-a", "conforma-stage"), ("comp-b", "conforma-stage")}
        collector.conforma_repo.resolve_fixed_components.return_value = 1

        result = collector.resolve_fixed_components(failing, all_seen)

        assert result == 1
        collector.conforma_repo.resolve_fixed_components.assert_called_once_with(
            "test-app", failing, all_seen
        )


# ===================================================================
# 13. _resolve_deleted_components
# ===================================================================

class TestResolveDeletedComponents:
    def test_some_deleted(self, collector):
        collector.conforma_repo.find_unresolved_component_names.return_value = [
            "comp-a", "comp-b", "comp-c"
        ]

        def metadata_side_effect(name):
            if name == "comp-b":
                return None  # deleted
            return {"repository_url": "https://github.com/org/" + name}

        collector.k8s.get_component_metadata.side_effect = metadata_side_effect
        collector.conforma_repo.resolve_deleted_component.return_value = 2

        result = collector._resolve_deleted_components()
        assert result == 2
        collector.conforma_repo.resolve_deleted_component.assert_called_once_with(
            "comp-b", "test-app"
        )

    def test_none_deleted(self, collector):
        collector.conforma_repo.find_unresolved_component_names.return_value = [
            "comp-a", "comp-b"
        ]
        collector.k8s.get_component_metadata.return_value = {"repository_url": "url"}

        result = collector._resolve_deleted_components()
        assert result == 0
        collector.conforma_repo.resolve_deleted_component.assert_not_called()

    def test_all_deleted(self, collector):
        collector.conforma_repo.find_unresolved_component_names.return_value = [
            "comp-a", "comp-b"
        ]
        collector.k8s.get_component_metadata.return_value = None
        collector.conforma_repo.resolve_deleted_component.return_value = 1

        result = collector._resolve_deleted_components()
        assert result == 2  # 1 per component, 2 components

    def test_empty_unresolved(self, collector):
        collector.conforma_repo.find_unresolved_component_names.return_value = []
        result = collector._resolve_deleted_components()
        assert result == 0

    def test_resolve_returns_zero_for_component(self, collector):
        """When resolve_deleted_component returns 0 (falsy), count stays at 0."""
        collector.conforma_repo.find_unresolved_component_names.return_value = ["comp-a"]
        collector.k8s.get_component_metadata.return_value = None
        collector.conforma_repo.resolve_deleted_component.return_value = 0

        result = collector._resolve_deleted_components()
        assert result == 0


# ===================================================================
# 14. run
# ===================================================================

class TestRun:
    @patch("collectors.conforma_violation_collector.query_pipelineruns")
    def test_full_happy_path(self, mock_query, collector):
        failing_pr = _make_pr(
            "comp-a", "conforma-stage", "2026-06-01T00:00:00Z", "False",
            name="pr-fail", uid="uid-fail"
        )
        mock_query.return_value = [failing_pr]
        collector.tekton_results.query_conforma_records.return_value = []

        # extract_violation_details mocking
        with patch.object(collector, "extract_violation_details") as mock_violations, \
             patch.object(collector, "extract_component_info") as mock_comp_info, \
             patch.object(collector, "save_to_db") as mock_save, \
             patch.object(collector, "_resolve_deleted_components") as mock_deleted:

            mock_violations.return_value = {"violations_count": 2}
            mock_comp_info.return_value = {"image": "img"}
            mock_save.return_value = True
            collector.conforma_repo.resolve_fixed_components.return_value = 0
            mock_deleted.return_value = 0

            result = collector.run()

            assert result["collected"] == 1
            assert result["resolved"] == 0
            assert "duration" in result
            mock_violations.assert_called_once_with("pr-fail", failing_pr)
            mock_save.assert_called_once()

    @patch("collectors.conforma_violation_collector.query_pipelineruns")
    def test_no_failures(self, mock_query, collector):
        passing_pr = _make_pr(
            "comp-a", "conforma-stage", "2026-06-01T00:00:00Z", "True"
        )
        mock_query.return_value = [passing_pr]
        collector.tekton_results.query_conforma_records.return_value = []
        collector.conforma_repo.resolve_fixed_components.return_value = 3

        result = collector.run()

        assert result["collected"] == 0
        assert result["resolved"] == 3

    @patch("collectors.conforma_violation_collector.query_pipelineruns")
    def test_failures_with_resolutions(self, mock_query, collector):
        failing = _make_pr("comp-a", "conforma-stage", "2026-06-01T00:00:00Z", "False",
                          name="pr-f", uid="uid-f")
        passing = _make_pr("comp-b", "conforma-stage", "2026-06-01T00:00:00Z", "True",
                          name="pr-p", uid="uid-p")
        mock_query.return_value = [failing, passing]
        collector.tekton_results.query_conforma_records.return_value = []

        with patch.object(collector, "extract_violation_details") as mock_v, \
             patch.object(collector, "extract_component_info") as mock_ci, \
             patch.object(collector, "save_to_db") as mock_save, \
             patch.object(collector, "_resolve_deleted_components") as mock_deleted:

            mock_v.return_value = {}
            mock_ci.return_value = {}
            mock_save.return_value = True
            collector.conforma_repo.resolve_fixed_components.return_value = 2
            mock_deleted.return_value = 1

            result = collector.run()

            assert result["collected"] == 1
            assert result["resolved"] == 3  # 2 fixed + 1 deleted

    @patch("collectors.conforma_violation_collector.query_pipelineruns")
    def test_save_failure_not_counted(self, mock_query, collector):
        failing_pr = _make_pr(
            "comp-a", "conforma-stage", "2026-06-01T00:00:00Z", "False",
            name="pr-1", uid="uid-1"
        )
        mock_query.return_value = [failing_pr]
        collector.tekton_results.query_conforma_records.return_value = []

        with patch.object(collector, "extract_violation_details") as mock_v, \
             patch.object(collector, "extract_component_info") as mock_ci, \
             patch.object(collector, "save_to_db") as mock_save, \
             patch.object(collector, "_resolve_deleted_components") as mock_deleted:

            mock_v.return_value = {}
            mock_ci.return_value = {}
            mock_save.return_value = False  # save fails
            collector.conforma_repo.resolve_fixed_components.return_value = 0
            mock_deleted.return_value = 0

            result = collector.run()
            assert result["collected"] == 0

    @patch("collectors.conforma_violation_collector.query_pipelineruns")
    def test_deleted_components_resolved(self, mock_query, collector):
        mock_query.return_value = []
        collector.tekton_results.query_conforma_records.return_value = []

        # No all_seen means resolve_fixed_components is not called with all_seen
        result = collector.run()

        assert result["collected"] == 0
        assert "duration" in result

    @patch("collectors.conforma_violation_collector.query_pipelineruns")
    def test_multiple_failing_components(self, mock_query, collector):
        prs = [
            _make_pr("comp-a", "conforma-stage", "2026-06-01T00:00:00Z", "False",
                     name="pr-a", uid="uid-a"),
            _make_pr("comp-b", "conforma-prod", "2026-06-01T00:00:00Z", "False",
                     name="pr-b", uid="uid-b"),
            _make_pr("comp-c", "conforma-stage", "2026-06-01T00:00:00Z", "True",
                     name="pr-c", uid="uid-c"),
        ]
        mock_query.return_value = prs
        collector.tekton_results.query_conforma_records.return_value = []

        with patch.object(collector, "extract_violation_details") as mock_v, \
             patch.object(collector, "extract_component_info") as mock_ci, \
             patch.object(collector, "save_to_db") as mock_save, \
             patch.object(collector, "_resolve_deleted_components") as mock_deleted:

            mock_v.return_value = {}
            mock_ci.return_value = {}
            mock_save.return_value = True
            collector.conforma_repo.resolve_fixed_components.return_value = 0
            mock_deleted.return_value = 0

            result = collector.run()
            assert result["collected"] == 2
            assert mock_v.call_count == 2
            assert mock_save.call_count == 2

    @patch("collectors.conforma_violation_collector.query_pipelineruns")
    def test_duration_is_positive(self, mock_query, collector):
        mock_query.return_value = []
        collector.tekton_results.query_conforma_records.return_value = []

        result = collector.run()
        assert result["duration"] >= 0
