"""Query Neo4j for targeted context to enrich AI analysis prompts.

Returns short, formatted strings — never dumps the whole graph.
Fails silently if Neo4j is unavailable (returns empty strings).
"""

import os
import logging

logger = logging.getLogger(__name__)

_driver = None


def _get_driver():
    global _driver
    if _driver is not None:
        return _driver
    try:
        from neo4j import GraphDatabase
        uri = os.environ.get("SLK_NEO4J_URI", "bolt://localhost:7687")
        user = os.environ.get("SLK_NEO4J_USER", "neo4j")
        password = os.environ.get("SLK_NEO4J_PASSWORD", "")
        if not password:
            return None
        _driver = GraphDatabase.driver(uri, auth=(user, password))
        _driver.verify_connectivity()
        return _driver
    except Exception:
        _driver = None
        return None


def policy_rules_context(rule_names):
    """Look up PolicyRule nodes by name. Returns formatted prompt section."""
    if not rule_names:
        return ""
    driver = _get_driver()
    if not driver:
        return ""
    try:
        with driver.session() as s:
            result = s.run(
                "MATCH (p:PolicyRule) WHERE p.name IN $names "
                "RETURN p.name AS name, p.title AS title, "
                "p.description AS description, p.typical_fix AS typical_fix",
                names=list(rule_names),
            )
            records = [dict(r) for r in result]
        if not records:
            return ""
        lines = ["\n## Known Policy Rules (from knowledge graph)"]
        for rec in records:
            lines.append("- {}: {}".format(rec['name'], rec.get('title', '')))
            if rec.get('description'):
                lines.append("  {}".format(rec['description'][:200]))
            if rec.get('typical_fix'):
                fix = rec['typical_fix'].split('\n')[0][:150]
                lines.append("  Typical fix: {}".format(fix))
        return '\n'.join(lines)
    except Exception:
        return ""


def failure_pattern_context(category):
    """Look up a FailurePattern by category. Returns formatted prompt section."""
    if not category:
        return ""
    driver = _get_driver()
    if not driver:
        return ""
    try:
        with driver.session() as s:
            result = s.run(
                "MATCH (f:FailurePattern {category: $cat}) "
                "RETURN f.description AS description, f.typical_fix AS typical_fix",
                cat=category,
            )
            rec = result.single()
        if not rec or not rec.get('description'):
            return ""
        lines = ["\n## Known Failure Pattern: {} (from knowledge graph)".format(category)]
        lines.append(rec['description'][:300])
        if rec.get('typical_fix'):
            fix_lines = rec['typical_fix'].split('\n')[:3]
            lines.append("Typical fix: {}".format(' '.join(line.strip() for line in fix_lines)))
        return '\n'.join(lines)
    except Exception:
        return ""


def component_context(component_name):
    """Look up which application a component belongs to. Returns formatted string."""
    if not component_name:
        return ""
    driver = _get_driver()
    if not driver:
        return ""
    try:
        with driver.session() as s:
            result = s.run(
                "MATCH (a:Application)-[:CONTAINS]->(c:Component {name: $name}) "
                "RETURN a.name AS app",
                name=component_name,
            )
            rec = result.single()
        if not rec:
            return ""
        return "- Application (from knowledge graph): {}".format(rec['app'])
    except Exception:
        return ""


def domain_concepts_context(concept_names):
    """Look up Concept nodes by name. Returns formatted prompt section."""
    if not concept_names:
        return ""
    driver = _get_driver()
    if not driver:
        return ""
    try:
        with driver.session() as s:
            result = s.run(
                "MATCH (c:Concept) WHERE c.name IN $names "
                "RETURN c.name AS name, c.definition AS definition",
                names=list(concept_names),
            )
            records = [dict(r) for r in result]
        if not records:
            return ""
        lines = ["\n## Domain Context (from knowledge graph)"]
        for rec in records:
            defn = rec.get('definition', '')[:200]
            lines.append("- {}: {}".format(rec['name'], defn))
        return '\n'.join(lines)
    except Exception:
        return ""


def conforma_context(violation):
    """Build targeted Neo4j context for a conforma violation analysis.

    Extracts violated rule names from the summary, looks up matching
    PolicyRule nodes, and returns a short prompt section.
    """
    from utils.conforma_utils import extract_violation_rules
    summary = violation.get('violation_summary', '') or ''
    rules = extract_violation_rules(summary)
    rule_keys = _map_rules_to_policy_keys(rules)
    return policy_rules_context(rule_keys)


def build_context(failure):
    """Build targeted Neo4j context for a build failure analysis.

    Looks up the component's application and any matching concepts
    based on the error type.
    """
    parts = []
    comp = component_context(failure.get('component_name', ''))
    if comp:
        parts.append(comp)

    error_type = failure.get('error_type', '') or ''
    failed_step = failure.get('failed_step_name', '') or ''
    concepts = _concepts_for_build(error_type, failed_step)
    if concepts:
        parts.append(concepts)

    return '\n'.join(parts) if parts else ""


def release_context(context):
    """Build targeted Neo4j context for a release failure analysis.

    Looks up violated policy rules from logs and relevant failure patterns.
    """
    parts = []
    logs = context.get('logs', {})
    rules = set()
    for log_content in logs.values():
        if isinstance(log_content, str) and '[Violation]' in log_content:
            import re
            found = re.findall(r'\[Violation\]\s+([\w.]+)', log_content)
            rules.update(found)
    if rules:
        rule_keys = _map_rules_to_policy_keys(rules)
        pr = policy_rules_context(rule_keys)
        if pr:
            parts.append(pr)

    concepts = domain_concepts_context(['Conforma', 'ReleasePlan', 'FBC Fragment'])
    if concepts:
        parts.append(concepts)

    return '\n'.join(parts) if parts else ""


def _map_rules_to_policy_keys(rule_names):
    """Map raw Conforma rule names to Neo4j PolicyRule node names."""
    mapping = {
        'hermetic': 'policy_hermetic_build',
        'hermetic_task': 'policy_hermetic_build',
        'labels': 'policy_cpe_label',
        'required_labels': 'policy_cpe_label',
        'sbom_vendor_label': 'policy_sbom_vendor_label',
        'deprecated': 'policy_deprecated_task',
        'not_acceptable': 'policy_untrusted_image',
        'unpinned': 'policy_unpinned_task',
        'disallowed_packages': 'policy_package_source',
        'disallowed_package_source': 'policy_package_source',
        'fips': 'policy_fips_check',
        'source_image': 'policy_source_image',
        'rpm': 'policy_rpm_repository',
        'signing': 'policy_signing_key',
        'version_label': 'policy_version_label',
    }
    keys = set()
    for rule in rule_names:
        parts = rule.lower().split('.')
        for part in parts:
            if part in mapping:
                keys.add(mapping[part])
                break
        else:
            for keyword, key in mapping.items():
                if keyword in rule.lower():
                    keys.add(key)
                    break
    return list(keys)


def _concepts_for_build(error_type, failed_step):
    """Pick relevant domain concepts based on the build error."""
    concept_map = {
        'hermetic': ['Hermetic Build', 'Prefetching'],
        'prefetch': ['Prefetching', 'Hermetic Build'],
        'source-build': ['SBOM', 'Provenance'],
        'attestation': ['Attestation', 'Tekton Chains'],
        'sbom': ['SBOM'],
        'fips': ['Hermetic Build'],
    }
    names = set()
    combined = '{} {}'.format(error_type, failed_step).lower()
    for keyword, concepts in concept_map.items():
        if keyword in combined:
            names.update(concepts)
    if not names:
        return ""
    return domain_concepts_context(list(names))
