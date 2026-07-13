"""Verify shared exception-coverage enrichment function."""
from unittest.mock import patch, MagicMock


@patch("conforma.exception_enrichment.policy_env", return_value="stage")
@patch("conforma.exception_enrichment.compute_blocks", return_value="none")
@patch("conforma.exception_enrichment.compute_exception_coverage_details")
@patch("conforma.exception_enrichment.extract_policy_from_scenario", return_value="rhoai-policy")
@patch("conforma.exception_enrichment.extract_violation_rules", return_value=["rule1"])
def test_enrich_returns_expected_keys(mock_rules, mock_policy, mock_cov, mock_blocks, mock_env):
    from conforma.exception_enrichment import enrich_with_exception_coverage

    mock_cov.return_value = {
        'coverage': 'full', 'stage': 'full', 'prod': 'none',
        'env_tag': 'stage', 'policy_url_stage': 'http://s',
        'policy_url_prod': 'http://p',
        'uncovered_rules_stage': [], 'uncovered_rules_prod': ['rule1'],
    }

    result = enrich_with_exception_coverage(
        "some violation summary", "scenario-name", {})

    assert result['coverage'] == 'full'
    assert result['blocks'] == 'none'
    assert result['policy_env'] == 'stage'
    assert 'policy_url' in result
    mock_rules.assert_called_once_with("some violation summary")
    mock_cov.assert_called_once_with(["rule1"], "scenario-name", {})
