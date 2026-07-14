"""Tests for failure_nature repository methods."""

import unittest
from unittest.mock import MagicMock


class TestUpdateFailureNature(unittest.TestCase):

    def setUp(self):
        self.db = MagicMock()
        self.conn = MagicMock()
        self.cursor = MagicMock()
        self.db.connection.return_value.__enter__ = lambda s: self.conn
        self.db.connection.return_value.__exit__ = MagicMock(return_value=False)
        self.conn.cursor.return_value = self.cursor

        from repositories.build_failure_repository import BuildFailureRepository
        self.repo = BuildFailureRepository.__new__(BuildFailureRepository)
        self.repo.db = self.db

    def test_update_failure_nature_sets_column(self):
        self.cursor.rowcount = 1
        result = self.repo.update_failure_nature(42, 'structural')
        self.assertTrue(result)
        sql = self.cursor.execute.call_args[0][0]
        self.assertIn('failure_nature', sql)
        self.assertIn('WHERE id = %s', sql)
        params = self.cursor.execute.call_args[0][1]
        self.assertEqual(params, ('structural', 42))

    def test_update_failure_nature_returns_false_on_no_match(self):
        self.cursor.rowcount = 0
        result = self.repo.update_failure_nature(999, 'unknown')
        self.assertFalse(result)

    def test_update_failure_nature_returns_false_on_exception(self):
        self.cursor.execute.side_effect = Exception('db error')
        result = self.repo.update_failure_nature(1, 'structural')
        self.assertFalse(result)


class TestGetUnresolvedFailure(unittest.TestCase):

    def setUp(self):
        self.db = MagicMock()
        self.conn = MagicMock()
        self.cursor = MagicMock()
        self.db.connection.return_value.__enter__ = lambda s: self.conn
        self.db.connection.return_value.__exit__ = MagicMock(return_value=False)
        self.conn.cursor.return_value = self.cursor

        from repositories.build_failure_repository import BuildFailureRepository
        self.repo = BuildFailureRepository.__new__(BuildFailureRepository)
        self.repo.db = self.db

    def test_returns_failure_with_nature_and_step(self):
        self.cursor.fetchone.return_value = {
            'id': 10,
            'failure_nature': 'structural',
            'failed_step_name': 'fips-check',
        }
        result = self.repo.get_unresolved_failure('comp-a', 'app-1')
        self.assertEqual(result['failure_nature'], 'structural')
        self.assertEqual(result['failed_step_name'], 'fips-check')

    def test_returns_none_when_no_unresolved(self):
        self.cursor.fetchone.return_value = None
        result = self.repo.get_unresolved_failure('comp-a', 'app-1')
        self.assertIsNone(result)

    def test_returns_none_on_exception(self):
        self.cursor.execute.side_effect = Exception('db error')
        result = self.repo.get_unresolved_failure('comp-a', 'app-1')
        self.assertIsNone(result)


if __name__ == '__main__':
    unittest.main()
