"""Unit tests for log filtering utility."""

import unittest

from utils.log_filter import filter_error_lines


class TestFilterErrorLines(unittest.TestCase):

    def test_empty_logs(self):
        self.assertEqual(filter_error_lines(''), '')
        self.assertEqual(filter_error_lines(None), None)

    def test_short_logs_returned_unchanged(self):
        logs = 'line 1\nline 2\nline 3\n'
        self.assertEqual(filter_error_lines(logs), logs)

    def test_under_100_lines_returned_unchanged(self):
        logs = '\n'.join('line {}'.format(i) for i in range(99))
        self.assertEqual(filter_error_lines(logs), logs)

    def test_filters_large_logs(self):
        lines = ['ok line {}'.format(i) for i in range(500)]
        lines[250] = 'ERROR: something failed'
        logs = '\n'.join(lines)
        filtered = filter_error_lines(logs, context_lines=5)
        self.assertIn('ERROR: something failed', filtered)
        self.assertIn('... (', filtered)
        self.assertLess(len(filtered), len(logs))

    def test_keeps_first_and_last_lines(self):
        lines = ['start line {}'.format(i) for i in range(10)]
        lines.extend(['noise line {}'.format(i) for i in range(300)])
        lines.extend(['end line {}'.format(i) for i in range(30)])
        logs = '\n'.join(lines)
        filtered = filter_error_lines(logs, context_lines=2)
        self.assertIn('start line 0', filtered)
        self.assertIn('end line 29', filtered)

    def test_mostly_errors_returns_unchanged(self):
        lines = ['error: problem {}'.format(i) for i in range(200)]
        logs = '\n'.join(lines)
        self.assertEqual(filter_error_lines(logs), logs)

    def test_gap_markers_show_skipped_count(self):
        lines = ['ok'] * 200
        lines[100] = 'fatal error here'
        logs = '\n'.join(lines)
        filtered = filter_error_lines(logs, context_lines=2)
        self.assertIn('lines filtered', filtered)


if __name__ == '__main__':
    unittest.main()
