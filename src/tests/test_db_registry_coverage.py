"""Comprehensive tests for skills.db_registry module."""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from unittest.mock import MagicMock, patch

import pytest

from skills.db_registry import DatabaseSkillRegistry, get_registry
from skills.models import ExecutionResult, SkillEntry, SkillMetadata, SourceEntry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db():
    db = MagicMock()
    conn = MagicMock()
    cursor = MagicMock()
    db.connection.return_value.__enter__ = MagicMock(return_value=conn)
    db.connection.return_value.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value = cursor
    return db, conn, cursor


def _meta(name='myskill', description='desc', category=''):
    return SkillMetadata(name=name, description=description, category=category)


# ---------------------------------------------------------------------------
# save
# ---------------------------------------------------------------------------

class TestSave:
    def test_save_is_noop(self):
        db, _, _ = _make_db()
        reg = DatabaseSkillRegistry(db)
        result = reg.save()
        assert result is None


# ---------------------------------------------------------------------------
# add_source
# ---------------------------------------------------------------------------

class TestAddSource:
    def test_add_source_happy_path(self):
        db, conn, cursor = _make_db()
        reg = DatabaseSkillRegistry(db)
        entry = reg.add_source('local', 'https://example.com', 'abc123', '/tmp/src')
        assert isinstance(entry, SourceEntry)
        assert entry.name == 'local'
        assert entry.url == 'https://example.com'
        assert entry.commit == 'abc123'
        assert entry.local_path == '/tmp/src'
        assert entry.branch is None
        cursor.execute.assert_called_once()
        conn.commit.assert_called_once()

    def test_add_source_with_branch(self):
        db, conn, cursor = _make_db()
        reg = DatabaseSkillRegistry(db)
        entry = reg.add_source('remote', 'https://git.co', 'def456', '/opt/r', branch='main')
        assert entry.branch == 'main'
        assert entry.name == 'remote'
        # Verify branch was passed in the execute params
        call_args = cursor.execute.call_args
        assert 'main' in call_args[0][1]


# ---------------------------------------------------------------------------
# remove_source
# ---------------------------------------------------------------------------

class TestRemoveSource:
    def test_remove_source_returns_count(self):
        db, conn, cursor = _make_db()
        cursor.fetchone.return_value = (3,)
        reg = DatabaseSkillRegistry(db)
        count = reg.remove_source('my-source')
        assert count == 3
        assert cursor.execute.call_count == 2  # SELECT COUNT + DELETE
        conn.commit.assert_called_once()


# ---------------------------------------------------------------------------
# add_skill
# ---------------------------------------------------------------------------

class TestAddSkill:
    def test_add_skill_with_tags(self):
        db, conn, cursor = _make_db()
        reg = DatabaseSkillRegistry(db)
        meta = _meta()
        entry = reg.add_skill('check', 'local', '/path/check', meta, initial_tags=['ci', 'lint'])
        assert isinstance(entry, SkillEntry)
        assert entry.name == 'check'
        assert entry.source == 'local'
        assert entry.status == 'active'
        # source auto-prepended, initial tags preserved
        assert 'local' in entry.tags
        assert 'ci' in entry.tags
        assert 'lint' in entry.tags
        conn.commit.assert_called_once()

    def test_add_skill_without_tags(self):
        db, conn, cursor = _make_db()
        reg = DatabaseSkillRegistry(db)
        meta = _meta()
        entry = reg.add_skill('deploy', 'remote', '/path/deploy', meta)
        # source auto-tagged even with no initial_tags
        assert 'remote' in entry.tags

    def test_add_skill_source_auto_tagged(self):
        db, conn, cursor = _make_db()
        reg = DatabaseSkillRegistry(db)
        meta = _meta()
        entry = reg.add_skill('run', 'ops', '/p', meta, initial_tags=['ops'])
        # source already in initial_tags, should not duplicate
        assert entry.tags.count('ops') == 1

    def test_add_skill_category_auto_tagged(self):
        db, conn, cursor = _make_db()
        reg = DatabaseSkillRegistry(db)
        meta = _meta(category='triage')
        entry = reg.add_skill('fix', 'local', '/p', meta, initial_tags=['ci'])
        assert 'triage' in entry.tags
        # category appended at end
        assert entry.tags[-1] == 'triage'

    def test_add_skill_category_not_duplicated(self):
        db, conn, cursor = _make_db()
        reg = DatabaseSkillRegistry(db)
        meta = _meta(category='ci')
        entry = reg.add_skill('fix', 'local', '/p', meta, initial_tags=['ci'])
        assert entry.tags.count('ci') == 1


# ---------------------------------------------------------------------------
# remove_skill
# ---------------------------------------------------------------------------

class TestRemoveSkill:
    def test_remove_skill_found(self):
        db, conn, cursor = _make_db()
        cursor.rowcount = 1
        reg = DatabaseSkillRegistry(db)
        assert reg.remove_skill('local/check') is True

    def test_remove_skill_not_found(self):
        db, conn, cursor = _make_db()
        cursor.rowcount = 0
        reg = DatabaseSkillRegistry(db)
        assert reg.remove_skill('local/nonexistent') is False


# ---------------------------------------------------------------------------
# get_skill
# ---------------------------------------------------------------------------

class TestGetSkill:
    def _skill_row(self, qname='local/check', name='check', source='local'):
        return (qname, name, source, '/path', 'active', ['ci'],
                {'name': name, 'description': 'desc'})

    def test_get_skill_by_qualified_name(self):
        db, conn, cursor = _make_db()
        cursor.fetchone.return_value = self._skill_row()
        reg = DatabaseSkillRegistry(db)
        skill = reg.get_skill('local/check')
        assert skill is not None
        assert skill.name == 'check'
        assert skill.source == 'local'

    def test_get_skill_by_name_single_match(self):
        db, conn, cursor = _make_db()
        # First query (qualified_name) returns None
        cursor.fetchone.return_value = None
        # Second query (name) returns one row
        cursor.fetchall.return_value = [self._skill_row()]
        reg = DatabaseSkillRegistry(db)
        skill = reg.get_skill('check')
        assert skill is not None
        assert skill.name == 'check'

    def test_get_skill_ambiguous_raises_key_error(self):
        db, conn, cursor = _make_db()
        cursor.fetchone.return_value = None
        cursor.fetchall.return_value = [
            self._skill_row('local/check', 'check', 'local'),
            self._skill_row('remote/check', 'check', 'remote'),
        ]
        reg = DatabaseSkillRegistry(db)
        with pytest.raises(KeyError, match='Ambiguous'):
            reg.get_skill('check')

    def test_get_skill_not_found(self):
        db, conn, cursor = _make_db()
        cursor.fetchone.return_value = None
        cursor.fetchall.return_value = []
        reg = DatabaseSkillRegistry(db)
        result = reg.get_skill('nonexistent')
        assert result is None


# ---------------------------------------------------------------------------
# list_skills
# ---------------------------------------------------------------------------

class TestListSkills:
    def _rows(self):
        return [
            ('local/a', 'a', 'local', '/p/a', 'active', ['ci'], {'name': 'a', 'description': 'A'}),
            ('local/b', 'b', 'local', '/p/b', 'active', ['ci'], {'name': 'b', 'description': 'B'}),
        ]

    def test_list_skills_no_filters(self):
        db, conn, cursor = _make_db()
        cursor.fetchall.return_value = self._rows()
        reg = DatabaseSkillRegistry(db)
        skills = reg.list_skills()
        assert len(skills) == 2
        assert all(isinstance(s, SkillEntry) for s in skills)

    def test_list_skills_filter_by_tag(self):
        db, conn, cursor = _make_db()
        cursor.fetchall.return_value = self._rows()
        reg = DatabaseSkillRegistry(db)
        skills = reg.list_skills(tag='ci')
        assert len(skills) == 2
        call_sql = cursor.execute.call_args[0][0]
        assert 'tags @>' in call_sql

    def test_list_skills_filter_by_source(self):
        db, conn, cursor = _make_db()
        cursor.fetchall.return_value = self._rows()
        reg = DatabaseSkillRegistry(db)
        reg.list_skills(source='local')
        call_sql = cursor.execute.call_args[0][0]
        assert 'source = %s' in call_sql

    def test_list_skills_filter_by_status(self):
        db, conn, cursor = _make_db()
        cursor.fetchall.return_value = []
        reg = DatabaseSkillRegistry(db)
        reg.list_skills(status='disabled')
        call_sql = cursor.execute.call_args[0][0]
        assert 'status = %s' in call_sql

    def test_list_skills_all_filters_combined(self):
        db, conn, cursor = _make_db()
        cursor.fetchall.return_value = []
        reg = DatabaseSkillRegistry(db)
        reg.list_skills(tag='ci', source='local', status='active')
        call_sql = cursor.execute.call_args[0][0]
        assert 'tags @>' in call_sql
        assert 'source = %s' in call_sql
        assert 'status = %s' in call_sql
        params = cursor.execute.call_args[0][1]
        assert params == [['ci'], 'local', 'active']


# ---------------------------------------------------------------------------
# add_tag
# ---------------------------------------------------------------------------

class TestAddTag:
    def test_add_tag_skill_found(self):
        db, conn, cursor = _make_db()
        # get_skill lookup: first fetchone returns a row
        cursor.fetchone.return_value = (
            'local/check', 'check', 'local', '/p', 'active', ['ci'],
            {'name': 'check', 'description': 'desc'},
        )
        reg = DatabaseSkillRegistry(db)
        result = reg.add_tag('local/check', 'release')
        assert result is True
        conn.commit.assert_called()

    def test_add_tag_skill_not_found(self):
        db, conn, cursor = _make_db()
        cursor.fetchone.return_value = None
        cursor.fetchall.return_value = []
        reg = DatabaseSkillRegistry(db)
        result = reg.add_tag('nonexistent', 'tag')
        assert result is False


# ---------------------------------------------------------------------------
# remove_tag
# ---------------------------------------------------------------------------

class TestRemoveTag:
    def test_remove_tag_skill_found_rowcount_positive(self):
        db, conn, cursor = _make_db()
        # get_skill returns a row on first fetchone
        cursor.fetchone.return_value = (
            'local/check', 'check', 'local', '/p', 'active', ['ci'],
            {'name': 'check', 'description': 'desc'},
        )
        cursor.rowcount = 1
        reg = DatabaseSkillRegistry(db)
        result = reg.remove_tag('local/check', 'ci')
        assert result is True

    def test_remove_tag_skill_not_found(self):
        db, conn, cursor = _make_db()
        cursor.fetchone.return_value = None
        cursor.fetchall.return_value = []
        reg = DatabaseSkillRegistry(db)
        result = reg.remove_tag('nonexistent', 'ci')
        assert result is False


# ---------------------------------------------------------------------------
# list_tags
# ---------------------------------------------------------------------------

class TestListTags:
    def test_list_tags_returns_dict(self):
        db, conn, cursor = _make_db()
        cursor.fetchall.return_value = [('ci', 5), ('release', 3), ('triage', 1)]
        reg = DatabaseSkillRegistry(db)
        tags = reg.list_tags()
        assert tags == {'ci': 5, 'release': 3, 'triage': 1}

    def test_list_tags_empty(self):
        db, conn, cursor = _make_db()
        cursor.fetchall.return_value = []
        reg = DatabaseSkillRegistry(db)
        assert reg.list_tags() == {}


# ---------------------------------------------------------------------------
# list_sources
# ---------------------------------------------------------------------------

class TestListSources:
    def test_list_sources_returns_source_entries(self):
        db, conn, cursor = _make_db()
        cursor.fetchall.return_value = [
            ('local', 'https://example.com', 'abc', '/tmp', '2025-01-01', 'main'),
            ('remote', 'https://git.co', None, None, '2025-06-01', None),
        ]
        reg = DatabaseSkillRegistry(db)
        sources = reg.list_sources()
        assert len(sources) == 2
        assert all(isinstance(s, SourceEntry) for s in sources)
        assert sources[0].name == 'local'
        assert sources[0].branch == 'main'
        # None commit coerced to ''
        assert sources[1].commit == ''
        assert sources[1].local_path == ''
        assert sources[1].branch is None


# ---------------------------------------------------------------------------
# update_source_commit
# ---------------------------------------------------------------------------

class TestUpdateSourceCommit:
    def test_update_source_commit_executes(self):
        db, conn, cursor = _make_db()
        reg = DatabaseSkillRegistry(db)
        reg.update_source_commit('local', 'newsha')
        cursor.execute.assert_called_once()
        call_params = cursor.execute.call_args[0][1]
        assert call_params == ('newsha', 'local')
        conn.commit.assert_called_once()


# ---------------------------------------------------------------------------
# sources property
# ---------------------------------------------------------------------------

class TestSourcesProperty:
    def test_sources_returns_dict_keyed_by_name(self):
        db, conn, cursor = _make_db()
        cursor.fetchall.return_value = [
            ('local', 'https://example.com', 'abc', '/tmp', '2025-01-01', None),
        ]
        reg = DatabaseSkillRegistry(db)
        sources = reg.sources
        assert isinstance(sources, dict)
        assert 'local' in sources
        assert sources['local'].url == 'https://example.com'


# ---------------------------------------------------------------------------
# skills property
# ---------------------------------------------------------------------------

class TestSkillsProperty:
    def test_skills_returns_dict_keyed_by_qualified_name(self):
        db, conn, cursor = _make_db()
        cursor.fetchall.return_value = [
            ('local/check', 'check', 'local', '/p', 'active', ['ci'],
             {'name': 'check', 'description': 'desc'}),
        ]
        reg = DatabaseSkillRegistry(db)
        skills = reg.skills
        assert isinstance(skills, dict)
        assert 'local/check' in skills
        assert skills['local/check'].name == 'check'


# ---------------------------------------------------------------------------
# _row_to_entry (static method)
# ---------------------------------------------------------------------------

class TestRowToEntry:
    def test_row_with_dict_metadata(self):
        row = ('src/myskill', 'myskill', 'local', '/path', 'active',
               ['tag1'], {'name': 'myskill', 'description': 'desc'})
        entry = DatabaseSkillRegistry._row_to_entry(row)
        assert isinstance(entry, SkillEntry)
        assert entry.name == 'myskill'
        assert entry.source == 'local'
        assert entry.path == '/path'
        assert entry.status == 'active'
        assert entry.tags == ['tag1']
        assert entry.metadata.name == 'myskill'
        assert entry.metadata.description == 'desc'

    def test_row_with_json_string_metadata(self):
        meta_json = json.dumps({'name': 'tool', 'description': 'A tool'})
        row = ('src/tool', 'tool', 'remote', '/opt', 'active', ['ci'], meta_json)
        entry = DatabaseSkillRegistry._row_to_entry(row)
        assert entry.metadata.name == 'tool'
        assert entry.metadata.description == 'A tool'

    def test_row_with_empty_metadata_raises(self):
        row = ('src/empty', 'empty', 'local', '/p', 'active', ['t'], None)
        # None metadata falls back to empty dict via json.loads('{}'),
        # but SkillMetadata.from_dict requires 'name' key — raises KeyError
        with pytest.raises(KeyError):
            DatabaseSkillRegistry._row_to_entry(row)

    def test_row_with_none_path_and_status(self):
        row = ('src/x', 'x', 'local', None, None, None,
               {'name': 'x', 'description': 'd'})
        entry = DatabaseSkillRegistry._row_to_entry(row)
        assert entry.path == ''
        assert entry.status == 'active'
        assert entry.tags == []

    def test_row_with_empty_string_metadata_raises(self):
        row = ('src/y', 'y', 'local', '/p', 'active', [], '')
        # empty string -> json.loads('{}') -> SkillMetadata.from_dict({}) -> KeyError
        with pytest.raises(KeyError):
            DatabaseSkillRegistry._row_to_entry(row)


# ---------------------------------------------------------------------------
# record_run
# ---------------------------------------------------------------------------

class TestRecordRun:
    def test_record_run_with_all_fields(self):
        db, conn, cursor = _make_db()
        reg = DatabaseSkillRegistry(db)
        result = ExecutionResult(
            skill_name='local/fix',
            status='success',
            exit_code=0,
            stdout='output data',
            stderr='warning msg',
            duration_seconds=12.5,
            risk_level='low',
            started_at='2025-06-01T00:00:00',
            triggered_by='api',
            component_name='odh-dashboard',
            application='rhoai',
            triage_item_id=42,
        )
        reg.record_run(result)
        cursor.execute.assert_called_once()
        call_params = cursor.execute.call_args[0][1]
        assert call_params[0] == 'local/fix'
        assert call_params[1] == 'success'
        assert call_params[2] == 0
        assert call_params[3] == 'low'
        assert call_params[4] == 12.5
        assert call_params[5] == 'output data'
        assert call_params[6] == 'warning msg'
        assert call_params[8] == 'api'
        assert call_params[10] == 'odh-dashboard'
        assert call_params[11] == 'rhoai'
        assert call_params[12] == 42

    def test_record_run_without_optional_fields(self):
        db, conn, cursor = _make_db()
        reg = DatabaseSkillRegistry(db)
        result = ExecutionResult(
            skill_name='local/check',
            status='failed',
            exit_code=1,
            duration_seconds=0.5,
            risk_level='medium',
            started_at='2025-06-01T00:00:00',
        )
        # stdout/stderr are empty strings -> truncation still works
        reg.record_run(result)
        cursor.execute.assert_called_once()
        call_params = cursor.execute.call_args[0][1]
        # Empty stdout -> None via the falsy check
        assert call_params[5] is None
        assert call_params[6] is None
        # triggered_by defaults to 'cli' via getattr
        assert call_params[8] == 'cli'
        # component_name, application, triage_item_id default to None
        assert call_params[10] is None
        assert call_params[11] is None
        assert call_params[12] is None

    def test_record_run_truncates_long_stdout(self):
        db, conn, cursor = _make_db()
        reg = DatabaseSkillRegistry(db)
        long_output = 'x' * 60000
        result = ExecutionResult(
            skill_name='local/big',
            status='success',
            exit_code=0,
            stdout=long_output,
            stderr='',
            duration_seconds=1.0,
            risk_level='low',
            started_at='2025-01-01T00:00:00',
        )
        reg.record_run(result)
        call_params = cursor.execute.call_args[0][1]
        assert len(call_params[5]) == 50000

    def test_record_run_truncates_long_stderr(self):
        db, conn, cursor = _make_db()
        reg = DatabaseSkillRegistry(db)
        long_err = 'e' * 20000
        result = ExecutionResult(
            skill_name='local/err',
            status='failed',
            exit_code=1,
            stdout='ok',
            stderr=long_err,
            duration_seconds=1.0,
            risk_level='high',
            started_at='2025-01-01T00:00:00',
        )
        reg.record_run(result)
        call_params = cursor.execute.call_args[0][1]
        assert len(call_params[6]) == 10000


# ---------------------------------------------------------------------------
# get_run_history
# ---------------------------------------------------------------------------

class TestGetRunHistory:
    def _setup_description(self, cursor):
        cursor.description = [
            ('id',), ('skill_name',), ('status',), ('exit_code',),
            ('risk_level',), ('duration_seconds',), ('triggered_by',),
            ('started_at',), ('completed_at',),
        ]

    def test_get_run_history_with_skill_name(self):
        db, conn, cursor = _make_db()
        self._setup_description(cursor)
        cursor.fetchall.return_value = [
            (1, 'local/fix', 'success', 0, 'low', 5.0, 'cli', '2025-06-01', '2025-06-01'),
        ]
        reg = DatabaseSkillRegistry(db)
        history = reg.get_run_history(skill_name='local/fix', limit=10)
        assert len(history) == 1
        assert history[0]['skill_name'] == 'local/fix'
        assert history[0]['status'] == 'success'
        # Verify the query used the skill_name filter
        call_args = cursor.execute.call_args[0]
        assert 'WHERE skill_name = %s' in call_args[0]
        assert call_args[1] == ('local/fix', 10)

    def test_get_run_history_without_skill_name(self):
        db, conn, cursor = _make_db()
        self._setup_description(cursor)
        cursor.fetchall.return_value = [
            (1, 'local/a', 'success', 0, 'low', 1.0, 'cli', '2025-06-01', '2025-06-01'),
            (2, 'local/b', 'failed', 1, 'high', 2.0, 'api', '2025-06-02', '2025-06-02'),
        ]
        reg = DatabaseSkillRegistry(db)
        history = reg.get_run_history()
        assert len(history) == 2
        call_args = cursor.execute.call_args[0]
        assert 'WHERE skill_name' not in call_args[0]
        assert call_args[1] == (20,)

    def test_get_run_history_empty(self):
        db, conn, cursor = _make_db()
        self._setup_description(cursor)
        cursor.fetchall.return_value = []
        reg = DatabaseSkillRegistry(db)
        history = reg.get_run_history(skill_name='nonexistent')
        assert history == []


# ---------------------------------------------------------------------------
# get_registry
# ---------------------------------------------------------------------------

class TestGetRegistry:
    @patch('skills.db_registry.DatabaseSkillRegistry')
    def test_get_registry_db_available_with_skills_table(self, mock_cls):
        mock_db = MagicMock()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_db.connection.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (1,)

        with patch.dict('sys.modules', {}):
            with patch('skills.db_registry.DatabaseSkillRegistry', mock_cls):
                mock_cls.return_value = MagicMock(spec=DatabaseSkillRegistry)
                with patch('builtins.__import__', side_effect=__builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__):
                    pass

        # Simpler approach: patch the imports inside get_registry
        mock_check_db = MagicMock(return_value=True)
        mock_get_db = MagicMock(return_value=mock_db)

        with patch.dict('sys.modules', {
            'cli.db': MagicMock(check_db=mock_check_db, _get_db_connection=mock_get_db),
        }):
            result = get_registry()
            assert isinstance(result, DatabaseSkillRegistry)

    def test_get_registry_db_available_without_skills_table(self):
        mock_db = MagicMock()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_db.connection.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = None  # skills table does not exist

        mock_check_db = MagicMock(return_value=True)
        mock_get_db = MagicMock(return_value=mock_db)
        mock_json_registry = MagicMock()

        with patch.dict('sys.modules', {
            'cli.db': MagicMock(check_db=mock_check_db, _get_db_connection=mock_get_db),
            'skills.registry': MagicMock(SkillRegistry=MagicMock(return_value=mock_json_registry)),
        }):
            result = get_registry()
            assert result is mock_json_registry

    def test_get_registry_db_unavailable_exception(self):
        mock_check_db = MagicMock(side_effect=Exception('connection refused'))
        mock_json_registry = MagicMock()

        with patch.dict('sys.modules', {
            'cli.db': MagicMock(check_db=mock_check_db),
            'skills.registry': MagicMock(SkillRegistry=MagicMock(return_value=mock_json_registry)),
        }):
            result = get_registry()
            assert result is mock_json_registry

    def test_get_registry_check_db_returns_false(self):
        mock_check_db = MagicMock(return_value=False)
        mock_json_registry = MagicMock()

        with patch.dict('sys.modules', {
            'cli.db': MagicMock(check_db=mock_check_db),
            'skills.registry': MagicMock(SkillRegistry=MagicMock(return_value=mock_json_registry)),
        }):
            result = get_registry()
            assert result is mock_json_registry
