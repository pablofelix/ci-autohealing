"""Verify build_failure_repository logs warnings on DB errors."""
import logging
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def repo_with_broken_db():
    from repositories.build_failure_repository import BuildFailureRepository
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.connection.return_value.__enter__.return_value.cursor.return_value = mock_cursor
    mock_cursor.execute.side_effect = Exception("connection refused")
    mock_cursor.fetchone.side_effect = Exception("connection refused")
    mock_cursor.fetchall.side_effect = Exception("connection refused")
    return BuildFailureRepository(mock_db)


def test_find_unresolved_component_names_logs_on_error(repo_with_broken_db, caplog):
    with caplog.at_level(logging.WARNING):
        result = repo_with_broken_db.find_unresolved_component_names("rhoai-v3-5")
    assert result == set()
    assert any("find_unresolved_component_names" in r.message for r in caplog.records)


def test_find_failing_component_names_logs_on_error(repo_with_broken_db, caplog):
    with caplog.at_level(logging.WARNING):
        result = repo_with_broken_db.find_failing_component_names("rhoai-v3-5")
    assert result is None
    assert any("find_failing_component_names" in r.message for r in caplog.records)


def test_count_unresolved_logs_on_error(repo_with_broken_db, caplog):
    with caplog.at_level(logging.WARNING):
        result = repo_with_broken_db.count_unresolved("some-comp", "rhoai-v3-5")
    assert result == 0
    assert any("count_unresolved" in r.message for r in caplog.records)


def test_get_last_status_logs_on_error(repo_with_broken_db, caplog):
    with caplog.at_level(logging.WARNING):
        result = repo_with_broken_db.get_last_status("some-comp", "rhoai-v3-5")
    assert result is None
    assert any("get_last_status" in r.message for r in caplog.records)


def test_mark_resolved_logs_on_error(repo_with_broken_db, caplog):
    with caplog.at_level(logging.WARNING):
        result = repo_with_broken_db.mark_resolved("some-comp", "rhoai-v3-5", "default", "pr-123")
    assert result is False
    assert any("mark_resolved" in r.message for r in caplog.records)


def test_mark_resolved_deleted_logs_on_error(repo_with_broken_db, caplog):
    with caplog.at_level(logging.WARNING):
        result = repo_with_broken_db.mark_resolved_deleted("some-comp", "rhoai-v3-5")
    assert result is False
    assert any("mark_resolved_deleted" in r.message for r in caplog.records)


def test_update_failure_nature_logs_on_error(repo_with_broken_db, caplog):
    with caplog.at_level(logging.WARNING):
        result = repo_with_broken_db.update_failure_nature(1, "dependency_issue")
    assert result is False
    assert any("update_failure_nature" in r.message for r in caplog.records)


def test_get_unresolved_failure_logs_on_error(repo_with_broken_db, caplog):
    with caplog.at_level(logging.WARNING):
        result = repo_with_broken_db.get_unresolved_failure("some-comp", "rhoai-v3-5")
    assert result is None
    assert any("get_unresolved_failure" in r.message for r in caplog.records)


def test_record_successful_build_logs_on_error(repo_with_broken_db, caplog):
    with caplog.at_level(logging.WARNING):
        result = repo_with_broken_db.record_successful_build(
            "comp", "pr-123", "uid-123", "rhoai-v3-5", "default",
            "https://github.com/foo/bar.git", "main"
        )
    assert result is False
    assert any("record_successful_build" in r.message for r in caplog.records)


def test_upsert_failure_logs_on_error(repo_with_broken_db, caplog):
    with caplog.at_level(logging.WARNING):
        try:
            repo_with_broken_db.upsert_failure(
                "pr-123", "uid-123", "comp", "rhoai-v3-5", "default",
                "https://github.com/foo/bar.git", "main", "Failed"
            )
            raise AssertionError("Expected exception to be raised")
        except Exception:  # noqa: B014
            pass
    assert any("upsert_failure" in r.message for r in caplog.records)
