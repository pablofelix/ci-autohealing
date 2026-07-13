"""Verify enrichment coverage counts only unresolved failures."""
from unittest.mock import MagicMock


def test_enrichment_excludes_resolved():
    """Enriched count must not exceed total (both filter is_resolved=FALSE)."""
    from repositories.build_failure_repository import BuildFailureRepository

    mock_db = MagicMock()
    repo = BuildFailureRepository(mock_db)

    mock_cursor = MagicMock()
    mock_db.connection.return_value.__enter__.return_value.cursor.return_value = mock_cursor
    mock_cursor.fetchone.return_value = (3, 2, 0)

    result = repo.get_enrichment_coverage("rhoai-v3-5")

    sql = mock_cursor.execute.call_args[0][0]
    filter_clauses = sql.split("FILTER")
    for clause in filter_clauses[1:]:
        assert "is_resolved = FALSE" in clause, \
            f"Missing is_resolved filter in FILTER clause: {clause.strip()[:80]}"

    assert result['enriched'] <= result['total']
