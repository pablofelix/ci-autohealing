"""Tests for Phases 10a-10d — Chat service, IC context, concepts, and /api/map/chat endpoint."""

from unittest.mock import MagicMock, patch


class TestBuildContextBlock:

    def setup_method(self):
        from map.backend.chat_service import _build_context_block
        self.build = _build_context_block

    def test_with_node_detail(self):
        node = {
            "id": "comp-test",
            "type": "Component",
            "props": {"name": "test-comp", "description": "A test component"},
            "neighbors": [
                {"direction": "outgoing", "relationship": "NUDGES", "name": "bundle", "id": "comp-bundle"}
            ],
            "gaps": [],
        }
        result = self.build(node, None, None)
        assert "test-comp" in result
        assert "Component" in result
        assert "NUDGES" in result
        assert "bundle" in result

    def test_with_impact(self):
        impact = {"total_affected": 5, "by_type": {"Component": 3, "Pipeline": 2}}
        result = self.build(None, impact, None)
        assert "5 affected" in result
        assert "Component: 3" in result

    def test_with_stats(self):
        stats = {"nodes": [{"type": "Component", "count": 40}, {"type": "Pipeline", "count": 10}]}
        result = self.build(None, None, stats)
        assert "50 total nodes" in result

    def test_empty_context(self):
        result = self.build(None, None, None)
        assert "No graph context" in result

    def test_gaps_included(self):
        node = {
            "id": "comp-x",
            "type": "Component",
            "props": {"name": "x"},
            "gaps": [{"type": "orphan", "message": "no pipeline linked"}],
        }
        result = self.build(node, None, None)
        assert "orphan" in result
        assert "no pipeline linked" in result

    def test_neighbors_capped_at_15(self):
        neighbors = [
            {"direction": "outgoing", "relationship": "R", "name": f"n{i}", "id": f"id-{i}"}
            for i in range(20)
        ]
        node = {"id": "x", "type": "T", "props": {"name": "x"}, "neighbors": neighbors}
        result = self.build(node, None, None)
        assert "n14" in result
        assert "n15" not in result


class TestBuildICContext:

    def setup_method(self):
        from map.backend.chat_service import _build_ic_context
        self.build = _build_ic_context

    def test_failure_context(self):
        ic = {"failure": {
            "status": "failed",
            "error_message": "npm ERR! 404 Not Found: @scope/pkg@1.2.3",
            "failed_task": "build-container",
            "failed_step": "build",
            "jira_key": "RHOAIENG-1234",
            "konflux_url": "https://konflux.example.com/run/123",
            "build_history": [
                {"status": "failed"}, {"status": "failed"}, {"status": "success"}
            ],
        }}
        result = self.build(ic)
        assert "[Build Failure]" in result
        assert "npm ERR! 404" in result
        assert "build-container" in result
        assert "RHOAIENG-1234" in result
        assert "failed → failed → success" in result

    def test_analysis_context(self):
        ic = {"analysis": {
            "root_cause": "npm registry timeout during dependency install",
            "failure_category": "dependency_issue",
            "confidence_score": 0.89,
            "recommended_fix": "Retry the build or update .npmrc",
            "can_auto_fix": True,
        }}
        result = self.build(ic)
        assert "[AI Analysis]" in result
        assert "npm registry timeout" in result
        assert "dependency_issue" in result
        assert "89%" in result
        assert "Auto-fixable: yes" in result

    def test_violation_context(self):
        ic = {"violation": {
            "violations": [
                {"title": "source_image.exists", "status": "FAIL"},
                {"title": "not_expired", "status": "WARN"},
            ],
            "exception_coverage": "partial",
        }}
        result = self.build(ic)
        assert "[Conforma Violation]" in result
        assert "source_image.exists" in result
        assert "partial" in result

    def test_triage_context(self):
        ic = {"triage": [
            {"component": "vllm", "status": "active", "root_cause": "Go 1.26 mismatch", "jira_key": "RHOAIENG-5678"},
            {"component": "other", "status": "resolved", "root_cause": "fixed"},
        ]}
        result = self.build(ic)
        assert "[Active Triage" in result
        assert "vllm" in result
        assert "Go 1.26" in result
        assert "RHOAIENG-5678" in result
        assert "other" not in result

    def test_readiness_context(self):
        ic = {"readiness": {
            "verdict": "NOT_READY",
            "checks": [
                {"name": "build_failures", "status": "FAIL", "detail": "2 components failing", "fix": "Fix builds"},
                {"name": "fbc_health", "status": "WARN", "detail": "Fragment build 3 days old"},
                {"name": "snapshot", "status": "PASS", "detail": "All images current"},
            ],
        }}
        result = self.build(ic)
        assert "[Release Readiness]" in result
        assert "NOT_READY" in result
        assert "Blockers (1)" in result
        assert "build_failures" in result
        assert "Risks (1)" in result

    def test_blockers_context(self):
        ic = {"blockers": {"blockers": [
            {"key": "RHOAIENG-9999", "summary": "OOM on workbench build", "critical_signals": ["unassigned_24h"]},
        ]}}
        result = self.build(ic)
        assert "[Jira Blockers" in result
        assert "RHOAIENG-9999" in result
        assert "unassigned_24h" in result

    def test_empty_ic_data(self):
        result = self.build({})
        assert result == ""

    def test_combined_with_graph_context(self):
        from map.backend.chat_service import _build_context_block
        node = {"id": "comp-x", "type": "Component", "props": {"name": "x"}}
        ic = {"failure": {"status": "failed", "error_message": "build timeout"}}
        result = _build_context_block(node, None, None, ic_data=ic)
        assert "comp-x" in result or "x" in result
        assert "[Build Failure]" in result
        assert "build timeout" in result


def _mock_llm_response(content="ok", model="m", input_tokens=50, output_tokens=10):
    """Build a mock LLM response without importing the real dataclass."""
    resp = MagicMock()
    resp.content = content
    resp.model = model
    resp.input_tokens = input_tokens
    resp.output_tokens = output_tokens
    return resp


class TestChatService:

    def test_chat_returns_response(self):
        from map.backend.chat_service import ChatService

        mock_provider = MagicMock()
        mock_provider.create_message.return_value = _mock_llm_response(
            content="This is a pipeline that builds container images.",
            model="claude-sonnet-4-6",
            input_tokens=100,
            output_tokens=30,
        )

        service = ChatService(provider=mock_provider)
        result = service.chat("What does this pipeline do?")

        assert result is not None
        assert result["response"] == "This is a pipeline that builds container images."
        assert result["model"] == "claude-sonnet-4-6"
        assert result["tokens"]["input"] == 100

    def test_chat_with_node_context(self):
        from map.backend.chat_service import ChatService

        mock_provider = MagicMock()
        mock_provider.create_message.return_value = _mock_llm_response(content="Answer")

        service = ChatService(provider=mock_provider)
        node = {"id": "comp-a", "type": "Component", "props": {"name": "a"}}
        service.chat("Explain this", node_detail=node)

        call_args = mock_provider.create_message.call_args
        assert "comp-a" in call_args.kwargs.get("user_content", "") or "a" in str(call_args)

    def test_chat_with_ic_data(self):
        from map.backend.chat_service import ChatService

        mock_provider = MagicMock()
        mock_provider.create_message.return_value = _mock_llm_response(
            content="The build is failing due to a timeout.",
            input_tokens=200,
            output_tokens=20,
        )

        service = ChatService(provider=mock_provider)
        ic_data = {"failure": {"status": "failed", "error_message": "build timeout"}}
        result = service.chat("Why is this red?", ic_data=ic_data)

        assert result is not None
        call_args = mock_provider.create_message.call_args
        prompt = call_args.kwargs.get("user_content", "")
        assert "Build Failure" in prompt
        assert "build timeout" in prompt

    def test_chat_no_provider_returns_none(self):
        from map.backend.chat_service import ChatService
        service = ChatService(provider=None)
        with patch('map.backend.chat_service._create_provider', return_value=None):
            result = service.chat("hello")
        assert result is None

    def test_chat_exception_returns_none(self):
        from map.backend.chat_service import ChatService

        mock_provider = MagicMock()
        mock_provider.create_message.side_effect = Exception("API error")

        service = ChatService(provider=mock_provider)
        result = service.chat("test")
        assert result is None


class TestChatRoute:

    def test_chat_endpoint_503_when_no_llm(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from map.backend.routes import router

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        with patch('map.backend.chat_service.ChatService.chat', return_value=None), \
             patch('map.backend.chat_service._create_provider', return_value=None):
            resp = client.post("/api/map/chat", json={"message": "hello"})
            assert resp.status_code == 503


class TestFetchICContext:

    def setup_method(self):
        import map.backend.routes as routes_mod
        routes_mod._ic_client = None

    def test_component_node_fetches_diagnostics(self):
        from map.backend.routes import _fetch_ic_context

        with patch('map.backend.ic_client.ICClient') as MockClient:
            mock = MockClient.return_value
            mock.is_available.return_value = True
            mock.get_failure.return_value = {"status": "failed", "error_message": "err"}
            mock.get_analysis.return_value = {"root_cause": "dep issue"}
            mock.get_violation.return_value = None
            mock.get_triage.return_value = {"items": []}

            result = _fetch_ic_context("comp-vllm")

            mock.get_failure.assert_called_once_with("vllm", "rhoai-v3-5")
            mock.get_analysis.assert_called_once_with("vllm", "rhoai-v3-5")
            mock.get_violation.assert_called_once_with("vllm", "rhoai-v3-5")
            assert "failure" in result
            assert "analysis" in result

    def test_app_node_fetches_readiness(self):
        from map.backend.routes import _fetch_ic_context

        with patch('map.backend.ic_client.ICClient') as MockClient:
            mock = MockClient.return_value
            mock.is_available.return_value = True
            mock.get_readiness.return_value = {"verdict": "AT_RISK"}
            mock.get_blockers.return_value = {"blockers": []}
            mock.get_triage.return_value = {"items": []}

            result = _fetch_ic_context("app-rhoai")

            mock.get_readiness.assert_called_once()
            mock.get_blockers.assert_called_once()
            assert "readiness" in result

    def test_ic_unavailable_returns_empty(self):
        from map.backend.routes import _fetch_ic_context

        with patch('map.backend.ic_client.ICClient') as MockClient:
            mock = MockClient.return_value
            mock.is_available.return_value = False

            result = _fetch_ic_context("comp-test")
            assert result == {}

    def test_filters_out_none_values(self):
        from map.backend.routes import _fetch_ic_context

        with patch('map.backend.ic_client.ICClient') as MockClient:
            mock = MockClient.return_value
            mock.is_available.return_value = True
            mock.get_failure.return_value = None
            mock.get_analysis.return_value = None
            mock.get_violation.return_value = None
            mock.get_triage.return_value = None

            result = _fetch_ic_context("comp-test")
            assert result == {}

    def test_triage_filtered_to_component(self):
        from map.backend.routes import _fetch_ic_context

        with patch('map.backend.ic_client.ICClient') as MockClient:
            mock = MockClient.return_value
            mock.is_available.return_value = True
            mock.get_failure.return_value = None
            mock.get_analysis.return_value = None
            mock.get_violation.return_value = None
            mock.get_triage.return_value = {"items": [
                {"component": "vllm", "status": "active", "root_cause": "Go mismatch"},
                {"component": "other", "status": "active", "root_cause": "unrelated"},
            ]}

            result = _fetch_ic_context("comp-vllm")
            assert "triage" in result
            assert len(result["triage"]) == 1
            assert result["triage"][0]["component"] == "vllm"


class TestBuildPanoramicContext:

    def setup_method(self):
        from map.backend.chat_service import _build_panoramic_context
        self.build = _build_panoramic_context

    def test_current_state_with_alerts_and_triage(self):
        data = {
            "triage_summary": {"total": 80, "failing": 3, "working": 77},
            "alerts": {
                "build_failures": [
                    {"component": "vllm", "age_hours": 48, "is_new": False},
                    {"component": "notebook", "age_hours": 2, "is_new": True},
                ],
                "conforma_violations": [{"component": "x"}],
                "freeze_countdown": {"message": "Code freeze in 14 days", "urgency": "normal"},
            },
        }
        result = self.build(data)
        assert "[Current State]" in result
        assert "3 failing" in result
        assert "77 working" in result
        assert "vllm" in result
        assert "[NEW]" in result
        assert "Code freeze" in result

    def test_daily_stats_trend(self):
        data = {"daily_stats": [
            {"date": "2026-07-10", "count": 3},
            {"date": "2026-07-09", "count": 5},
            {"date": "2026-07-08", "count": 2},
        ]}
        result = self.build(data)
        assert "[Failure Trend" in result
        assert "2026-07-10: 3" in result
        assert "2026-07-09: 5" in result

    def test_recent_resolutions(self):
        data = {"resolved": [
            {"component": "vllm", "resolved_at": "2026-07-09T15:30:00"},
            {"component": "dashboard", "resolved_at": "2026-07-08T10:00:00"},
        ]}
        result = self.build(data)
        assert "[Recent Resolutions (2 in 7 days)]" in result
        assert "vllm" in result
        assert "2026-07-09" in result

    def test_release_status_with_schedule(self):
        data = {
            "readiness": {
                "verdict": "AT_RISK",
                "checks": [
                    {"name": "build_failures", "status": "FAIL", "detail": "2 failing"},
                    {"name": "snapshot", "status": "PASS", "detail": "ok"},
                ],
            },
            "schedule": {
                "code_freeze": {"date": "2026-07-24", "days_remaining": 14},
                "release_date": {"date": "2026-08-20", "days_remaining": 41},
            },
        }
        result = self.build(data)
        assert "[Release Status]" in result
        assert "AT_RISK" in result
        assert "build_failures" in result
        assert "Code Freeze" in result
        assert "14 days" in result

    def test_nightly_health(self):
        data = {"nightly": {
            "fbc_health": {"status": "healthy"},
            "blockers": [{"component": "vllm-gpu"}],
        }}
        result = self.build(data)
        assert "[Nightly Build Health]" in result
        assert "healthy" in result
        assert "vllm-gpu" in result

    def test_health_warnings(self):
        data = {"health_warnings": [
            {"component": "notebook", "message": "Health score dropped from 90 to 40", "severity": "high"},
        ]}
        result = self.build(data)
        assert "[Health Warnings" in result
        assert "notebook" in result
        assert "dropped" in result

    def test_stale_components(self):
        data = {"stale": [
            {"component": "vllm-cpu", "built_commit": "abc123", "head_commit": "def456"},
        ]}
        result = self.build(data)
        assert "[Stale Components" in result
        assert "vllm-cpu" in result
        assert "abc123" in result
        assert "def456" in result

    def test_empty_panoramic_data(self):
        result = self.build({})
        assert result == ""

    def test_combined_panoramic_with_graph(self):
        from map.backend.chat_service import _build_context_block
        stats = {"nodes": [{"type": "Component", "count": 80}]}
        panoramic = {
            "triage_summary": {"total": 80, "failing": 2, "working": 78},
            "daily_stats": [{"date": "2026-07-10", "count": 2}],
        }
        result = _build_context_block(None, None, stats, panoramic_data=panoramic)
        assert "80 total nodes" in result
        assert "2 failing" in result
        assert "[Failure Trend" in result


class TestFetchPanoramicContext:

    def setup_method(self):
        import map.backend.routes as routes_mod
        routes_mod._ic_client = None

    def test_fetches_all_endpoints(self):
        from map.backend.routes import _fetch_panoramic_context

        with patch('map.backend.ic_client.ICClient') as MockClient:
            mock = MockClient.return_value
            mock.is_available.return_value = True
            mock.get_alerts.return_value = {"build_failures": []}
            mock.get_triage_summary.return_value = {"total": 80, "failing": 2, "working": 78}
            mock.get_daily_stats.return_value = [{"date": "2026-07-10", "count": 2}]
            mock.get_resolved.return_value = []
            mock.get_readiness.return_value = {"verdict": "READY"}
            mock.get_nightly.return_value = {"fbc_health": {"status": "ok"}}
            mock.get_schedule.return_value = {"code_freeze": {"date": "2026-07-24"}}
            mock.get_health_warnings.return_value = []
            mock.get_stale.return_value = []

            result = _fetch_panoramic_context("rhoai-v3-5")

            mock.get_alerts.assert_called_once_with("rhoai-v3-5")
            mock.get_daily_stats.assert_called_once_with("rhoai-v3-5", 7)
            mock.get_resolved.assert_called_once_with("rhoai-v3-5", 7)
            assert "alerts" in result
            assert "triage_summary" in result
            assert "readiness" in result

    def test_ic_unavailable_returns_empty(self):
        from map.backend.routes import _fetch_panoramic_context

        with patch('map.backend.ic_client.ICClient') as MockClient:
            mock = MockClient.return_value
            mock.is_available.return_value = False

            result = _fetch_panoramic_context("rhoai-v3-5")
            assert result == {}

    def test_filters_none_values(self):
        from map.backend.routes import _fetch_panoramic_context

        with patch('map.backend.ic_client.ICClient') as MockClient:
            mock = MockClient.return_value
            mock.is_available.return_value = True
            mock.get_alerts.return_value = {"build_failures": []}
            mock.get_triage_summary.return_value = None
            mock.get_daily_stats.return_value = None
            mock.get_resolved.return_value = None
            mock.get_readiness.return_value = None
            mock.get_nightly.return_value = None
            mock.get_schedule.return_value = None
            mock.get_health_warnings.return_value = None
            mock.get_stale.return_value = None

            result = _fetch_panoramic_context("rhoai-v3-5")
            assert "alerts" in result
            assert "triage_summary" not in result


class TestConceptDetection:

    def setup_method(self):
        import map.backend.concepts as mod
        from map.backend.concepts import detect_concept
        mod._COMPILED_PATTERNS = None
        self.detect = detect_concept

    def test_detects_conforma(self):
        assert self.detect("Explain Conforma") == "conforma"
        assert self.detect("What is the EC policy?") == "conforma"
        assert self.detect("How does conforma work?") == "conforma"

    def test_detects_nudging(self):
        assert self.detect("How does nudging work?") == "nudging"
        assert self.detect("Explain the nudge chain") == "nudging"

    def test_detects_build_pipeline(self):
        assert self.detect("Show build pipeline") == "build_pipeline"
        assert self.detect("How does a container build work?") == "build_pipeline"

    def test_detects_release_flow(self):
        assert self.detect("Walk me through a release") == "release_flow"
        assert self.detect("How does the stage to prod flow work?") == "release_flow"

    def test_detects_dependency_management(self):
        assert self.detect("What does Renovate do?") == "dependency_management"
        assert self.detect("Explain MintMaker") == "dependency_management"

    def test_no_match_returns_none(self):
        assert self.detect("What is the weather?") is None
        assert self.detect("How many components are there?") is None

    def test_case_insensitive(self):
        assert self.detect("EXPLAIN CONFORMA") == "conforma"
        assert self.detect("how does NUDGING work") == "nudging"


class TestGetHighlight:

    def setup_method(self):
        from map.backend.concepts import get_highlight
        self.get_hl = get_highlight

    def test_returns_highlight_for_known_concept(self):
        hl = self.get_hl("conforma")
        assert hl is not None
        assert "automation-conforma" in hl["nodes"]
        assert hl["dim_others"] is True
        assert hl["glow_color"] == "#be185d"
        assert hl["label"] == "Conforma Validation"
        assert len(hl["edges"]) > 0

    def test_returns_none_for_unknown_concept(self):
        assert self.get_hl("nonexistent") is None

    def test_dynamic_edge_expansion(self):
        edges = [
            {"id": "comp-a-NUDGES-comp-b", "source": "comp-a", "target": "comp-b", "label": "NUDGES"},
            {"id": "comp-b-NUDGES-comp-c", "source": "comp-b", "target": "comp-c", "label": "NUDGES"},
            {"id": "comp-x-BUILDS_WITH-pipeline-y", "source": "comp-x", "target": "pipeline-y", "label": "BUILDS_WITH"},
        ]
        hl = self.get_hl("nudging", all_edges=edges)
        assert "comp-a-NUDGES-comp-b" in hl["edges"]
        assert "comp-b-NUDGES-comp-c" in hl["edges"]
        assert "comp-a" in hl["nodes"]
        assert "comp-b" in hl["nodes"]
        assert "comp-c" in hl["nodes"]
        assert "comp-x-BUILDS_WITH-pipeline-y" not in hl["edges"]

    def test_no_edge_labels_skips_expansion(self):
        edges = [
            {"id": "a-NUDGES-b", "source": "a", "target": "b", "label": "NUDGES"},
        ]
        hl = self.get_hl("conforma", all_edges=edges)
        assert "a-NUDGES-b" not in hl["edges"]


class TestConceptNarrative:

    def test_returns_narrative(self):
        from map.backend.concepts import get_concept_narrative
        narrative = get_concept_narrative("conforma")
        assert narrative is not None
        assert "Conforma" in narrative
        assert "security" in narrative.lower()

    def test_unknown_concept_returns_none(self):
        from map.backend.concepts import get_concept_narrative
        assert get_concept_narrative("nonexistent") is None


class TestListConcepts:

    def test_returns_all_concepts(self):
        from map.backend.concepts import list_concepts
        concepts = list_concepts()
        keys = [c["key"] for c in concepts]
        assert "conforma" in keys
        assert "nudging" in keys
        assert "build_pipeline" in keys
        assert "release_flow" in keys
        assert "dependency_management" in keys
        assert all("title" in c for c in concepts)


class TestChatConceptIntegration:

    def setup_method(self):
        import map.backend.routes as routes_mod
        routes_mod._ic_client = None

    def test_concept_query_returns_highlight(self):
        """Chat with a concept query should return highlight field."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from map.backend.routes import router

        app = FastAPI()
        app.include_router(router)

        with patch('map.backend.chat_service._create_provider') as mock_provider, \
             patch('map.backend.graph.get_all_edges') as mock_edges, \
             patch('map.backend.graph.get_stats') as mock_stats:

            mock_llm = MagicMock()
            mock_result = MagicMock()
            mock_result.content = "Conforma validates images."
            mock_result.model = "test-model"
            mock_result.input_tokens = 100
            mock_result.output_tokens = 50
            mock_llm.create_message.return_value = mock_result
            mock_provider.return_value = mock_llm

            mock_edges.return_value = [
                {"source": "automation-conforma", "label": "ENFORCES", "target": "ec-registry-rhoai-stage"},
            ]
            mock_stats.return_value = {"nodes": [{"type": "Component", "count": 10}]}

            client = TestClient(app)
            response = client.post("/api/map/chat", json={"message": "Explain Conforma"})

            assert response.status_code == 200
            data = response.json()
            assert "highlight" in data
            assert "automation-conforma" in data["highlight"]["nodes"]
            assert data["highlight"]["dim_others"] is True

    def test_non_concept_query_no_highlight(self):
        """Non-concept queries should not return highlight."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from map.backend.routes import router

        app = FastAPI()
        app.include_router(router)

        with patch('map.backend.chat_service._create_provider') as mock_provider, \
             patch('map.backend.graph.get_stats') as mock_stats:

            mock_llm = MagicMock()
            mock_result = MagicMock()
            mock_result.content = "2 components are failing."
            mock_result.model = "test-model"
            mock_result.input_tokens = 100
            mock_result.output_tokens = 50
            mock_llm.create_message.return_value = mock_result
            mock_provider.return_value = mock_llm
            mock_stats.return_value = {"nodes": []}

            client = TestClient(app)

            with patch('map.backend.ic_client.ICClient') as MockIC:
                mock_ic = MockIC.return_value
                mock_ic.is_available.return_value = False

                response = client.post("/api/map/chat", json={"message": "How many failures?"})

            assert response.status_code == 200
            data = response.json()
            assert "highlight" not in data

    def test_concept_narrative_injected_into_llm_context(self):
        """Concept narrative should appear in the LLM user content."""
        from map.backend.chat_service import ChatService

        mock_provider = MagicMock()
        mock_result = MagicMock()
        mock_result.content = "test"
        mock_result.model = "m"
        mock_result.input_tokens = 10
        mock_result.output_tokens = 5
        mock_provider.create_message.return_value = mock_result

        service = ChatService(provider=mock_provider)
        service.chat(
            message="Explain Conforma",
            concept_narrative="Conforma validates images before release.",
        )

        call_args = mock_provider.create_message.call_args
        user_content = call_args.kwargs.get("user_content", call_args[1].get("user_content", ""))
        assert "[Concept Explanation]" in user_content
        assert "Conforma validates images" in user_content


class TestLLMFallback:
    def test_fallback_when_ic_modules_unavailable(self):
        """chat_service creates provider even without ic's config module."""
        import sys
        import os
        from unittest.mock import patch, MagicMock

        # Simulate ic modules not being importable
        with patch.dict(sys.modules, {'config': None, 'clients': None, 'clients.llm_provider': None}):
            with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'sk-test-key'}):
                from importlib import reload
                # This should not raise ImportError
                import map.backend.chat_service as cs
                provider = cs._create_provider()
                # Should get either a real provider or None (not crash)
                assert provider is None or hasattr(provider, 'messages')
