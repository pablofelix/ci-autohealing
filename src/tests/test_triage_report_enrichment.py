"""Tests for enriched triage report data."""

import os
import sys
from unittest.mock import MagicMock
from datetime import datetime

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


def test_save_snapshot_inserts_row():
    db, conn, cursor = _make_db()
    repo = TriageRepository(db)
    repo.save_snapshot('rhoai-v3-5', failing=7, working=249, total=256)
    cursor.execute.assert_called_once()
    sql = cursor.execute.call_args[0][0]
    assert 'INSERT INTO triage_snapshots' in sql


def test_get_daily_snapshot_returns_none_when_no_data():
    db, conn, cursor = _make_db()
    cursor.fetchone.return_value = None
    repo = TriageRepository(db)
    result = repo.get_daily_snapshot('rhoai-v3-5', '2026-07-16')
    assert result is None


def test_get_daily_snapshot_returns_dict_when_data_exists():
    db, conn, cursor = _make_db()
    cursor.fetchone.return_value = (7, 249, 256, datetime(2026, 7, 16))
    repo = TriageRepository(db)
    result = repo.get_daily_snapshot('rhoai-v3-5', '2026-07-16')
    assert result['failing'] == 7
    assert result['working'] == 249
    assert result['total'] == 256
