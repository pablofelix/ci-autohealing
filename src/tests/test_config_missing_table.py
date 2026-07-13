"""Verify ConfigRepository.get_all() handles missing table gracefully."""
from unittest.mock import MagicMock

from psycopg2.errors import UndefinedTable


def test_get_all_returns_empty_on_missing_table():
    from repositories.config_repository import ConfigRepository

    mock_db = MagicMock()
    repo = ConfigRepository(mock_db)

    mock_cursor = MagicMock()
    mock_db.connection.return_value.__enter__.return_value.cursor.return_value = mock_cursor

    mock_cursor.execute.side_effect = UndefinedTable(
        'relation "runtime_config" does not exist')

    result = repo.get_all()
    assert result == []
