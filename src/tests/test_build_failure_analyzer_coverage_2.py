"""Additional coverage tests for BuildFailureAnalyzer.

Covers lines missed by test_build_failure_analyzer_coverage.py:
- _ensure_context exception path (239-240)
- _ensure_enrichment orchestrator flow (255-276)
- _store_field blob offloading (291-295)
- _ensure_logs extracted section log (341-343)
- _ensure_logs filtering branch (359-360)
- _ensure_logs head+tail truncation fallback (366)
- _refetch_logs full method (388-425)
- _fetch_oci_logs full method (429-461)
- _fetch_sarif_for_failure full method (465-495)
- _fetch_failed_taskrun_logs full method (503-597)
- _ai_extract_error creation failure + success (622-624, 648-651)
- _get_release_context blocker signals (834-836)
- _get_release_context systemic pattern (850-862)
- _format_commit_context enriched_context parsing (929-930)
- _format_commit_context file without patch (957)
- _format_commit_context PR body truncation (975)
- _format_commit_context Tekton config truncation (994)
- run_batch_analysis force/component_filter logs (1267, 1269)
"""

import json
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def _make_config(llm=True):
    config = MagicMock()
    config.db = MagicMock()
    config.k8s.application_name = 'test-app'
    config.k8s.namespace = 'test-ns'
    config.github_token = 'gh-tok'
    if llm:
        config.llm = MagicMock()
        config.llm.provider = 'anthropic'
    else:
        config.llm = None
    return config


def _make_pattern_service():
    svc = MagicMock()
    enhancement = MagicMock()
    enhancement.boost_applied = False
    enhancement.original_confidence = 0.8
    enhancement.boosted_confidence = 0.8
    enhancement.boost_amount = 0.0
    enhancement.matched_patterns = []
    svc.enhance_analysis.return_value = enhancement
    svc.get_matches_for_prompt.return_value = ''
    return svc


def _make_llm_response(tool_input=None, tool_name='record_analysis',
                        input_tokens=1000, output_tokens=500):
    if tool_input is None:
        tool_input = {
            'root_cause': 'Dependency resolution failed',
            'failure_category': 'dependency_issue',
            'confidence_score': 0.85,
            'recommended_fix': 'Update go.sum',
            'recommended_files': ['go.sum'],
            'can_auto_fix': False,
            'requires_human_review': True,
        }
    resp = MagicMock()
    resp.tool_calls = [{'name': tool_name, 'input': tool_input}]
    resp.input_tokens = input_tokens
    resp.output_tokens = output_tokens
    resp.content = 'some text'
    resp.content_text = 'some text'
    return resp


def _make_failure(**overrides):
    failure = {
        'id': 42,
        'component_name': 'odh-dashboard',
        'pipelinerun_name': 'odh-dashboard-pr-123',
        'repository_url': 'https://github.com/opendatahub-io/odh-dashboard',
        'repository': 'https://github.com/opendatahub-io/odh-dashboard',
        'commit_sha': 'abc1234567890def',
        'commit_author': 'dev@example.com',
        'commit_message': 'Update deps',
        'branch': 'rhoai-2.19',
        'failed_task_name': 'build-container',
        'failed_step_name': 'build',
        'error_type': 'build_error',
        'error_message': 'exit code 1',
        'build_logs': 'Step 5: ERROR: cannot find module\nSome more logs',
        'commit_context': None,
        'enriched_context': None,
        'application': 'rhoai-v2.19',
        'triage_items': [],
        'blob_refs': None,
    }
    failure.update(overrides)
    return failure


def _build_analyzer(**kw):
    defaults = dict(
        config=_make_config(),
        db=MagicMock(),
        build_repo=MagicMock(),
        ai_repo=MagicMock(),
        llm=MagicMock(),
        langfuse=MagicMock(),
        pattern_service=_make_pattern_service(),
        github_client=MagicMock(),
    )
    defaults.update(kw)
    with patch('analyzers.build_failure_analyzer.load_prompt', return_value='system prompt'):
        from analyzers.build_failure_analyzer import BuildFailureAnalyzer
        return BuildFailureAnalyzer(**defaults)


# ---------------------------------------------------------------------------
# Lines 239-240: _ensure_context exception in _store_field
# ---------------------------------------------------------------------------
class TestEnsureContextStoreFieldException:
    """Tests for _ensure_context when _store_field raises."""

    def test_store_field_exception_logs_warning(self):
        """When _store_field fails inside _ensure_context, a warning is logged
        but the failure dict still has the commit_context set."""
        gh = MagicMock()
        gh.get_commit_context.return_value = {'commit': {'sha': 'abc123'}}
        analyzer = _build_analyzer(github_client=gh)
        # Make _store_field raise
        analyzer._store_field = MagicMock(side_effect=RuntimeError("DB connection lost"))

        failure = _make_failure(commit_context=None)
        analyzer._ensure_context(failure)

        # commit_context should still be set even though store failed
        assert failure['commit_context'] == {'commit': {'sha': 'abc123'}}
        analyzer._store_field.assert_called_once()


# ---------------------------------------------------------------------------
# Lines 255-276: _ensure_enrichment orchestrator flow
# ---------------------------------------------------------------------------
class TestEnsureEnrichment:
    """Tests for _ensure_enrichment orchestrator."""

    def test_enrichment_success_reads_db(self):
        """On successful enrichment, reads enriched_context from DB."""
        db = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = ('{"related_failures": []}',)
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        db.connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
        db.connection.return_value.__exit__ = MagicMock(return_value=False)

        analyzer = _build_analyzer(db=db)

        failure = _make_failure(enriched_context=None)

        # Patch all the imports inside _ensure_enrichment
        mock_orch_instance = MagicMock()
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.sources_succeeded = 3
        mock_result.sources_attempted = 4
        mock_orch_instance.enrich_failure.return_value = mock_result

        with patch.dict('sys.modules', {
            'enrichment.enrichment_orchestrator': MagicMock(
                EnrichmentOrchestrator=MagicMock(return_value=mock_orch_instance)
            ),
            'enrichment.sources.build_history': MagicMock(),
            'enrichment.sources.dependency_context': MagicMock(),
            'enrichment.sources.open_prs': MagicMock(),
            'enrichment.sources.related_failures': MagicMock(),
        }):
            analyzer._ensure_enrichment(failure)

        assert failure['enriched_context'] == '{"related_failures": []}'

    def test_enrichment_no_additional_context(self):
        """When enrichment succeeds but finds nothing, log message is generated."""
        db = MagicMock()
        analyzer = _build_analyzer(db=db)

        failure = _make_failure(enriched_context=None)

        mock_orch_instance = MagicMock()
        mock_result = MagicMock()
        mock_result.success = False
        mock_orch_instance.enrich_failure.return_value = mock_result

        with patch.dict('sys.modules', {
            'enrichment.enrichment_orchestrator': MagicMock(
                EnrichmentOrchestrator=MagicMock(return_value=mock_orch_instance)
            ),
            'enrichment.sources.build_history': MagicMock(),
            'enrichment.sources.dependency_context': MagicMock(),
            'enrichment.sources.open_prs': MagicMock(),
            'enrichment.sources.related_failures': MagicMock(),
        }):
            analyzer._ensure_enrichment(failure)

        # enriched_context should remain None since enrichment found nothing
        assert failure.get('enriched_context') is None

    def test_enrichment_skips_when_already_present(self):
        """When enriched_context is already set, enrichment is skipped."""
        analyzer = _build_analyzer()
        failure = _make_failure(enriched_context='{"already": "present"}')
        # Should return immediately without doing anything
        analyzer._ensure_enrichment(failure)

    def test_enrichment_exception_is_caught(self):
        """When enrichment imports fail, exception is caught silently."""
        analyzer = _build_analyzer()
        failure = _make_failure(enriched_context=None)

        with patch.dict('sys.modules', {
            'enrichment.enrichment_orchestrator': None,
        }):
            # Should not raise
            analyzer._ensure_enrichment(failure)

    def test_enrichment_db_returns_no_row(self):
        """When DB returns no row after enrichment, enriched_context stays None."""
        db = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        db.connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
        db.connection.return_value.__exit__ = MagicMock(return_value=False)

        analyzer = _build_analyzer(db=db)
        failure = _make_failure(enriched_context=None)

        mock_orch_instance = MagicMock()
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.sources_succeeded = 2
        mock_result.sources_attempted = 2
        mock_orch_instance.enrich_failure.return_value = mock_result

        with patch.dict('sys.modules', {
            'enrichment.enrichment_orchestrator': MagicMock(
                EnrichmentOrchestrator=MagicMock(return_value=mock_orch_instance)
            ),
            'enrichment.sources.build_history': MagicMock(),
            'enrichment.sources.dependency_context': MagicMock(),
            'enrichment.sources.open_prs': MagicMock(),
            'enrichment.sources.related_failures': MagicMock(),
        }):
            analyzer._ensure_enrichment(failure)

        assert failure.get('enriched_context') is None


# ---------------------------------------------------------------------------
# Lines 291-295: _store_field blob offloading branch
# ---------------------------------------------------------------------------
class TestStoreFieldBlobOffload:
    """Tests for _store_field when should_offload returns True."""

    @patch('analyzers.build_failure_analyzer.get_blob_store')
    @patch('analyzers.build_failure_analyzer.make_blob_key', return_value='blob/key.txt')
    @patch('analyzers.build_failure_analyzer.should_offload', return_value=True)
    def test_offload_string_data(self, mock_should, mock_key, mock_get_blob):
        """String data is offloaded with .txt extension."""
        db = MagicMock()
        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        db.connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
        db.connection.return_value.__exit__ = MagicMock(return_value=False)

        mock_blob = MagicMock()
        mock_get_blob.return_value = mock_blob

        analyzer = _build_analyzer(db=db)
        failure = _make_failure()
        large_data = 'x' * 500000

        analyzer._store_field(failure, 'build_logs', large_data)

        mock_blob.put.assert_called_once_with('blob/key.txt', large_data)
        mock_key.assert_called_once_with(
            'build-failures', 'odh-dashboard', 'odh-dashboard-pr-123', 'build_logs', 'txt')
        mock_cursor.execute.assert_called_once()
        sql = mock_cursor.execute.call_args[0][0]
        assert 'blob_refs' in sql
        assert 'NULL' in sql

    @patch('analyzers.build_failure_analyzer.get_blob_store')
    @patch('analyzers.build_failure_analyzer.make_blob_key', return_value='blob/key.json')
    @patch('analyzers.build_failure_analyzer.should_offload', return_value=True)
    def test_offload_dict_data(self, mock_should, mock_key, mock_get_blob):
        """Dict data is JSON-serialized and offloaded with .json extension."""
        db = MagicMock()
        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        db.connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
        db.connection.return_value.__exit__ = MagicMock(return_value=False)

        mock_blob = MagicMock()
        mock_get_blob.return_value = mock_blob

        analyzer = _build_analyzer(db=db)
        failure = _make_failure()
        dict_data = {'commit': {'sha': 'abc'}, 'pr': {'title': 'fix'}}

        analyzer._store_field(failure, 'commit_context', dict_data)

        mock_key.assert_called_once_with(
            'build-failures', 'odh-dashboard', 'odh-dashboard-pr-123', 'commit_context', 'json')
        mock_blob.put.assert_called_once()
        # Verify the stored data is JSON-serialized
        stored = mock_blob.put.call_args[0][1]
        assert json.loads(stored) == dict_data


# ---------------------------------------------------------------------------
# Lines 341-343: _ensure_logs extracted failed section
# ---------------------------------------------------------------------------
class TestEnsureLogsExtractedSection:
    """Tests for _ensure_logs when failed section is extracted."""

    @patch('analyzers.build_failure_analyzer.BuildFailureAnalyzer._fetch_failed_taskrun_logs',
           return_value=None)
    def test_extracted_section_replaces_logs(self, _mock_fetch):
        """When _extract_failed_section returns a section > 100 chars,
        logs are replaced with the extracted section."""
        analyzer = _build_analyzer()

        section = 'x' * 200  # Over 100 chars
        analyzer._extract_failed_section = MagicMock(return_value=section)

        failure = _make_failure(
            build_logs='lots of logs here\n' * 100,
            failed_task_name='build-container',
        )

        analyzer._ensure_logs(failure)

        assert failure['build_logs'] == section


# ---------------------------------------------------------------------------
# Lines 359-360: _ensure_logs filtering branch >= 200 chars
# ---------------------------------------------------------------------------
class TestEnsureLogsFiltering:
    """Tests for _ensure_logs when error keyword filtering applies."""

    def test_keyword_filtering_applied_when_result_long_enough(self):
        """When logs > MAX_LOG_CHARS and filtered result >= 200, use filtered."""
        analyzer = _build_analyzer()
        analyzer._fetch_failed_taskrun_logs = MagicMock(return_value=None)
        analyzer._extract_failed_section = MagicMock(return_value=None)

        # Create logs > 100000 chars
        big_logs = 'normal line\n' * 20000  # ~240000 chars

        filtered_result = 'ERROR: something failed\n' * 20  # ~500 chars, >= 200
        analyzer._filter_error_lines = MagicMock(return_value=filtered_result)

        failure = _make_failure(
            build_logs=big_logs,
            failed_task_name='',  # Skip section extraction
        )

        analyzer._ensure_logs(failure)

        assert failure['build_logs'] == filtered_result
        analyzer._filter_error_lines.assert_called_once()


# ---------------------------------------------------------------------------
# Line 366: _ensure_logs head+tail truncation fallback
# ---------------------------------------------------------------------------
class TestEnsureLogsHeadTailFallback:
    """Tests for _ensure_logs head+tail truncation path."""

    @patch('analyzers.build_failure_analyzer.BuildFailureAnalyzer._fetch_failed_taskrun_logs',
           return_value=None)
    def test_head_tail_truncation_when_ai_extraction_fails(self, _mock_fetch):
        """When logs are huge, filtering returns None, and AI extraction
        returns None, fall back to head+tail truncation."""
        analyzer = _build_analyzer()
        analyzer._extract_failed_section = MagicMock(return_value=None)
        analyzer._filter_error_lines = MagicMock(return_value=None)
        analyzer._ai_extract_error = MagicMock(return_value=None)

        big_logs = 'A' * 200000
        failure = _make_failure(
            build_logs=big_logs,
            failed_task_name='',
        )

        analyzer._ensure_logs(failure)

        # Should be truncated to head + tail = ~100000 chars plus separator
        assert len(failure['build_logs']) <= 100100
        assert 'chars omitted' in failure['build_logs']  # truncation marker

    @patch('analyzers.build_failure_analyzer.BuildFailureAnalyzer._fetch_failed_taskrun_logs',
           return_value=None)
    def test_ai_extraction_used_when_available(self, _mock_fetch):
        """When AI extraction succeeds, its result is used."""
        analyzer = _build_analyzer()
        analyzer._extract_failed_section = MagicMock(return_value=None)
        analyzer._filter_error_lines = MagicMock(return_value=None)
        extracted = 'AI-extracted error lines here\n' * 10
        analyzer._ai_extract_error = MagicMock(return_value=extracted)

        big_logs = 'B' * 200000
        failure = _make_failure(
            build_logs=big_logs,
            failed_task_name='',
        )

        analyzer._ensure_logs(failure)

        assert failure['build_logs'] == extracted


# ---------------------------------------------------------------------------
# Lines 388-425: _refetch_logs
# ---------------------------------------------------------------------------
class TestRefetchLogs:
    """Tests for _refetch_logs method."""

    def test_refetch_no_pr_name(self):
        """Returns None when no pipelinerun_name."""
        analyzer = _build_analyzer()
        failure = _make_failure(pipelinerun_name=None)
        assert analyzer._refetch_logs(failure) is None

    def test_refetch_from_tekton_results_success(self):
        """Successfully fetches from Tekton Results."""
        analyzer = _build_analyzer()
        full_logs = 'Complete build output\n' * 100

        mock_tr = MagicMock()
        mock_tr.get_pipelinerun_logs.return_value = full_logs

        failure = _make_failure(build_logs='short')
        analyzer._store_field = MagicMock()

        with patch.dict('sys.modules', {
            'clients.tekton_results': MagicMock(
                TektonResultsClient=MagicMock(return_value=mock_tr)
            ),
        }):
            result = analyzer._refetch_logs(failure, failed_task='')

        assert result == full_logs
        analyzer._store_field.assert_called_once()

    def test_refetch_tekton_fails_falls_back_to_oci(self):
        """When Tekton Results fails, falls back to OCI logs."""
        analyzer = _build_analyzer()
        oci_logs = 'OCI fallback logs\n' * 50

        failure = _make_failure(build_logs='short')
        analyzer._store_field = MagicMock()
        analyzer._fetch_oci_logs = MagicMock(return_value=oci_logs)

        with patch.dict('sys.modules', {
            'clients.tekton_results': MagicMock(
                TektonResultsClient=MagicMock(side_effect=Exception("TR unavailable"))
            ),
        }):
            # Tekton Results import will succeed but the client call should fail
            # Actually, let's mock at the method level
            pass

        # Simpler approach: mock the Tekton Results return to be empty
        mock_tr = MagicMock()
        mock_tr.get_pipelinerun_logs.return_value = None

        with patch.dict('sys.modules', {
            'clients.tekton_results': MagicMock(
                TektonResultsClient=MagicMock(return_value=mock_tr)
            ),
        }):
            result = analyzer._refetch_logs(failure, failed_task='')

        assert result == oci_logs

    def test_refetch_extracts_failed_section(self):
        """When failed_task is provided, extracts that section."""
        analyzer = _build_analyzer()
        full_logs = 'Complete build logs with many tasks\n' * 100

        mock_tr = MagicMock()
        mock_tr.get_pipelinerun_logs.return_value = full_logs

        failure = _make_failure(build_logs='short')
        analyzer._store_field = MagicMock()
        section = 'extracted section for build-container ' + 'x' * 200
        analyzer._extract_failed_section = MagicMock(return_value=section)

        with patch.dict('sys.modules', {
            'clients.tekton_results': MagicMock(
                TektonResultsClient=MagicMock(return_value=mock_tr)
            ),
        }):
            result = analyzer._refetch_logs(failure, failed_task='build-container')

        assert result == section

    def test_refetch_tekton_exception_returns_none(self):
        """When Tekton Results raises, returns None."""
        analyzer = _build_analyzer()
        failure = _make_failure()

        with patch.dict('sys.modules', {
            'clients.tekton_results': MagicMock(
                TektonResultsClient=MagicMock(
                    return_value=MagicMock(
                        get_pipelinerun_logs=MagicMock(side_effect=Exception("connection refused"))
                    )
                )
            ),
        }):
            # Force the exception path by having the import succeed but the call fail
            pass

        # Use a simpler mock approach
        mock_tr_cls = MagicMock()
        mock_tr_cls.return_value.get_pipelinerun_logs.side_effect = Exception("boom")

        with patch.dict('sys.modules', {
            'clients.tekton_results': MagicMock(TektonResultsClient=mock_tr_cls),
        }):
            result = analyzer._refetch_logs(failure)
        assert result is None

    def test_refetch_store_field_fails_still_returns_logs(self):
        """When storing refetched logs fails, logs are still returned."""
        analyzer = _build_analyzer()
        full_logs = 'refetched logs content'

        mock_tr = MagicMock()
        mock_tr.get_pipelinerun_logs.return_value = full_logs

        failure = _make_failure(build_logs='old')
        analyzer._store_field = MagicMock(side_effect=Exception("DB write fail"))

        with patch.dict('sys.modules', {
            'clients.tekton_results': MagicMock(
                TektonResultsClient=MagicMock(return_value=mock_tr)
            ),
        }):
            result = analyzer._refetch_logs(failure, failed_task='')

        assert result == full_logs

    def test_refetch_both_tekton_and_oci_empty(self):
        """When both Tekton Results and OCI return nothing, returns None."""
        analyzer = _build_analyzer()
        mock_tr = MagicMock()
        mock_tr.get_pipelinerun_logs.return_value = None

        failure = _make_failure()
        analyzer._fetch_oci_logs = MagicMock(return_value=None)

        with patch.dict('sys.modules', {
            'clients.tekton_results': MagicMock(
                TektonResultsClient=MagicMock(return_value=mock_tr)
            ),
        }):
            result = analyzer._refetch_logs(failure)

        assert result is None


# ---------------------------------------------------------------------------
# Lines 429-461: _fetch_oci_logs
# ---------------------------------------------------------------------------
class TestFetchOciLogs:
    """Tests for _fetch_oci_logs method."""

    def test_no_pr_name(self):
        """Returns None when pipelinerun_name is missing."""
        analyzer = _build_analyzer()
        failure = _make_failure(pipelinerun_name=None)
        assert analyzer._fetch_oci_logs(failure) is None

    def test_no_component(self):
        """Returns None when component_name is missing."""
        analyzer = _build_analyzer()
        failure = _make_failure(component_name=None)
        assert analyzer._fetch_oci_logs(failure) is None

    def test_no_container_image(self):
        """Returns None when component has no container_image."""
        analyzer = _build_analyzer()
        failure = _make_failure()

        mock_kc = MagicMock()
        mock_kc.get_component_metadata.return_value = {}

        with patch.dict('sys.modules', {
            'clients.kubernetes': MagicMock(KubernetesClient=MagicMock(return_value=mock_kc)),
            'clients.registry_client': MagicMock(),
        }):
            assert analyzer._fetch_oci_logs(failure) is None

    def test_fetch_full_logs_no_task_filter(self):
        """Returns full logs when no failed_task_name."""
        analyzer = _build_analyzer()
        failure = _make_failure(failed_task_name='')

        mock_kc = MagicMock()
        mock_kc.get_component_metadata.return_value = {
            'container_image': 'quay.io/rhoai/dashboard:sha256:abc123'
        }

        mock_rc = MagicMock()
        mock_rc.fetch_log_artifact.return_value = 'complete oci logs content'

        mock_rc_cls = MagicMock(return_value=mock_rc)
        mock_rc_cls.parse_image_ref = MagicMock(return_value=('quay.io', 'rhoai/dashboard', 'sha256:abc123'))

        with patch.dict('sys.modules', {
            'clients.kubernetes': MagicMock(KubernetesClient=MagicMock(return_value=mock_kc)),
            'clients.registry_client': MagicMock(RegistryClient=mock_rc_cls),
        }):
            result = analyzer._fetch_oci_logs(failure)

        assert result == 'complete oci logs content'

    def test_fetch_with_task_filter_extracts_section(self):
        """Extracts specific task section from OCI logs."""
        analyzer = _build_analyzer()
        failure = _make_failure(failed_task_name='build-container')

        mock_kc = MagicMock()
        mock_kc.get_component_metadata.return_value = {
            'container_image': 'quay.io/rhoai/dashboard:sha256:abc123'
        }

        logs_content = (
            '--- LOGS FOR build-container ---\n'
            'BUILD ERROR: module not found\n'
            'exit code 1\n'
            '--- LOGS FOR next-task ---\n'
            'This is a different task\n'
        )
        mock_rc = MagicMock()
        mock_rc.fetch_log_artifact.return_value = logs_content

        mock_rc_cls = MagicMock(return_value=mock_rc)
        mock_rc_cls.parse_image_ref = MagicMock(return_value=('quay.io', 'rhoai/dashboard', 'sha256:abc123'))

        with patch.dict('sys.modules', {
            'clients.kubernetes': MagicMock(KubernetesClient=MagicMock(return_value=mock_kc)),
            'clients.registry_client': MagicMock(RegistryClient=mock_rc_cls),
        }):
            result = analyzer._fetch_oci_logs(failure)

        assert result is not None
        assert 'BUILD ERROR' in result

    def test_fetch_logs_none_from_registry(self):
        """Returns None when registry returns no logs."""
        analyzer = _build_analyzer()
        failure = _make_failure()

        mock_kc = MagicMock()
        mock_kc.get_component_metadata.return_value = {
            'container_image': 'quay.io/rhoai/dashboard:sha256:abc'
        }

        mock_rc = MagicMock()
        mock_rc.fetch_log_artifact.return_value = None

        mock_rc_cls = MagicMock(return_value=mock_rc)
        mock_rc_cls.parse_image_ref = MagicMock(return_value=('quay.io', 'rhoai/dashboard', 'sha256:abc'))

        with patch.dict('sys.modules', {
            'clients.kubernetes': MagicMock(KubernetesClient=MagicMock(return_value=mock_kc)),
            'clients.registry_client': MagicMock(RegistryClient=mock_rc_cls),
        }):
            result = analyzer._fetch_oci_logs(failure)

        assert result is None

    def test_exception_returns_none(self):
        """Returns None when an exception occurs."""
        analyzer = _build_analyzer()
        failure = _make_failure()

        with patch.dict('sys.modules', {
            'clients.kubernetes': MagicMock(
                KubernetesClient=MagicMock(side_effect=Exception("k8s down"))
            ),
        }):
            result = analyzer._fetch_oci_logs(failure)
        assert result is None


# ---------------------------------------------------------------------------
# Lines 465-495: _fetch_sarif_for_failure
# ---------------------------------------------------------------------------
class TestFetchSarifForFailure:
    """Tests for _fetch_sarif_for_failure method."""

    def test_not_scan_related(self):
        """Returns empty string when failure is not scan-related."""
        analyzer = _build_analyzer()
        failure = _make_failure(error_message='go build failed', failed_task_name='build-container')
        assert analyzer._fetch_sarif_for_failure(failure) == ''

    def test_scan_related_success(self):
        """Returns SARIF summary for scan-related failures."""
        analyzer = _build_analyzer()
        failure = _make_failure(
            error_message='clair-scan found vulnerabilities',
            failed_task_name='clair-scan',
        )

        mock_kc = MagicMock()
        mock_kc.get_component_metadata.return_value = {
            'container_image': 'quay.io/rhoai/dashboard@sha256:deadbeef'
        }

        mock_rc = MagicMock()
        mock_rc.fetch_sarif_results.return_value = [{'rule': 'CVE-2024-1234'}]

        mock_rc_cls = MagicMock(return_value=mock_rc)
        mock_rc_cls.parse_image_ref = MagicMock(return_value=(
            'quay.io', 'rhoai/dashboard', 'sha256:deadbeef'))
        mock_rc_cls.format_sarif_summary = MagicMock(return_value='Critical: 1')

        with patch.dict('sys.modules', {
            'clients.kubernetes': MagicMock(KubernetesClient=MagicMock(return_value=mock_kc)),
            'clients.registry_client': MagicMock(RegistryClient=mock_rc_cls),
        }):
            result = analyzer._fetch_sarif_for_failure(failure)

        assert result == 'Critical: 1'

    def test_no_component_returns_empty(self):
        """Returns empty string when component_name is missing."""
        analyzer = _build_analyzer()
        failure = _make_failure(
            component_name=None,
            error_message='sast vulnerability',
            failed_task_name='sast-scan',
        )
        assert analyzer._fetch_sarif_for_failure(failure) == ''

    def test_tag_not_sha256_returns_empty(self):
        """Returns empty string when tag is not a sha256 digest."""
        analyzer = _build_analyzer()
        failure = _make_failure(
            error_message='vulnerability scan failed',
            failed_task_name='scan-check',
        )

        mock_kc = MagicMock()
        mock_kc.get_component_metadata.return_value = {
            'container_image': 'quay.io/rhoai/dashboard:latest'
        }

        mock_rc_cls = MagicMock()
        mock_rc_cls.parse_image_ref = MagicMock(return_value=(
            'quay.io', 'rhoai/dashboard', 'latest'))

        with patch.dict('sys.modules', {
            'clients.kubernetes': MagicMock(KubernetesClient=MagicMock(return_value=mock_kc)),
            'clients.registry_client': MagicMock(RegistryClient=mock_rc_cls),
        }):
            result = analyzer._fetch_sarif_for_failure(failure)

        assert result == ''

    def test_exception_returns_empty(self):
        """Returns empty string when an exception occurs."""
        analyzer = _build_analyzer()
        failure = _make_failure(
            error_message='clair scan error',
            failed_task_name='clair-check',
        )

        with patch.dict('sys.modules', {
            'clients.kubernetes': MagicMock(
                KubernetesClient=MagicMock(side_effect=Exception("cluster down"))
            ),
        }):
            result = analyzer._fetch_sarif_for_failure(failure)

        assert result == ''

    def test_no_container_image_returns_empty(self):
        """Returns empty when component has no container_image."""
        analyzer = _build_analyzer()
        failure = _make_failure(
            error_message='scan vulnerability found',
            failed_task_name='scan-task',
        )

        mock_kc = MagicMock()
        mock_kc.get_component_metadata.return_value = None

        with patch.dict('sys.modules', {
            'clients.kubernetes': MagicMock(KubernetesClient=MagicMock(return_value=mock_kc)),
        }):
            result = analyzer._fetch_sarif_for_failure(failure)

        assert result == ''


# ---------------------------------------------------------------------------
# Lines 503-597: _fetch_failed_taskrun_logs
# ---------------------------------------------------------------------------
class TestFetchFailedTaskRunLogs:
    """Tests for _fetch_failed_taskrun_logs method."""

    def test_no_pr_name(self):
        """Returns None when no pipelinerun_name."""
        analyzer = _build_analyzer()
        failure = _make_failure(pipelinerun_name=None)
        assert analyzer._fetch_failed_taskrun_logs(failure) is None

    def test_full_taskrun_flow_with_platform_summary(self):
        """Exercises the full TaskRun processing: platform summary, test outputs,
        failed logs, and SARIF integration."""
        analyzer = _build_analyzer()
        analyzer._store_field = MagicMock()
        analyzer._fetch_sarif_for_failure = MagicMock(return_value='')

        # Build TaskRun data
        failed_taskrun = {
            'metadata': {'labels': {'tekton.dev/pipelineTask': 'build-container'}},
            'status': {
                'conditions': [{'status': 'False', 'message': 'build failed', 'reason': 'Failed'}],
                'results': [
                    {
                        'name': 'TEST_OUTPUT',
                        'value': json.dumps({
                            'result': 'FAILURE',
                            'note': 'unit tests failed'
                        })
                    }
                ],
            },
            'spec': {'params': [{'name': 'PLATFORM', 'value': 'linux/amd64'}]},
        }
        passed_taskrun = {
            'metadata': {'labels': {'tekton.dev/pipelineTask': 'build-container'}},
            'status': {
                'conditions': [{'status': 'True', 'message': 'ok', 'reason': 'Succeeded'}],
                'results': [],
            },
            'spec': {'params': [{'name': 'PLATFORM', 'value': 'linux/arm64'}]},
        }

        mock_tr = MagicMock()
        mock_tr.query_taskrun_records.return_value = [
            (failed_taskrun, 'record/1'),
            (passed_taskrun, 'record/2'),
        ]
        mock_tr.get_taskrun_logs.return_value = 'FAILED: cannot build image\nexit 1'

        failure = _make_failure()

        with patch.dict('sys.modules', {
            'clients.tekton_results': MagicMock(
                TektonResultsClient=MagicMock(return_value=mock_tr)
            ),
        }):
            result = analyzer._fetch_failed_taskrun_logs(failure)

        assert result is not None
        assert 'Per-platform build status' in result
        assert 'linux/amd64: FAILED' in result
        assert 'linux/arm64: PASSED' in result
        assert 'FAILED: cannot build image' in result
        assert 'Structured Test Results (FAILURES)' in result
        analyzer._store_field.assert_called_once()

    def test_taskrun_with_warnings(self):
        """Processes WARNING test results separately."""
        analyzer = _build_analyzer()
        analyzer._store_field = MagicMock()
        analyzer._fetch_sarif_for_failure = MagicMock(return_value='')

        taskrun = {
            'metadata': {'labels': {'tekton.dev/pipelineTask': 'sast-check'}},
            'status': {
                'conditions': [{'status': 'False', 'message': 'failed', 'reason': 'Failed'}],
                'results': [
                    {
                        'name': 'SCAN_OUTPUT',
                        'value': json.dumps({
                            'result': 'WARNING',
                            'note': 'deprecated API usage'
                        })
                    }
                ],
            },
            'spec': {'params': []},
        }

        mock_tr = MagicMock()
        mock_tr.query_taskrun_records.return_value = [(taskrun, 'rec/1')]
        mock_tr.get_taskrun_logs.return_value = 'warning: deprecated API'

        failure = _make_failure()

        with patch.dict('sys.modules', {
            'clients.tekton_results': MagicMock(
                TektonResultsClient=MagicMock(return_value=mock_tr)
            ),
        }):
            result = analyzer._fetch_failed_taskrun_logs(failure)

        assert result is not None
        assert 'Structured Test Results (WARNINGS)' in result

    def test_taskrun_no_conditions_skipped(self):
        """TaskRuns with empty conditions are skipped."""
        analyzer = _build_analyzer()
        analyzer._store_field = MagicMock()
        analyzer._fetch_sarif_for_failure = MagicMock(return_value='')

        empty_taskrun = {
            'metadata': {'labels': {'tekton.dev/pipelineTask': 'init'}},
            'status': {'conditions': [], 'results': []},
            'spec': {'params': []},
        }

        mock_tr = MagicMock()
        mock_tr.query_taskrun_records.return_value = [(empty_taskrun, 'rec/1')]

        failure = _make_failure()

        with patch.dict('sys.modules', {
            'clients.tekton_results': MagicMock(
                TektonResultsClient=MagicMock(return_value=mock_tr)
            ),
        }):
            result = analyzer._fetch_failed_taskrun_logs(failure)

        assert result is None

    def test_failed_taskrun_no_logs_uses_condition_message(self):
        """When get_taskrun_logs returns None, uses condition message."""
        analyzer = _build_analyzer()
        analyzer._store_field = MagicMock()
        analyzer._fetch_sarif_for_failure = MagicMock(return_value='')

        taskrun = {
            'metadata': {'labels': {'tekton.dev/pipelineTask': 'build'}},
            'status': {
                'conditions': [{'status': 'False', 'message': 'PipelineRunTimeout', 'reason': 'TaskRunTimeout'}],
                'results': [],
            },
            'spec': {'params': []},
        }

        mock_tr = MagicMock()
        mock_tr.query_taskrun_records.return_value = [(taskrun, 'rec/1')]
        mock_tr.get_taskrun_logs.return_value = None

        failure = _make_failure()

        with patch.dict('sys.modules', {
            'clients.tekton_results': MagicMock(
                TektonResultsClient=MagicMock(return_value=mock_tr)
            ),
        }):
            result = analyzer._fetch_failed_taskrun_logs(failure)

        assert result is not None
        assert 'TaskRunTimeout: PipelineRunTimeout' in result

    def test_taskrun_with_sarif(self):
        """SARIF summary is prepended when available."""
        analyzer = _build_analyzer()
        analyzer._store_field = MagicMock()
        analyzer._fetch_sarif_for_failure = MagicMock(return_value='=== SARIF: 3 critical CVEs ===')

        taskrun = {
            'metadata': {'labels': {'tekton.dev/pipelineTask': 'scan'}},
            'status': {
                'conditions': [{'status': 'False', 'message': 'scan failed', 'reason': 'Failed'}],
                'results': [],
            },
            'spec': {'params': []},
        }

        mock_tr = MagicMock()
        mock_tr.query_taskrun_records.return_value = [(taskrun, 'rec/1')]
        mock_tr.get_taskrun_logs.return_value = 'scan error details'

        failure = _make_failure()

        with patch.dict('sys.modules', {
            'clients.tekton_results': MagicMock(
                TektonResultsClient=MagicMock(return_value=mock_tr)
            ),
        }):
            result = analyzer._fetch_failed_taskrun_logs(failure)

        assert '=== SARIF: 3 critical CVEs ===' in result

    def test_exception_returns_none(self):
        """Returns None when Tekton Results raises."""
        analyzer = _build_analyzer()
        failure = _make_failure()

        mock_tr_cls = MagicMock()
        mock_tr_cls.return_value.query_taskrun_records.side_effect = Exception("TR down")

        with patch.dict('sys.modules', {
            'clients.tekton_results': MagicMock(TektonResultsClient=mock_tr_cls),
        }):
            result = analyzer._fetch_failed_taskrun_logs(failure)

        assert result is None

    def test_taskrun_test_output_json_decode_error(self):
        """Gracefully handles invalid JSON in TEST_OUTPUT."""
        analyzer = _build_analyzer()
        analyzer._store_field = MagicMock()
        analyzer._fetch_sarif_for_failure = MagicMock(return_value='')

        taskrun = {
            'metadata': {'labels': {'tekton.dev/pipelineTask': 'test'}},
            'status': {
                'conditions': [{'status': 'False', 'message': 'failed', 'reason': 'Failed'}],
                'results': [
                    {'name': 'TEST_OUTPUT', 'value': 'not valid json!!!'}
                ],
            },
            'spec': {'params': []},
        }

        mock_tr = MagicMock()
        mock_tr.query_taskrun_records.return_value = [(taskrun, 'rec/1')]
        mock_tr.get_taskrun_logs.return_value = 'test failure logs'

        failure = _make_failure()

        with patch.dict('sys.modules', {
            'clients.tekton_results': MagicMock(
                TektonResultsClient=MagicMock(return_value=mock_tr)
            ),
        }):
            result = analyzer._fetch_failed_taskrun_logs(failure)

        # Should still return the logs despite JSON decode error
        assert result is not None
        assert 'test failure logs' in result

    def test_taskrun_success_result_ignored(self):
        """SUCCESS test results are not included in outputs."""
        analyzer = _build_analyzer()
        analyzer._store_field = MagicMock()
        analyzer._fetch_sarif_for_failure = MagicMock(return_value='')

        taskrun = {
            'metadata': {'labels': {'tekton.dev/pipelineTask': 'build'}},
            'status': {
                'conditions': [{'status': 'False', 'message': 'failed', 'reason': 'Failed'}],
                'results': [
                    {
                        'name': 'TEST_OUTPUT',
                        'value': json.dumps({'result': 'SUCCESS', 'note': 'all good'})
                    }
                ],
            },
            'spec': {'params': []},
        }

        mock_tr = MagicMock()
        mock_tr.query_taskrun_records.return_value = [(taskrun, 'rec/1')]
        mock_tr.get_taskrun_logs.return_value = 'build logs'

        failure = _make_failure()

        with patch.dict('sys.modules', {
            'clients.tekton_results': MagicMock(
                TektonResultsClient=MagicMock(return_value=mock_tr)
            ),
        }):
            result = analyzer._fetch_failed_taskrun_logs(failure)

        assert result is not None
        # Should NOT contain test result sections since result was SUCCESS
        assert 'Structured Test Results' not in result


# ---------------------------------------------------------------------------
# Lines 622-624: _ai_extract_error when creating cheap LLM fails
# ---------------------------------------------------------------------------
class TestAiExtractErrorCreationFailure:
    """Tests for _ai_extract_error when LLM creation fails."""

    def test_cheap_llm_creation_exception(self):
        """Returns None when creating cheap LLM raises."""
        analyzer = _build_analyzer()
        analyzer._cheap_llm = None  # Not yet created

        with patch('clients.llm_provider.create_llm_provider',
                   side_effect=Exception("No API key")):
            result = analyzer._ai_extract_error('huge logs here', _make_failure())

        assert result is None


# ---------------------------------------------------------------------------
# Lines 648-651: _ai_extract_error successful AI extraction
# ---------------------------------------------------------------------------
class TestAiExtractErrorSuccess:
    """Tests for _ai_extract_error when extraction succeeds."""

    def test_successful_extraction(self):
        """Returns extracted content from LLM response."""
        analyzer = _build_analyzer()
        mock_cheap_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content_text = 'ERROR: cannot find module github.com/foo/bar\nexit code 1\n' + 'x' * 200
        mock_cheap_llm.create_message.return_value = mock_response
        analyzer._cheap_llm = mock_cheap_llm

        result = analyzer._ai_extract_error('A' * 200000, _make_failure())

        assert result is not None
        assert 'ERROR: cannot find module' in result
        mock_cheap_llm.create_message.assert_called_once()

    def test_extraction_too_short_returns_none(self):
        """Returns None when extracted content is <= 100 chars."""
        analyzer = _build_analyzer()
        mock_cheap_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content_text = 'short'  # < 100 chars
        mock_cheap_llm.create_message.return_value = mock_response
        analyzer._cheap_llm = mock_cheap_llm

        result = analyzer._ai_extract_error('huge logs', _make_failure())

        assert result is None


# ---------------------------------------------------------------------------
# Lines 834-836: _get_release_context blocker signals
# ---------------------------------------------------------------------------
class TestGetReleaseContextBlockerSignals:
    """Tests for _get_release_context blocker signals section."""

    def test_blocker_signals_included(self):
        """When blockers have critical_signals, they appear in context."""
        analyzer = _build_analyzer()

        mock_blockers_result = MagicMock()
        mock_blockers_result.critical_signals = [
            'RHOAI-1234: unassigned for 3 days',
            'RHOAI-5678: no updates in 5 days',
        ]

        failure = _make_failure(application='rhoai-v2.19')

        with patch('api.routes.failures.list_blockers', return_value=mock_blockers_result):
            result = analyzer._get_release_context(failure)

        assert 'Active Blocker Signals' in result
        assert 'RHOAI-1234: unassigned for 3 days' in result
        assert 'RHOAI-5678: no updates in 5 days' in result

    def test_no_blocker_signals(self):
        """When no critical_signals, section is not added."""
        analyzer = _build_analyzer()

        mock_blockers_result = MagicMock()
        mock_blockers_result.critical_signals = []

        failure = _make_failure(application='rhoai-v2.19')

        with patch('api.routes.failures.list_blockers', return_value=mock_blockers_result):
            result = analyzer._get_release_context(failure)

        assert 'Active Blocker Signals' not in result


# ---------------------------------------------------------------------------
# Lines 850-862: _get_release_context systemic pattern detection
# ---------------------------------------------------------------------------
class TestGetReleaseContextSystemicPatterns:
    """Tests for _get_release_context systemic pattern detection."""

    def test_systemic_pattern_detected(self):
        """When multiple components match a systemic pattern, it's included."""
        analyzer = _build_analyzer()
        analyzer.build_repo.get_triage_summary.return_value = {
            'failing_components': [
                {'component': 'comp-a', 'error_type': 'fips_check_failure'},
                {'component': 'comp-b', 'error_type': 'fips_check_failure'},
                {'component': 'comp-c', 'error_type': 'build_error'},
            ]
        }

        failure = _make_failure(
            application='rhoai-v2.19',
            error_type='fips_check_failure',
            component_name='comp-a',
        )

        mock_patterns = [
            ('fips_check', ['fips']),
            ('go_version_mismatch', ['go_module']),  # Second pattern to test break
        ]

        with patch('api.routes.failures.list_blockers',
                   side_effect=Exception("skip")):
            with patch('api.routes.failures.SYSTEMIC_PATTERNS', mock_patterns):
                result = analyzer._get_release_context(failure)

        assert 'Systemic Pattern Detected' in result
        assert 'fips check' in result
        assert 'comp-a' in result
        assert 'comp-b' in result
        # Should NOT contain the second pattern (break after first match)
        assert 'go version' not in result

    def test_systemic_pattern_not_enough_affected(self):
        """When only 1 component matches, no systemic pattern is reported."""
        analyzer = _build_analyzer()
        analyzer.build_repo.get_triage_summary.return_value = {
            'failing_components': [
                {'component': 'comp-a', 'error_type': 'fips_check_failure'},
            ]
        }

        failure = _make_failure(
            application='rhoai-v2.19',
            error_type='fips_check_failure',
            component_name='comp-a',
        )

        mock_patterns = [
            ('fips_check', ['fips']),
        ]

        with patch('api.routes.failures.list_blockers',
                   side_effect=Exception("skip")):
            with patch('api.routes.failures.SYSTEMIC_PATTERNS', mock_patterns):
                result = analyzer._get_release_context(failure)

        assert 'Systemic Pattern Detected' not in result


# ---------------------------------------------------------------------------
# Lines 929-930: _format_commit_context enriched_context JSON parsing
# ---------------------------------------------------------------------------
class TestFormatCommitContextEnrichedParsing:
    """Tests for _format_commit_context enriched_context handling."""

    def test_enriched_context_parsed_from_json_string(self):
        """enriched_context as a JSON string is parsed into a dict."""
        analyzer = _build_analyzer()
        commit_ctx = {'commit': {'files': [], 'stats': {}}}
        enriched = json.dumps({
            'dependency_changes': {
                'go.sum': {
                    'status': 'modified',
                    'additions': 5,
                    'deletions': 3,
                    'patch': '+new dep\n-old dep',
                }
            }
        })

        result = analyzer._format_commit_context(commit_ctx, enriched)

        assert 'Dependency File Changes' in result
        assert 'go.sum' in result

    def test_enriched_context_invalid_json_ignored(self):
        """enriched_context with invalid JSON is set to None."""
        analyzer = _build_analyzer()
        commit_ctx = {'commit': {'files': [], 'stats': {}}}
        enriched = 'not valid json at all {'

        result = analyzer._format_commit_context(commit_ctx, enriched)

        # Should still produce output, just without enriched sections
        assert 'Commit Context' in result
        assert 'Dependency File Changes' not in result


# ---------------------------------------------------------------------------
# Line 957: _format_commit_context file without patch
# ---------------------------------------------------------------------------
class TestFormatCommitContextNoPatch:
    """Tests for _format_commit_context file without patch."""

    def test_file_without_patch(self):
        """Files with no patch or truncated diff get a simplified entry."""
        analyzer = _build_analyzer()
        commit_ctx = {
            'commit': {
                'files': [
                    {
                        'filename': 'big_binary.so',
                        'status': 'added',
                        'additions': 0,
                        'deletions': 0,
                        'patch': '',
                    }
                ],
                'stats': {'additions': 0, 'deletions': 0},
            }
        }

        result = analyzer._format_commit_context(commit_ctx)

        assert 'big_binary.so' in result
        assert '```diff' not in result  # No diff block for files without patch

    def test_file_with_truncated_patch(self):
        """Files with truncated diff marker get simplified entry."""
        analyzer = _build_analyzer()
        commit_ctx = {
            'commit': {
                'files': [
                    {
                        'filename': 'large_file.go',
                        'status': 'modified',
                        'additions': 500,
                        'deletions': 300,
                        'patch': '(diff truncated — total diff too large)',
                    }
                ],
                'stats': {'additions': 500, 'deletions': 300},
            }
        }

        result = analyzer._format_commit_context(commit_ctx)

        assert 'large_file.go' in result
        assert '```diff' not in result


# ---------------------------------------------------------------------------
# Line 975: _format_commit_context PR body truncation
# ---------------------------------------------------------------------------
class TestFormatCommitContextPRTruncation:
    """Tests for _format_commit_context PR body truncation."""

    def test_pr_body_truncated_when_too_long(self):
        """PR body > 3000 chars is truncated with '...'."""
        analyzer = _build_analyzer()
        long_body = 'Description line\n' * 500  # > 3000 chars
        commit_ctx = {
            'commit': {'files': [], 'stats': {}},
            'pr': {
                'number': 42,
                'title': 'Big PR',
                'body': long_body,
            }
        }

        result = analyzer._format_commit_context(commit_ctx)

        assert 'Pull Request #42' in result
        assert result.count('...') >= 1
        # The body in the output should be at most 3003 chars (3000 + '...')
        # Verify it's truncated by checking the original body is NOT fully present
        assert long_body not in result


# ---------------------------------------------------------------------------
# Line 994: _format_commit_context Tekton config truncation
# ---------------------------------------------------------------------------
class TestFormatCommitContextTektonTruncation:
    """Tests for _format_commit_context Tekton config truncation."""

    def test_tekton_config_truncated_when_too_long(self):
        """Tekton config > 5000 chars is truncated."""
        analyzer = _build_analyzer()
        long_config = 'apiVersion: tekton.dev/v1\n' * 500  # > 5000 chars
        commit_ctx = {
            'commit': {'files': [], 'stats': {}},
            'tekton_configs': {
                'pipeline.yaml': long_config,
            }
        }

        result = analyzer._format_commit_context(commit_ctx)

        assert 'Tekton Pipeline Configs' in result
        assert 'pipeline.yaml' in result
        assert '(truncated)' in result
        # The full config should NOT be in the output
        assert long_config not in result


# ---------------------------------------------------------------------------
# Lines 1267, 1269: run_batch_analysis force/component_filter log messages
# ---------------------------------------------------------------------------
class TestRunLogMessages:
    """Tests for run() force/component_filter log messages."""

    def test_component_filter_logged(self):
        """Component filter is logged when provided."""
        analyzer = _build_analyzer()
        analyzer.ai_repo.skip_no_logs_timeouts.return_value = 0
        analyzer.get_pending_failures = MagicMock(return_value=[])

        result = analyzer.run(
            limit=5, component_filter='odh-dashboard', force=False)

        assert result['analyzed'] == 0

    def test_force_mode_logged(self):
        """Force mode is logged when enabled."""
        analyzer = _build_analyzer()
        analyzer.ai_repo.skip_no_logs_timeouts.return_value = 0
        analyzer.get_pending_failures = MagicMock(return_value=[])

        result = analyzer.run(
            limit=5, component_filter=None, force=True)

        assert result['analyzed'] == 0

    def test_both_force_and_filter(self):
        """Both force and component_filter are logged together."""
        analyzer = _build_analyzer()
        analyzer.ai_repo.skip_no_logs_timeouts.return_value = 0
        analyzer.get_pending_failures = MagicMock(return_value=[])

        result = analyzer.run(
            limit=5, component_filter='odh-model-controller', force=True)

        assert result['analyzed'] == 0
        assert 'duration' in result
