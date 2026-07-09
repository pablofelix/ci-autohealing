"""Tests for infer_label_from_pr() and LabelInferenceService."""
import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# --- Pure function tests for infer_label_from_pr ---

class TestInferLabelFromPr:
    def _infer(self, files):
        from collectors.verdict_correlator import infer_label_from_pr
        return infer_label_from_pr(files)

    def test_empty_files_returns_zero_confidence(self):
        result = self._infer([])
        assert result.failure_category == 'unknown'
        assert result.label_confidence == 0.0

    def test_none_like_empty_list_returns_zero_confidence(self):
        result = self._infer([])
        assert result.label_confidence == 0.0

    # --- dependency_issue ---

    def test_go_mod_only_returns_dependency_issue_confidence_1(self):
        result = self._infer(['go.mod'])
        assert result.failure_category == 'dependency_issue'
        assert result.label_confidence == 1.0

    def test_go_mod_and_go_sum_returns_dependency_issue(self):
        result = self._infer(['go.mod', 'go.sum'])
        assert result.failure_category == 'dependency_issue'
        assert result.label_confidence == 1.0

    def test_requirements_txt_returns_dependency_issue(self):
        result = self._infer(['requirements.txt'])
        assert result.failure_category == 'dependency_issue'
        assert result.label_confidence == 1.0

    def test_pyproject_toml_returns_dependency_issue(self):
        result = self._infer(['pyproject.toml'])
        assert result.failure_category == 'dependency_issue'
        assert result.label_confidence == 1.0

    def test_package_json_returns_dependency_issue(self):
        result = self._infer(['package.json'])
        assert result.failure_category == 'dependency_issue'
        assert result.label_confidence == 1.0

    def test_package_lock_json_returns_dependency_issue(self):
        result = self._infer(['package-lock.json'])
        assert result.failure_category == 'dependency_issue'
        assert result.label_confidence == 1.0

    def test_pipfile_returns_dependency_issue(self):
        result = self._infer(['Pipfile'])
        assert result.failure_category == 'dependency_issue'
        assert result.label_confidence == 1.0

    def test_setup_py_returns_dependency_issue(self):
        result = self._infer(['setup.py'])
        assert result.failure_category == 'dependency_issue'
        assert result.label_confidence == 1.0

    # --- build_configuration ---

    def test_dockerfile_only_returns_build_configuration(self):
        result = self._infer(['Dockerfile'])
        assert result.failure_category == 'build_configuration'
        assert result.label_confidence == 1.0

    def test_containerfile_returns_build_configuration(self):
        result = self._infer(['Containerfile'])
        assert result.failure_category == 'build_configuration'
        assert result.label_confidence == 1.0

    def test_makefile_returns_build_configuration(self):
        result = self._infer(['Makefile'])
        assert result.failure_category == 'build_configuration'
        assert result.label_confidence == 1.0

    # --- tekton_configuration ---

    def test_tekton_yaml_returns_tekton_configuration(self):
        result = self._infer(['.tekton/push.yaml'])
        assert result.failure_category == 'tekton_configuration'
        assert result.label_confidence == 1.0

    def test_tekton_multiple_files_returns_tekton_configuration(self):
        result = self._infer(['.tekton/push.yaml', '.tekton/pull-request.yaml'])
        assert result.failure_category == 'tekton_configuration'
        assert result.label_confidence == 1.0

    def test_non_tekton_yaml_at_root_is_not_tekton(self):
        result = self._infer(['config.yaml'])
        assert result.failure_category != 'tekton_configuration'

    # --- test_failure ---

    def test_go_test_file_returns_test_failure(self):
        result = self._infer(['main_test.go'])
        assert result.failure_category == 'test_failure'
        assert result.label_confidence == 1.0

    def test_spec_ts_returns_test_failure(self):
        result = self._infer(['service.spec.ts'])
        assert result.failure_category == 'test_failure'
        assert result.label_confidence == 1.0

    def test_test_prefix_py_returns_test_failure(self):
        result = self._infer(['test_main.py'])
        assert result.failure_category == 'test_failure'
        assert result.label_confidence == 1.0

    # --- build_script_error ---

    def test_hack_script_returns_build_script_error(self):
        result = self._infer(['hack/build.sh'])
        assert result.failure_category == 'build_script_error'
        assert result.label_confidence == 1.0

    def test_scripts_dir_returns_build_script_error(self):
        result = self._infer(['scripts/compile.sh'])
        assert result.failure_category == 'build_script_error'
        assert result.label_confidence == 1.0

    # --- source_code_change ---

    def test_go_source_only_returns_source_code_change_low_confidence(self):
        result = self._infer(['main.go'])
        assert result.failure_category == 'source_code_change'
        assert result.label_confidence == 0.3

    def test_python_source_only_returns_source_code_change_low_confidence(self):
        result = self._infer(['app.py'])
        assert result.failure_category == 'source_code_change'
        assert result.label_confidence == 0.3

    def test_multiple_go_files_returns_source_code_change_low_confidence(self):
        result = self._infer(['cmd/main.go', 'pkg/handler.go', 'internal/util.go'])
        assert result.failure_category == 'source_code_change'
        assert result.label_confidence == 0.3

    # --- unrecognized ---

    def test_unrecognized_extension_returns_zero_confidence(self):
        result = self._infer(['README.rst', 'CHANGES'])
        assert result.label_confidence == 0.0

    # --- mixed signals ---

    def test_dockerfile_plus_go_mod_returns_0_5_confidence(self):
        result = self._infer(['Dockerfile', 'go.mod'])
        assert result.label_confidence == 0.5

    def test_build_config_dominant_returns_0_8(self):
        result = self._infer(['Dockerfile', 'Containerfile', 'Makefile', 'main.go'])
        assert result.failure_category == 'build_configuration'
        assert result.label_confidence == 0.8

    def test_primary_signal_75_percent_returns_0_8(self):
        result = self._infer(['go.mod', 'go.sum', 'go.sum', 'Dockerfile'])
        assert result.failure_category == 'dependency_issue'
        assert result.label_confidence == 0.8

    def test_mixed_many_categories_returns_0_5(self):
        result = self._infer(['go.mod', 'Dockerfile', 'main.go', '.tekton/push.yaml'])
        assert result.label_confidence == 0.5

    # --- path handling ---

    def test_tekton_path_uses_full_path_not_basename(self):
        result = self._infer(['.tekton/push.yaml'])
        assert result.failure_category == 'tekton_configuration'

    def test_vendor_go_files_map_to_source_not_dependency(self):
        result = self._infer(['vendor/github.com/some/pkg/util.go'])
        assert result.failure_category == 'source_code_change'
        assert result.label_confidence == 0.3

    def test_full_path_dependency_file_resolved_by_basename(self):
        result = self._infer(['path/to/nested/go.mod'])
        assert result.failure_category == 'dependency_issue'

    def test_label_source_is_non_empty_string(self):
        result = self._infer(['go.mod'])
        assert isinstance(result.label_source, str)
        assert len(result.label_source) > 0

    # --- LabelInference dataclass ---

    def test_label_inference_is_frozen(self):
        from collectors.verdict_correlator import LabelInference
        inf = LabelInference('dependency_issue', 1.0, 'go.mod only (1 file)')
        try:
            inf.failure_category = 'build_error'
            raise AssertionError('should have raised FrozenInstanceError')
        except AssertionError:
            raise
        except Exception:
            pass

    def test_classify_fix_type_still_works_after_additions(self):
        from collectors.verdict_correlator import classify_fix_type
        assert classify_fix_type([]) == 'unknown'
        assert classify_fix_type(['Dockerfile']) == 'config_change'
        assert classify_fix_type(['main.go']) == 'code_change'
        assert classify_fix_type(['Dockerfile', 'main.go']) == 'mixed'


# --- LabelInferenceService tests ---

def _make_db(rows=None):
    db = MagicMock()
    conn = MagicMock()
    cursor = MagicMock()
    cursor.fetchall.return_value = rows or []
    conn.cursor.return_value = cursor
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    db.connection.return_value = conn
    return db, cursor


def _make_github(pr=None, files=None):
    gh = MagicMock()
    if pr is None:
        gh.get_pr_for_commit.return_value = None
    else:
        gh.get_pr_for_commit.return_value = pr
    if files is None:
        resp = MagicMock()
        resp.json.return_value = []
        gh._get.return_value = resp
    else:
        resp = MagicMock()
        resp.json.return_value = [{'filename': f} for f in files]
        gh._get.return_value = resp
    return gh


class TestLabelInferenceService:
    def _make_service(self, db, gh):
        from collectors.verdict_correlator import LabelInferenceService
        config = MagicMock()
        config.github_token = 'fake-token'
        config.db = MagicMock()
        return LabelInferenceService(config, db=db, github_client=gh)

    def test_skips_when_no_github_client(self):
        from collectors.verdict_correlator import LabelInferenceService
        config = MagicMock()
        config.github_token = None
        config.db = MagicMock()
        db, _ = _make_db()
        svc = LabelInferenceService(config, db=db, github_client=None)
        result = svc.label_one(
            build_failure_id=1,
            resolution_commit_sha='abc123',
            repository_url='https://github.com/org/repo',
        )
        assert result is None

    def test_skips_when_no_pr_and_no_failing_sha(self):
        db, _ = _make_db()
        gh = _make_github(pr=None)
        svc = self._make_service(db, gh)
        counts = {}
        result = svc.label_one(
            build_failure_id=1,
            resolution_commit_sha='abc123',
            repository_url='https://github.com/org/repo',
            _counts=counts,
        )
        assert result is None
        assert counts.get('skipped_no_files') == 1

    def test_skips_when_no_files_in_pr(self):
        db, _ = _make_db()
        gh = _make_github(pr={'number': 42, 'url': 'https://github.com/org/repo/pull/42'}, files=[])
        svc = self._make_service(db, gh)
        counts = {}
        result = svc.label_one(
            build_failure_id=1,
            resolution_commit_sha='abc123',
            repository_url='https://github.com/org/repo',
            _counts=counts,
        )
        assert result is None
        assert counts.get('skipped_no_files') == 1

    def test_skips_when_below_min_confidence(self):
        db, _ = _make_db()
        gh = _make_github(
            pr={'number': 1, 'url': 'url'},
            files=['main.go'],
        )
        svc = self._make_service(db, gh)
        counts = {}
        result = svc.label_one(
            build_failure_id=1,
            resolution_commit_sha='abc123',
            repository_url='https://github.com/org/repo',
            min_confidence=0.9,
            _counts=counts,
        )
        assert result is None
        assert counts.get('skipped_low_confidence') == 1

    def test_dry_run_does_not_write(self):
        db, cursor = _make_db()
        gh = _make_github(
            pr={'number': 1, 'url': 'url'},
            files=['go.mod'],
        )
        svc = self._make_service(db, gh)
        result = svc.label_one(
            build_failure_id=1,
            resolution_commit_sha='abc123',
            repository_url='https://github.com/org/repo',
            dry_run=True,
        )
        assert result is not None
        assert result.failure_category == 'dependency_issue'
        cursor.execute.assert_not_called()

    def test_saves_label_on_success(self):
        db, cursor = _make_db()
        gh = _make_github(
            pr={'number': 7, 'url': 'https://github.com/org/repo/pull/7'},
            files=['go.mod', 'go.sum'],
        )
        svc = self._make_service(db, gh)
        result = svc.label_one(
            build_failure_id=99,
            resolution_commit_sha='deadbeef',
            repository_url='https://github.com/org/repo',
        )
        assert result is not None
        assert result.failure_category == 'dependency_issue'
        assert result.label_confidence == 1.0
        cursor.execute.assert_called_once()
        call_args = cursor.execute.call_args[0]
        assert 'INSERT INTO ml_training_labels' in call_args[0]
        assert 99 in call_args[1]

    def test_backfill_returns_counts(self):
        rows = [
            (1, 'comp-a', 'app', 'https://github.com/org/repo', 'sha1', None),
            (2, 'comp-b', 'app', 'https://github.com/org/repo', 'sha2', None),
        ]
        db, cursor = _make_db(rows=rows)
        gh = _make_github(pr=None)
        svc = self._make_service(db, gh)
        counts = svc.backfill(application='app', limit=10)
        assert counts['processed'] == 2
        assert counts['skipped_no_files'] == 2
        assert counts['labeled'] == 0

    def test_backfill_with_files_labels_components(self):
        rows = [(1, 'comp-a', 'app', 'https://github.com/org/repo', 'sha1', None)]
        db, cursor = _make_db(rows=rows)
        gh = _make_github(
            pr={'number': 3, 'url': 'url'},
            files=['go.mod'],
        )
        svc = self._make_service(db, gh)
        counts = svc.backfill(application='app', limit=10)
        assert counts['processed'] == 1
        assert counts['labeled'] == 1

    def test_respects_min_confidence_in_backfill(self):
        rows = [(1, 'comp-a', 'app', 'https://github.com/org/repo', 'sha1', None)]
        db, _ = _make_db(rows=rows)
        gh = _make_github(pr={'number': 1, 'url': 'url'}, files=['main.go'])
        svc = self._make_service(db, gh)
        counts = svc.backfill(application='app', limit=10, min_confidence=0.8)
        assert counts['labeled'] == 0
        assert counts['skipped_low_confidence'] == 1

    def test_invalid_repo_url_skips_gracefully(self):
        db, _ = _make_db()
        gh = _make_github()
        svc = self._make_service(db, gh)
        result = svc.label_one(
            build_failure_id=1,
            resolution_commit_sha='abc',
            repository_url='not-a-valid-url',
        )
        assert result is None

    def test_uses_commit_comparison_when_no_pr(self):
        db, cursor = _make_db()
        gh = _make_github(pr=None)
        gh.compare_commits = lambda owner, repo, base, head: ['go.mod', 'go.sum']
        svc = self._make_service(db, gh)
        result = svc.label_one(
            build_failure_id=1,
            resolution_commit_sha='fix-sha',
            repository_url='https://github.com/org/repo',
            failing_commit_sha='fail-sha',
        )
        assert result is not None
        assert result.failure_category == 'dependency_issue'
        assert result.label_confidence == 1.0

    def test_commit_comparison_skips_when_no_failing_sha(self):
        db, _ = _make_db()
        gh = _make_github(pr=None)
        svc = self._make_service(db, gh)
        counts = {}
        result = svc.label_one(
            build_failure_id=1,
            resolution_commit_sha='fix-sha',
            repository_url='https://github.com/org/repo',
            failing_commit_sha=None,
            _counts=counts,
        )
        assert result is None
        assert counts.get('skipped_no_files') == 1

    def test_commit_comparison_not_used_when_same_sha(self):
        db, _ = _make_db()
        gh = _make_github(pr=None)
        compare_called = []
        gh.compare_commits = lambda *a: compare_called.append(a) or []
        svc = self._make_service(db, gh)
        result = svc.label_one(
            build_failure_id=1,
            resolution_commit_sha='same-sha',
            repository_url='https://github.com/org/repo',
            failing_commit_sha='same-sha',
        )
        assert result is None
        assert compare_called == []
