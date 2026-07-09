"""Comprehensive tests for knowledge/graph_context.py — Neo4j knowledge graph queries."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from unittest.mock import MagicMock, patch

import pytest

# We need to mock conforma.policy_tools before importing graph_context
# since conforma_context() imports from it.
_mock_policy_tools = MagicMock()
sys.modules.setdefault('conforma', MagicMock())
sys.modules.setdefault('conforma.policy_tools', _mock_policy_tools)

import knowledge.graph_context as gc

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reset_driver():
    """Reset the module-level _driver singleton between tests."""
    gc._driver = None


@pytest.fixture(autouse=True)
def reset_driver_fixture():
    """Ensure each test starts with a clean driver state."""
    _reset_driver()
    yield
    _reset_driver()


def _mock_record(mapping):
    """Create a mock Neo4j record that supports dict() and .get()."""
    rec = MagicMock()
    rec.__iter__ = lambda self: iter(mapping.items())
    rec.keys = lambda: mapping.keys()
    rec.get = lambda key, default=None: mapping.get(key, default)
    rec.__getitem__ = lambda self, key: mapping[key]
    # dict(record) works via the items iterator
    rec.items = lambda: mapping.items()
    return rec


def _make_driver_with_records(records):
    """Build a mock Neo4j driver whose session.run() returns the given records.

    Each record is a dict that will be converted to a mock record object.
    """
    mock_driver = MagicMock()
    mock_session = MagicMock()

    # Make the session a context manager
    mock_driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)
    mock_driver.session.return_value.__exit__ = MagicMock(return_value=False)

    mock_result = MagicMock()
    mock_records = [_mock_record(r) for r in records]
    mock_result.__iter__ = lambda self: iter(mock_records)
    mock_result.single.return_value = mock_records[0] if mock_records else None
    mock_session.run.return_value = mock_result

    return mock_driver


# ===========================================================================
# _get_driver
# ===========================================================================

class TestGetDriver:
    """Tests for the _get_driver() singleton factory."""

    def test_returns_none_when_no_password(self):
        with patch.dict(os.environ, {'SLK_NEO4J_PASSWORD': ''}, clear=False):
            result = gc._get_driver()
        assert result is None

    def test_returns_none_when_password_missing(self):
        env = {k: v for k, v in os.environ.items()
               if k != 'SLK_NEO4J_PASSWORD'}
        with patch.dict(os.environ, env, clear=True):
            result = gc._get_driver()
        assert result is None

    def test_returns_driver_on_success(self):
        mock_neo4j_module = MagicMock()
        mock_driver = MagicMock()
        mock_neo4j_module.GraphDatabase.driver.return_value = mock_driver

        with patch.dict(os.environ, {
            'SLK_NEO4J_URI': 'bolt://test:7687',
            'SLK_NEO4J_USER': 'neo4j',
            'SLK_NEO4J_PASSWORD': 'secret',
        }):
            with patch.dict(sys.modules, {'neo4j': mock_neo4j_module}):
                result = gc._get_driver()

        assert result is mock_driver
        mock_driver.verify_connectivity.assert_called_once()

    def test_returns_none_on_connection_error(self):
        mock_neo4j_module = MagicMock()
        mock_neo4j_module.GraphDatabase.driver.side_effect = Exception("Connection refused")

        with patch.dict(os.environ, {
            'SLK_NEO4J_PASSWORD': 'secret',
        }):
            with patch.dict(sys.modules, {'neo4j': mock_neo4j_module}):
                result = gc._get_driver()

        assert result is None

    def test_caches_driver_on_second_call(self):
        mock_driver = MagicMock()
        gc._driver = mock_driver
        result = gc._get_driver()
        assert result is mock_driver

    def test_returns_none_when_verify_connectivity_fails(self):
        mock_neo4j_module = MagicMock()
        mock_driver = MagicMock()
        mock_driver.verify_connectivity.side_effect = Exception("timeout")
        mock_neo4j_module.GraphDatabase.driver.return_value = mock_driver

        with patch.dict(os.environ, {'SLK_NEO4J_PASSWORD': 'secret'}):
            with patch.dict(sys.modules, {'neo4j': mock_neo4j_module}):
                result = gc._get_driver()

        assert result is None


# ===========================================================================
# policy_rules_context
# ===========================================================================

class TestPolicyRulesContext:
    """Tests for policy_rules_context()."""

    def test_empty_rule_names(self):
        assert gc.policy_rules_context([]) == ""
        assert gc.policy_rules_context(None) == ""
        assert gc.policy_rules_context(set()) == ""

    def test_no_driver_returns_empty(self):
        with patch.object(gc, '_get_driver', return_value=None):
            assert gc.policy_rules_context(['some_rule']) == ""

    def test_no_records_returns_empty(self):
        driver = _make_driver_with_records([])
        # Override single to return None for empty
        driver.session.return_value.__enter__.return_value.run.return_value.__iter__ = \
            lambda self: iter([])
        with patch.object(gc, '_get_driver', return_value=driver):
            assert gc.policy_rules_context(['no_match']) == ""

    def test_formats_rules_with_title_and_description(self):
        records = [
            {
                'name': 'policy_hermetic_build',
                'title': 'Hermetic Build Required',
                'description': 'Build must be hermetic.',
                'typical_fix': 'Add hermetic: true to pipeline config',
            },
        ]
        driver = _make_driver_with_records(records)
        with patch.object(gc, '_get_driver', return_value=driver):
            result = gc.policy_rules_context(['policy_hermetic_build'])

        assert '## Known Policy Rules' in result
        assert 'policy_hermetic_build' in result
        assert 'Hermetic Build Required' in result
        assert 'Build must be hermetic.' in result
        assert 'Typical fix:' in result

    def test_formats_rule_without_optional_fields(self):
        records = [
            {
                'name': 'policy_fips_check',
                'title': 'FIPS Check',
                'description': None,
                'typical_fix': None,
            },
        ]
        driver = _make_driver_with_records(records)
        with patch.object(gc, '_get_driver', return_value=driver):
            result = gc.policy_rules_context(['policy_fips_check'])

        assert 'policy_fips_check' in result
        assert 'FIPS Check' in result
        assert 'Typical fix:' not in result

    def test_multiple_rules(self):
        records = [
            {'name': 'rule_a', 'title': 'A', 'description': 'desc_a', 'typical_fix': 'fix_a'},
            {'name': 'rule_b', 'title': 'B', 'description': 'desc_b', 'typical_fix': None},
        ]
        driver = _make_driver_with_records(records)
        with patch.object(gc, '_get_driver', return_value=driver):
            result = gc.policy_rules_context(['rule_a', 'rule_b'])

        assert 'rule_a' in result
        assert 'rule_b' in result

    def test_truncates_long_description(self):
        long_desc = 'x' * 500
        records = [
            {'name': 'rule', 'title': 'T', 'description': long_desc, 'typical_fix': None},
        ]
        driver = _make_driver_with_records(records)
        with patch.object(gc, '_get_driver', return_value=driver):
            result = gc.policy_rules_context(['rule'])

        # Description should be truncated to 200 chars
        assert 'x' * 200 in result
        assert 'x' * 201 not in result

    def test_truncates_long_typical_fix(self):
        long_fix = 'y' * 300
        records = [
            {'name': 'rule', 'title': 'T', 'description': None, 'typical_fix': long_fix},
        ]
        driver = _make_driver_with_records(records)
        with patch.object(gc, '_get_driver', return_value=driver):
            result = gc.policy_rules_context(['rule'])

        # typical_fix first line truncated to 150
        assert 'y' * 150 in result
        assert 'y' * 151 not in result

    def test_typical_fix_takes_first_line(self):
        records = [
            {'name': 'rule', 'title': 'T', 'description': None,
             'typical_fix': 'first line\nsecond line\nthird line'},
        ]
        driver = _make_driver_with_records(records)
        with patch.object(gc, '_get_driver', return_value=driver):
            result = gc.policy_rules_context(['rule'])

        assert 'first line' in result
        assert 'second line' not in result

    def test_exception_returns_empty(self):
        driver = MagicMock()
        driver.session.side_effect = Exception("DB error")
        with patch.object(gc, '_get_driver', return_value=driver):
            assert gc.policy_rules_context(['rule']) == ""


# ===========================================================================
# failure_pattern_context
# ===========================================================================

class TestFailurePatternContext:
    """Tests for failure_pattern_context()."""

    def test_empty_category(self):
        assert gc.failure_pattern_context('') == ""
        assert gc.failure_pattern_context(None) == ""

    def test_no_driver(self):
        with patch.object(gc, '_get_driver', return_value=None):
            assert gc.failure_pattern_context('some_cat') == ""

    def test_no_record(self):
        driver = _make_driver_with_records([])
        # single() returns None for empty results
        driver.session.return_value.__enter__.return_value.run.return_value.single.return_value = None
        with patch.object(gc, '_get_driver', return_value=driver):
            assert gc.failure_pattern_context('no_match') == ""

    def test_record_without_description(self):
        rec = _mock_record({'description': None, 'typical_fix': None})
        driver = _make_driver_with_records([{'description': None, 'typical_fix': None}])
        # Override single to return the record
        driver.session.return_value.__enter__.return_value.run.return_value.single.return_value = rec
        with patch.object(gc, '_get_driver', return_value=driver):
            assert gc.failure_pattern_context('empty') == ""

    def test_formats_pattern_with_fix(self):
        rec = _mock_record({
            'description': 'This failure happens when deps conflict.',
            'typical_fix': 'Line one\nLine two\nLine three\nLine four',
        })
        driver = MagicMock()
        mock_session = MagicMock()
        driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        driver.session.return_value.__exit__ = MagicMock(return_value=False)
        mock_session.run.return_value.single.return_value = rec

        with patch.object(gc, '_get_driver', return_value=driver):
            result = gc.failure_pattern_context('dependency_issue')

        assert '## Known Failure Pattern: dependency_issue' in result
        assert 'This failure happens when deps conflict.' in result
        assert 'Typical fix:' in result
        # Only first 3 lines of fix
        assert 'Line one' in result
        assert 'Line two' in result
        assert 'Line three' in result
        assert 'Line four' not in result

    def test_truncates_long_description_to_300(self):
        long_desc = 'z' * 500
        rec = _mock_record({'description': long_desc, 'typical_fix': None})
        driver = MagicMock()
        mock_session = MagicMock()
        driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        driver.session.return_value.__exit__ = MagicMock(return_value=False)
        mock_session.run.return_value.single.return_value = rec

        with patch.object(gc, '_get_driver', return_value=driver):
            result = gc.failure_pattern_context('long_cat')

        assert 'z' * 300 in result
        assert 'z' * 301 not in result

    def test_exception_returns_empty(self):
        driver = MagicMock()
        driver.session.side_effect = RuntimeError("boom")
        with patch.object(gc, '_get_driver', return_value=driver):
            assert gc.failure_pattern_context('cat') == ""


# ===========================================================================
# component_context
# ===========================================================================

class TestComponentContext:
    """Tests for component_context()."""

    def test_empty_name(self):
        assert gc.component_context('') == ""
        assert gc.component_context(None) == ""

    def test_no_driver(self):
        with patch.object(gc, '_get_driver', return_value=None):
            assert gc.component_context('comp') == ""

    def test_no_record(self):
        driver = _make_driver_with_records([])
        driver.session.return_value.__enter__.return_value.run.return_value.single.return_value = None
        with patch.object(gc, '_get_driver', return_value=driver):
            assert gc.component_context('unknown') == ""

    def test_returns_formatted_application(self):
        rec = _mock_record({'app': 'rhoai-v3-5'})
        driver = MagicMock()
        mock_session = MagicMock()
        driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        driver.session.return_value.__exit__ = MagicMock(return_value=False)
        mock_session.run.return_value.single.return_value = rec

        with patch.object(gc, '_get_driver', return_value=driver):
            result = gc.component_context('odh-dashboard-v3-5')

        assert 'Application (from knowledge graph): rhoai-v3-5' in result

    def test_exception_returns_empty(self):
        driver = MagicMock()
        driver.session.side_effect = Exception("neo4j down")
        with patch.object(gc, '_get_driver', return_value=driver):
            assert gc.component_context('comp') == ""


# ===========================================================================
# domain_concepts_context
# ===========================================================================

class TestDomainConceptsContext:
    """Tests for domain_concepts_context()."""

    def test_empty_names(self):
        assert gc.domain_concepts_context([]) == ""
        assert gc.domain_concepts_context(None) == ""

    def test_no_driver(self):
        with patch.object(gc, '_get_driver', return_value=None):
            assert gc.domain_concepts_context(['SBOM']) == ""

    def test_no_records(self):
        driver = _make_driver_with_records([])
        driver.session.return_value.__enter__.return_value.run.return_value.__iter__ = \
            lambda self: iter([])
        with patch.object(gc, '_get_driver', return_value=driver):
            assert gc.domain_concepts_context(['SBOM']) == ""

    def test_formats_concepts(self):
        records = [
            {'name': 'SBOM', 'definition': 'Software Bill of Materials — a manifest of components'},
            {'name': 'Hermetic Build', 'definition': 'A build isolated from network access'},
        ]
        driver = _make_driver_with_records(records)
        with patch.object(gc, '_get_driver', return_value=driver):
            result = gc.domain_concepts_context(['SBOM', 'Hermetic Build'])

        assert '## Domain Context' in result
        assert 'SBOM' in result
        assert 'Software Bill of Materials' in result
        assert 'Hermetic Build' in result

    def test_truncates_long_definition(self):
        records = [{'name': 'Concept', 'definition': 'a' * 400}]
        driver = _make_driver_with_records(records)
        with patch.object(gc, '_get_driver', return_value=driver):
            result = gc.domain_concepts_context(['Concept'])

        assert 'a' * 200 in result
        assert 'a' * 201 not in result

    def test_exception_returns_empty(self):
        driver = MagicMock()
        driver.session.side_effect = Exception("fail")
        with patch.object(gc, '_get_driver', return_value=driver):
            assert gc.domain_concepts_context(['X']) == ""


# ===========================================================================
# _map_rules_to_policy_keys
# ===========================================================================

class TestMapRulesToPolicyKeys:
    """Tests for the internal _map_rules_to_policy_keys()."""

    def test_empty_input(self):
        assert gc._map_rules_to_policy_keys([]) == []

    def test_direct_part_match(self):
        result = gc._map_rules_to_policy_keys(['hermetic_task.hermetic'])
        assert 'policy_hermetic_build' in result

    def test_labels_mapping(self):
        result = gc._map_rules_to_policy_keys(['labels.required_labels'])
        assert 'policy_cpe_label' in result

    def test_fips_mapping(self):
        result = gc._map_rules_to_policy_keys(['something.fips.check'])
        assert 'policy_fips_check' in result

    def test_keyword_fallback(self):
        result = gc._map_rules_to_policy_keys(['some_deprecated_task_check'])
        assert 'policy_deprecated_task' in result

    def test_no_match(self):
        result = gc._map_rules_to_policy_keys(['totally_unknown_rule'])
        assert result == []

    def test_deduplication(self):
        result = gc._map_rules_to_policy_keys([
            'hermetic_task.hermetic',
            'another.hermetic.check',
        ])
        assert result.count('policy_hermetic_build') == 1

    def test_multiple_mappings(self):
        result = gc._map_rules_to_policy_keys([
            'hermetic_task.hermetic',
            'labels.required_labels',
            'sbom_vendor_label.check',
        ])
        assert set(result) == {
            'policy_hermetic_build',
            'policy_cpe_label',
            'policy_sbom_vendor_label',
        }

    def test_source_image_mapping(self):
        result = gc._map_rules_to_policy_keys(['source_image.exists'])
        assert 'policy_source_image' in result

    def test_rpm_mapping(self):
        result = gc._map_rules_to_policy_keys(['rpm.repository_check'])
        assert 'policy_rpm_repository' in result

    def test_signing_mapping(self):
        result = gc._map_rules_to_policy_keys(['signing.key_check'])
        assert 'policy_signing_key' in result

    def test_unpinned_mapping(self):
        result = gc._map_rules_to_policy_keys(['unpinned.task_bundle'])
        assert 'policy_unpinned_task' in result

    def test_disallowed_packages(self):
        result = gc._map_rules_to_policy_keys(['disallowed_packages.check'])
        assert 'policy_package_source' in result

    def test_version_label(self):
        result = gc._map_rules_to_policy_keys(['version_label.check'])
        assert 'policy_version_label' in result


# ===========================================================================
# _concepts_for_build
# ===========================================================================

class TestConceptsForBuild:
    """Tests for _concepts_for_build()."""

    def test_no_matching_keywords(self):
        result = gc._concepts_for_build('generic_error', 'build-container')
        assert result == ""

    def test_hermetic_keyword_in_error(self):
        with patch.object(gc, 'domain_concepts_context', return_value='context') as mock_dc:
            result = gc._concepts_for_build('hermetic_build_error', '')
            mock_dc.assert_called_once()
            call_args = mock_dc.call_args[0][0]
            assert 'Hermetic Build' in call_args
            assert 'Prefetching' in call_args

    def test_prefetch_keyword_in_step(self):
        with patch.object(gc, 'domain_concepts_context', return_value='context') as mock_dc:
            result = gc._concepts_for_build('', 'prefetch-dependencies')
            mock_dc.assert_called_once()
            call_args = mock_dc.call_args[0][0]
            assert 'Prefetching' in call_args

    def test_sbom_keyword(self):
        with patch.object(gc, 'domain_concepts_context', return_value='ctx') as mock_dc:
            gc._concepts_for_build('sbom_generation', '')
            call_args = mock_dc.call_args[0][0]
            assert 'SBOM' in call_args

    def test_fips_keyword(self):
        with patch.object(gc, 'domain_concepts_context', return_value='ctx') as mock_dc:
            gc._concepts_for_build('fips_check_failed', '')
            call_args = mock_dc.call_args[0][0]
            assert 'Hermetic Build' in call_args

    def test_attestation_keyword(self):
        with patch.object(gc, 'domain_concepts_context', return_value='ctx') as mock_dc:
            gc._concepts_for_build('attestation_error', '')
            call_args = mock_dc.call_args[0][0]
            assert 'Attestation' in call_args
            assert 'Tekton Chains' in call_args

    def test_source_build_keyword(self):
        with patch.object(gc, 'domain_concepts_context', return_value='ctx') as mock_dc:
            gc._concepts_for_build('', 'source-build')
            call_args = mock_dc.call_args[0][0]
            assert 'SBOM' in call_args
            assert 'Provenance' in call_args


# ===========================================================================
# build_context
# ===========================================================================

class TestBuildContext:
    """Tests for build_context()."""

    def test_empty_failure(self):
        with patch.object(gc, 'component_context', return_value=''):
            with patch.object(gc, '_concepts_for_build', return_value=''):
                result = gc.build_context({})
        assert result == ""

    def test_only_component_context(self):
        with patch.object(gc, 'component_context', return_value='- App: rhoai'):
            with patch.object(gc, '_concepts_for_build', return_value=''):
                result = gc.build_context({'component_name': 'comp'})
        assert result == '- App: rhoai'

    def test_only_concepts(self):
        with patch.object(gc, 'component_context', return_value=''):
            with patch.object(gc, '_concepts_for_build', return_value='## Concepts\n- SBOM'):
                result = gc.build_context({
                    'component_name': 'comp',
                    'error_type': 'sbom_error',
                    'failed_step_name': 'build',
                })
        assert '## Concepts' in result

    def test_both_parts(self):
        with patch.object(gc, 'component_context', return_value='- App: rhoai'):
            with patch.object(gc, '_concepts_for_build', return_value='## Concepts'):
                result = gc.build_context({
                    'component_name': 'comp',
                    'error_type': 'hermetic',
                })
        assert '- App: rhoai' in result
        assert '## Concepts' in result

    def test_handles_none_error_type(self):
        with patch.object(gc, 'component_context', return_value=''):
            with patch.object(gc, '_concepts_for_build', return_value='') as mock_cb:
                gc.build_context({'component_name': 'c', 'error_type': None})
                mock_cb.assert_called_once_with('', '')

    def test_handles_none_failed_step(self):
        with patch.object(gc, 'component_context', return_value=''):
            with patch.object(gc, '_concepts_for_build', return_value='') as mock_cb:
                gc.build_context({
                    'component_name': 'c',
                    'error_type': 'err',
                    'failed_step_name': None,
                })
                mock_cb.assert_called_once_with('err', '')


# ===========================================================================
# conforma_context
# ===========================================================================

class TestConformaContext:
    """Tests for conforma_context()."""

    def test_no_rules_extracted(self):
        _mock_policy_tools.extract_violation_rules.return_value = set()
        result = gc.conforma_context({'violation_summary': ''})
        assert result == ""

    def test_with_rules(self):
        _mock_policy_tools.extract_violation_rules.return_value = {
            'hermetic_task.hermetic',
        }
        with patch.object(gc, 'policy_rules_context', return_value='## Rules') as mock_prc:
            result = gc.conforma_context({
                'violation_summary': '✕ [Violation] hermetic_task.hermetic',
            })
        assert result == '## Rules'
        mock_prc.assert_called_once()

    def test_passes_mapped_keys(self):
        _mock_policy_tools.extract_violation_rules.return_value = {
            'labels.required_labels',
        }
        with patch.object(gc, '_map_rules_to_policy_keys',
                          return_value=['policy_cpe_label']) as mock_map:
            with patch.object(gc, 'policy_rules_context', return_value='') as mock_prc:
                gc.conforma_context({
                    'violation_summary': '✕ [Violation] labels.required_labels',
                })
                mock_map.assert_called_once()
                mock_prc.assert_called_once_with(['policy_cpe_label'])

    def test_uses_empty_string_for_none_summary(self):
        _mock_policy_tools.extract_violation_rules.return_value = set()
        result = gc.conforma_context({'violation_summary': None})
        assert result == ""

    def test_missing_violation_summary_key(self):
        _mock_policy_tools.extract_violation_rules.return_value = set()
        result = gc.conforma_context({})
        assert result == ""


# ===========================================================================
# release_context
# ===========================================================================

class TestReleaseContext:
    """Tests for release_context()."""

    def test_empty_context(self):
        with patch.object(gc, 'domain_concepts_context', return_value=''):
            result = gc.release_context({})
        assert result == ""

    def test_no_violation_markers(self):
        with patch.object(gc, 'domain_concepts_context', return_value=''):
            result = gc.release_context({'logs': {'step1': 'all good'}})
        assert result == ""

    def test_finds_violations_in_logs(self):
        logs = {
            'step1': 'Some text [Violation] hermetic_task.hermetic more text',
            'step2': 'Some text [Violation] labels.required_labels more text',
        }
        with patch.object(gc, '_map_rules_to_policy_keys',
                          return_value=['policy_hermetic_build']) as mock_map:
            with patch.object(gc, 'policy_rules_context',
                              return_value='## Rules Section') as mock_prc:
                with patch.object(gc, 'domain_concepts_context',
                                  return_value='') as mock_dc:
                    result = gc.release_context({'logs': logs})

        assert '## Rules Section' in result
        mock_map.assert_called_once()

    def test_concepts_always_queried(self):
        with patch.object(gc, 'domain_concepts_context',
                          return_value='## Domain') as mock_dc:
            result = gc.release_context({'logs': {}})

        mock_dc.assert_called_once_with(['Conforma', 'ReleasePlan', 'FBC Fragment'])
        assert '## Domain' in result

    def test_both_rules_and_concepts(self):
        logs = {'s1': '[Violation] fips.check'}
        with patch.object(gc, '_map_rules_to_policy_keys',
                          return_value=['policy_fips_check']):
            with patch.object(gc, 'policy_rules_context',
                              return_value='## Rules'):
                with patch.object(gc, 'domain_concepts_context',
                                  return_value='## Concepts'):
                    result = gc.release_context({'logs': logs})

        assert '## Rules' in result
        assert '## Concepts' in result

    def test_non_string_log_skipped(self):
        logs = {'step1': ['not', 'a', 'string'], 'step2': 123}
        with patch.object(gc, 'domain_concepts_context', return_value=''):
            result = gc.release_context({'logs': logs})
        assert result == ""

    def test_violation_regex_captures_rule_name(self):
        logs = {
            'step1': '[Violation] sbom_vendor_label.check_ok\n[Violation] source_image.exists',
        }
        captured_rules = set()

        def fake_map(rules):
            captured_rules.update(rules)
            return []

        with patch.object(gc, '_map_rules_to_policy_keys', side_effect=fake_map):
            with patch.object(gc, 'policy_rules_context', return_value=''):
                with patch.object(gc, 'domain_concepts_context', return_value=''):
                    gc.release_context({'logs': logs})

        assert 'sbom_vendor_label' in captured_rules or any(
            'sbom_vendor_label' in r for r in captured_rules
        )
