"""Tests for ChangeTracker (map.change_tracker)."""

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock


class TestGraphChange:
    def test_to_dict(self):
        from map.change_tracker import GraphChange
        change = GraphChange(
            change_type="added",
            entity_type="node",
            entity_id="comp-new",
            entity_name="new-component",
            details={"label": "Component"},
        )
        d = change.to_dict()
        assert d["change_type"] == "added"
        assert d["entity_id"] == "comp-new"
        assert d["entity_name"] == "new-component"
        assert d["details"]["label"] == "Component"
        assert "timestamp" in d

    def test_defaults(self):
        from map.change_tracker import GraphChange
        change = GraphChange("removed", "node", "comp-x")
        d = change.to_dict()
        assert d["entity_name"] == ""
        assert d["details"] == {}


class TestChangeTrackerSnapshots:
    @pytest.fixture
    def mock_neo4j(self):
        driver = MagicMock()
        mock_session = MagicMock()
        driver.session.return_value.__enter__ = lambda self: mock_session
        driver.session.return_value.__exit__ = lambda self, *a: None
        return driver, mock_session

    @pytest.fixture
    def tracker(self, mock_neo4j):
        from map.change_tracker import ChangeTracker
        driver, _ = mock_neo4j
        return ChangeTracker(driver)

    def test_take_snapshot(self, tracker, mock_neo4j):
        _, mock_session = mock_neo4j
        # Mock node query
        node_result = MagicMock()
        node_result.__iter__ = lambda self: iter([
            {"id": "comp-a", "label": "Component", "name": "comp-a",
             "props": {"id": "comp-a", "name": "comp-a"}},
        ])
        # Mock edge query
        edge_result = MagicMock()
        edge_result.__iter__ = lambda self: iter([
            {"source": "app-1", "rel_type": "CONTAINS", "target": "comp-a"},
        ])
        mock_session.run.side_effect = [node_result, edge_result]

        snapshot = tracker._take_snapshot()
        assert "comp-a" in snapshot["nodes"]
        assert ("app-1", "CONTAINS", "comp-a") in snapshot["edges"]

    def test_take_snapshot_strips_metadata(self, tracker, mock_neo4j):
        _, mock_session = mock_neo4j
        node_result = MagicMock()
        node_result.__iter__ = lambda self: iter([
            {"id": "comp-a", "label": "Component", "name": "comp-a",
             "props": {"id": "comp-a", "_seeded_at": "2026-01-01", "_source": "seed"}},
        ])
        edge_result = MagicMock()
        edge_result.__iter__ = lambda self: iter([])
        mock_session.run.side_effect = [node_result, edge_result]

        snapshot = tracker._take_snapshot()
        assert "_seeded_at" not in snapshot["nodes"]["comp-a"]["props"]
        assert "_source" not in snapshot["nodes"]["comp-a"]["props"]

    def test_diff_added_node(self, tracker):
        old = {"nodes": {}, "edges": set()}
        new = {
            "nodes": {"comp-new": {"label": "Component", "name": "new", "props": {}}},
            "edges": set(),
        }
        changes = tracker.diff_snapshots(old, new)
        assert len(changes) == 1
        assert changes[0].change_type == "added"
        assert changes[0].entity_id == "comp-new"

    def test_diff_removed_node(self, tracker):
        old = {
            "nodes": {"comp-old": {"label": "Component", "name": "old", "props": {}}},
            "edges": set(),
        }
        new = {"nodes": {}, "edges": set()}
        changes = tracker.diff_snapshots(old, new)
        assert len(changes) == 1
        assert changes[0].change_type == "removed"
        assert changes[0].entity_id == "comp-old"

    def test_diff_modified_node(self, tracker):
        old = {
            "nodes": {"comp-a": {"label": "Component", "name": "a", "props": {"version": "1.0"}}},
            "edges": set(),
        }
        new = {
            "nodes": {"comp-a": {"label": "Component", "name": "a", "props": {"version": "2.0"}}},
            "edges": set(),
        }
        changes = tracker.diff_snapshots(old, new)
        assert len(changes) == 1
        assert changes[0].change_type == "modified"
        assert "version" in changes[0].details["changed_properties"]

    def test_diff_no_changes(self, tracker):
        snapshot = {
            "nodes": {"comp-a": {"label": "Component", "name": "a", "props": {"v": "1"}}},
            "edges": {("app-1", "CONTAINS", "comp-a")},
        }
        changes = tracker.diff_snapshots(snapshot, snapshot)
        assert len(changes) == 0

    def test_diff_added_edge(self, tracker):
        old = {"nodes": {}, "edges": set()}
        new = {"nodes": {}, "edges": {("comp-a", "NUDGES", "comp-b")}}
        changes = tracker.diff_snapshots(old, new)
        assert len(changes) == 1
        assert changes[0].entity_type == "relationship"
        assert changes[0].change_type == "added"
        assert changes[0].details["source"] == "comp-a"
        assert changes[0].details["target"] == "comp-b"

    def test_diff_removed_edge(self, tracker):
        old = {"nodes": {}, "edges": {("comp-a", "NUDGES", "comp-b")}}
        new = {"nodes": {}, "edges": set()}
        changes = tracker.diff_snapshots(old, new)
        assert len(changes) == 1
        assert changes[0].change_type == "removed"

    def test_diff_multiple_changes(self, tracker):
        old = {
            "nodes": {
                "comp-a": {"label": "Component", "name": "a", "props": {"v": "1"}},
                "comp-b": {"label": "Component", "name": "b", "props": {}},
            },
            "edges": {("comp-a", "NUDGES", "comp-b")},
        }
        new = {
            "nodes": {
                "comp-a": {"label": "Component", "name": "a", "props": {"v": "2"}},
                "comp-c": {"label": "Component", "name": "c", "props": {}},
            },
            "edges": {("comp-a", "NUDGES", "comp-c")},
        }
        changes = tracker.diff_snapshots(old, new)
        types = {c.change_type for c in changes}
        assert "added" in types    # comp-c + new edge
        assert "removed" in types  # comp-b + old edge
        assert "modified" in types  # comp-a version changed


class TestChangeTrackerMemory:
    @pytest.fixture
    def tracker(self):
        from map.change_tracker import ChangeTracker
        driver = MagicMock()
        return ChangeTracker(driver, pg_conn_factory=None)

    def test_record_and_query_memory(self, tracker):
        from map.change_tracker import GraphChange
        changes = [
            GraphChange("added", "node", "comp-x", "x"),
            GraphChange("removed", "node", "comp-y", "y"),
        ]
        tracker.record_changes(changes)

        result = tracker.get_changes()
        assert len(result) == 2

    def test_query_by_change_type(self, tracker):
        from map.change_tracker import GraphChange
        changes = [
            GraphChange("added", "node", "comp-x", "x"),
            GraphChange("removed", "node", "comp-y", "y"),
            GraphChange("added", "node", "comp-z", "z"),
        ]
        tracker.record_changes(changes)

        result = tracker.get_changes(change_type="added")
        assert len(result) == 2

    def test_query_by_entity_id(self, tracker):
        from map.change_tracker import GraphChange
        changes = [
            GraphChange("added", "node", "comp-kserve", "kserve"),
            GraphChange("added", "node", "comp-dashboard", "dashboard"),
        ]
        tracker.record_changes(changes)

        result = tracker.get_changes(entity_id="kserve")
        assert len(result) == 1

    def test_query_with_limit(self, tracker):
        from map.change_tracker import GraphChange
        changes = [GraphChange("added", "node", f"comp-{i}", f"c{i}") for i in range(10)]
        tracker.record_changes(changes)

        result = tracker.get_changes(limit=3)
        assert len(result) == 3

    def test_empty_changes_noop(self, tracker):
        tracker.record_changes([])
        assert tracker.get_changes() == []


class TestChangeTrackerPostgres:
    @pytest.fixture
    def mock_pg(self):
        conn = MagicMock()
        cursor = MagicMock()
        conn.cursor.return_value.__enter__ = lambda self: cursor
        conn.cursor.return_value.__exit__ = lambda self, *a: None
        return conn, cursor

    def test_ensure_table(self, mock_pg):
        from map.change_tracker import ChangeTracker
        conn, cursor = mock_pg
        tracker = ChangeTracker(MagicMock(), pg_conn_factory=lambda: conn)
        tracker.ensure_table()
        assert cursor.execute.called
        # Verify CREATE TABLE was called
        sql = cursor.execute.call_args[0][0]
        assert "graph_changes" in sql

    def test_record_changes_to_postgres(self, mock_pg):
        from map.change_tracker import ChangeTracker, GraphChange
        conn, cursor = mock_pg
        tracker = ChangeTracker(MagicMock(), pg_conn_factory=lambda: conn)

        changes = [GraphChange("added", "node", "comp-x", "x")]
        tracker.record_changes(changes, snapshot_id="snap-001")

        assert cursor.execute.called
        sql = cursor.execute.call_args[0][0]
        assert "INSERT INTO graph_changes" in sql
        assert conn.commit.called

    def test_get_changes_from_postgres(self, mock_pg):
        from map.change_tracker import ChangeTracker
        conn, cursor = mock_pg
        cursor.description = [
            ("id",), ("timestamp",), ("change_type",), ("entity_type",),
            ("entity_id",), ("entity_name",), ("node_label",), ("details",),
            ("snapshot_id",),
        ]
        cursor.fetchall.return_value = [
            (1, datetime(2026, 7, 10, tzinfo=timezone.utc), "added", "node",
             "comp-x", "x", "Component", {}, "snap-001"),
        ]

        tracker = ChangeTracker(MagicMock(), pg_conn_factory=lambda: conn)
        result = tracker.get_changes()
        assert len(result) == 1
        assert result[0]["entity_id"] == "comp-x"

    def test_get_changes_with_filters(self, mock_pg):
        from map.change_tracker import ChangeTracker
        conn, cursor = mock_pg
        cursor.description = [
            ("id",), ("timestamp",), ("change_type",), ("entity_type",),
            ("entity_id",), ("entity_name",), ("node_label",), ("details",),
            ("snapshot_id",),
        ]
        cursor.fetchall.return_value = []

        tracker = ChangeTracker(MagicMock(), pg_conn_factory=lambda: conn)
        tracker.get_changes(
            since=datetime(2026, 7, 1, tzinfo=timezone.utc),
            entity_id="comp-x",
            change_type="added",
        )

        sql = cursor.execute.call_args[0][0]
        assert "timestamp >=" in sql
        assert "entity_id LIKE" in sql
        assert "change_type =" in sql

    def test_ensure_table_noop_without_pg(self):
        from map.change_tracker import ChangeTracker
        tracker = ChangeTracker(MagicMock(), pg_conn_factory=None)
        tracker.ensure_table()  # should not raise


class TestRunDiff:
    @pytest.fixture
    def mock_neo4j(self):
        driver = MagicMock()
        mock_session = MagicMock()
        driver.session.return_value.__enter__ = lambda self: mock_session
        driver.session.return_value.__exit__ = lambda self, *a: None
        return driver, mock_session

    def test_first_run_no_previous(self, mock_neo4j):
        from map.change_tracker import ChangeTracker
        driver, mock_session = mock_neo4j

        node_result = MagicMock()
        node_result.__iter__ = lambda self: iter([])
        edge_result = MagicMock()
        edge_result.__iter__ = lambda self: iter([])
        mock_session.run.side_effect = [node_result, edge_result]

        tracker = ChangeTracker(driver)
        result = tracker.run_diff()
        assert result["first_run"] is True
        assert "snapshot" in result

    def test_diff_with_previous(self, mock_neo4j):
        from map.change_tracker import ChangeTracker
        driver, mock_session = mock_neo4j

        node_result = MagicMock()
        node_result.__iter__ = lambda self: iter([
            {"id": "comp-new", "label": "Component", "name": "new",
             "props": {"id": "comp-new", "name": "new"}},
        ])
        edge_result = MagicMock()
        edge_result.__iter__ = lambda self: iter([])
        mock_session.run.side_effect = [node_result, edge_result]

        tracker = ChangeTracker(driver)
        previous = {"nodes": {}, "edges": set()}
        result = tracker.run_diff(previous_snapshot=previous, snapshot_id="test-001")

        assert result["first_run"] is False
        assert result["summary"]["added"] == 1
        assert result["summary"]["total"] == 1

    def test_diff_with_no_changes(self, mock_neo4j):
        from map.change_tracker import ChangeTracker
        driver, mock_session = mock_neo4j

        node_result = MagicMock()
        node_result.__iter__ = lambda self: iter([
            {"id": "comp-a", "label": "Component", "name": "a",
             "props": {"id": "comp-a"}},
        ])
        edge_result = MagicMock()
        edge_result.__iter__ = lambda self: iter([])
        mock_session.run.side_effect = [node_result, edge_result]

        tracker = ChangeTracker(driver)
        previous = {
            "nodes": {"comp-a": {"label": "Component", "name": "a", "props": {"id": "comp-a"}}},
            "edges": set(),
        }
        result = tracker.run_diff(previous_snapshot=previous)

        assert result["summary"]["total"] == 0
        assert result["changes"] == []
