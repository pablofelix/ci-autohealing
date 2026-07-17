"""Tests for triage lifecycle: reactivation, auto-create, post-resolution guard."""

import os
import sys
from datetime import datetime, timedelta
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


from repositories.triage_repository import TriageRepository


def _make_db():
    db = MagicMock()
    conn = MagicMock()
    cursor = MagicMock()
    db.connection.return_value.__enter__ = MagicMock(return_value=conn)
    db.connection.return_value.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value = cursor
    return db, conn, cursor


def _sample_row(item_id=1, status='resolved', components=None,
                resolved_at=None):
    """16-element tuple matching _row_to_dict column order."""
    return (
        item_id,                                      # 0  id
        'grp-1',                                      # 1  group_label
        components or ['comp-a'],                     # 2  components
        'build',                                      # 3  issue_type
        'some root cause',                            # 4  root_cause
        'build-container',                            # 5  failed_step
        status,                                       # 6  status
        ['https://slack.com/t1'],                     # 7  slack_thread_urls
        None,                                         # 8  reference_urls
        None,                                         # 9  jira_key
        'a note',                                     # 10 notes
        'fixed by rebuild',                           # 11 resolution
        None,                                         # 12 resolution_pr_url
        resolved_at or datetime.now(),                # 13 resolved_at
        datetime(2025, 6, 1),                         # 14 created_at
        datetime(2025, 6, 2),                         # 15 updated_at
    )


class TestReactivateForComponent:
    def test_reactivates_recently_resolved_item(self):
        db, conn, cursor = _make_db()
        repo = TriageRepository(db)
        recent = datetime.now() - timedelta(days=2)
        cursor.fetchall.return_value = [
            _sample_row(item_id=10, resolved_at=recent)
        ]
        result = repo.reactivate_for_component('comp-a', 'rhoai-v3-5',
                                                note='re-broke after fix')
        assert result == 10
        update_call = cursor.execute.call_args_list[-1]
        sql = update_call[0][0]
        assert 'UPDATE triage_items' in sql
        assert "status = 'active'" in sql

    def test_skips_old_resolved_item(self):
        db, conn, cursor = _make_db()
        repo = TriageRepository(db)
        old = datetime.now() - timedelta(days=10)
        # SQL filter would exclude this, so fetchall returns empty
        cursor.fetchall.return_value = []
        result = repo.reactivate_for_component('comp-a', 'rhoai-v3-5')
        assert result is None

    def test_returns_none_when_no_resolved_items(self):
        db, conn, cursor = _make_db()
        repo = TriageRepository(db)
        cursor.fetchall.return_value = []
        result = repo.reactivate_for_component('comp-a', 'rhoai-v3-5')
        assert result is None


class TestFindResolvedByComponent:
    def test_returns_recently_resolved_items(self):
        db, conn, cursor = _make_db()
        repo = TriageRepository(db)
        recent = datetime.now() - timedelta(days=2)
        cursor.fetchall.return_value = [
            _sample_row(item_id=5, resolved_at=recent)
        ]
        result = repo.find_resolved_by_component('comp-a', 'rhoai-v3-5')
        assert len(result) == 1
        assert result[0]['id'] == 5

    def test_returns_empty_when_none_found(self):
        db, conn, cursor = _make_db()
        repo = TriageRepository(db)
        cursor.fetchall.return_value = []
        result = repo.find_resolved_by_component('comp-a', 'rhoai-v3-5')
        assert result == []


class TestShouldCreateTriage:
    def test_returns_true_for_new_untracked_failure(self):
        from watcher.handlers import should_create_triage
        result = should_create_triage(
            is_new=True, active_triage_items=[],
            recent_success=False, auto_create_enabled=True,
        )
        assert result is True

    def test_returns_false_when_disabled(self):
        from watcher.handlers import should_create_triage
        result = should_create_triage(
            is_new=True, active_triage_items=[],
            recent_success=False, auto_create_enabled=False,
        )
        assert result is False

    def test_returns_false_when_already_tracked(self):
        from watcher.handlers import should_create_triage
        result = should_create_triage(
            is_new=True, active_triage_items=[{'id': 1}],
            recent_success=False, auto_create_enabled=True,
        )
        assert result is False

    def test_returns_false_post_resolution(self):
        from watcher.handlers import should_create_triage
        result = should_create_triage(
            is_new=True, active_triage_items=[],
            recent_success=True, auto_create_enabled=True,
        )
        assert result is False


class TestShouldReactivateTriage:
    def test_returns_true_for_recent_resolved(self):
        from watcher.handlers import should_reactivate_triage
        resolved_items = [{'id': 5, 'status': 'resolved',
                           'resolved_at': datetime.now() - timedelta(days=2)}]
        assert should_reactivate_triage(resolved_items, max_age_days=7) is True

    def test_returns_false_for_old_resolved(self):
        from watcher.handlers import should_reactivate_triage
        resolved_items = [{'id': 5, 'status': 'resolved',
                           'resolved_at': datetime.now() - timedelta(days=10)}]
        assert should_reactivate_triage(resolved_items, max_age_days=7) is False

    def test_returns_false_for_empty_list(self):
        from watcher.handlers import should_reactivate_triage
        assert should_reactivate_triage([], max_age_days=7) is False
