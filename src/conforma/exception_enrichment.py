"""Shared exception-coverage enrichment for conforma violations."""

from conforma.policy_tools import (
    compute_blocks,
    compute_exception_coverage_details,
    extract_policy_from_scenario,
    extract_violation_rules,
    policy_env,
    policy_url,
)


def enrich_with_exception_coverage(violation_summary, scenario, exceptions_by_policy):
    """Compute exception-coverage enrichment for a conforma violation.

    Args:
        violation_summary: Raw violation summary text containing rule codes
        scenario: ITS scenario name (e.g., 'verify-conforma-rhoai-v3-5')
        exceptions_by_policy: Dict mapping policy names to their exceptions

    Returns:
        Dict with keys: coverage, stage, prod, env_tag, policy_url,
        policy_url_stage, policy_url_prod, uncovered_rules_stage,
        uncovered_rules_prod, blocks, policy_env
    """
    rules = extract_violation_rules(violation_summary or '')
    cov = compute_exception_coverage_details(rules, scenario, exceptions_by_policy)
    pname = extract_policy_from_scenario(scenario)
    return {
        'coverage': cov['coverage'],
        'stage': cov['stage'],
        'prod': cov['prod'],
        'env_tag': cov['env_tag'],
        'policy_url': policy_url(scenario),
        'policy_url_stage': cov['policy_url_stage'],
        'policy_url_prod': cov['policy_url_prod'],
        'uncovered_rules_stage': cov['uncovered_rules_stage'],
        'uncovered_rules_prod': cov['uncovered_rules_prod'],
        'blocks': compute_blocks(scenario, cov['stage'], cov['prod']),
        'policy_env': policy_env(pname),
    }
