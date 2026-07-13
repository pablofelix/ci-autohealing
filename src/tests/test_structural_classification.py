"""Tests for classify_failure_nature() pure function."""

import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

sys.modules.setdefault('logger', MagicMock(setup_logger=MagicMock(return_value=MagicMock())))
sys.modules.setdefault('repositories.connection', MagicMock())
sys.modules.setdefault('psycopg2', MagicMock())
sys.modules.setdefault('psycopg2.pool', MagicMock())

from analyzers.build_failure_analyzer import classify_failure_nature


class TestClassifyFailureNature:

    def test_never_succeeded_is_structural(self):
        health = {'has_ever_succeeded': False, 'total_builds': 5}
        assert classify_failure_nature(health) == 'structural'

    def test_has_succeeded_is_unknown(self):
        health = {'has_ever_succeeded': True, 'total_builds': 10}
        assert classify_failure_nature(health) == 'unknown'

    def test_none_input_is_unknown(self):
        assert classify_failure_nature(None) == 'unknown'

    def test_empty_dict_is_unknown(self):
        assert classify_failure_nature({}) == 'unknown'

    def test_missing_key_defaults_to_unknown(self):
        health = {'total_builds': 3}
        assert classify_failure_nature(health) == 'unknown'

    def test_zero_builds_never_succeeded_is_structural(self):
        health = {'has_ever_succeeded': False, 'total_builds': 0}
        assert classify_failure_nature(health) == 'structural'
