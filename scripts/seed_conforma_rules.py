#!/usr/bin/env python3
"""Seed the conforma_rule_catalog table from data/conforma_rule_catalog.json.

Usage:
    python scripts/seed_conforma_rules.py                          # seed from JSON
    python scripts/seed_conforma_rules.py --merge-reporter rhoai-3.5  # also merge reporter solutions
    python scripts/seed_conforma_rules.py --stats                  # show catalog stats
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


def _get_db():
    from config import CollectorConfig
    from repositories.connection import DatabaseConnection
    cfg = CollectorConfig.from_env()
    return DatabaseConnection(cfg.db)


def _get_repo(db):
    from repositories.conforma_rule_catalog_repository import ConformaRuleCatalogRepository
    return ConformaRuleCatalogRepository(db)


def seed_from_json(json_path):
    """Load rules from JSON and upsert into DB."""
    with open(json_path) as f:
        rules = json.load(f)

    db = _get_db()
    repo = _get_repo(db)
    count = 0
    for rule in rules:
        repo.upsert(
            rule_id=rule['rule_id'],
            rule_package=rule['rule_package'],
            rule_name=rule['rule_name'],
            description=rule.get('description'),
            policy_type=rule.get('policy_type'),
            collections=rule.get('collections'),
            doc_url=rule.get('doc_url'),
        )
        count += 1
    print("Seeded {} rules into conforma_rule_catalog".format(count))
    return count


def merge_reporter(branch):
    """Merge solutions from conforma-reporter into the catalog."""
    from clients.conforma_reporter_client import fetch_reporter_rules

    db = _get_db()
    repo = _get_repo(db)

    for env in ('prod', 'stage'):
        for build_type in ('latest', 'nightly'):
            rules = fetch_reporter_rules(branch, env=env, build_type=build_type)
            if not rules:
                continue
            matched = 0
            for r in rules:
                rule_code = r.get('rule', '')
                solution = r.get('solution', '')
                if rule_code and solution:
                    if repo.update_reporter_solution(rule_code, solution):
                        matched += 1
            print("  {}/{}/{}: {} reporter rules, {} matched catalog entries".format(
                branch, env, build_type, len(rules), matched))


def show_stats():
    """Print catalog statistics."""
    db = _get_db()
    repo = _get_repo(db)
    stats = repo.get_catalog_stats()
    print("Conforma Rule Catalog:")
    print("  Total rules: {}".format(stats['total']))
    print("  With reporter solution: {}".format(stats['with_reporter_solution']))
    print("  With typical fix: {}".format(stats['with_typical_fix']))
    print("  Packages: {}".format(stats['packages']))
    print("  Policy types: {}".format(stats['policy_types']))


def main():
    parser = argparse.ArgumentParser(description='Seed conforma rule catalog')
    parser.add_argument('--merge-reporter', metavar='BRANCH',
                        help='Also merge solutions from conforma-reporter branch')
    parser.add_argument('--stats', action='store_true',
                        help='Show catalog statistics')
    parser.add_argument('--json', default='data/conforma_rule_catalog.json',
                        help='Path to JSON catalog file')
    args = parser.parse_args()

    if args.stats:
        show_stats()
        return

    json_path = os.path.join(os.path.dirname(__file__), '..', args.json)
    if not os.path.exists(json_path):
        json_path = args.json
    seed_from_json(json_path)

    if args.merge_reporter:
        print("Merging reporter solutions from branch: {}".format(args.merge_reporter))
        merge_reporter(args.merge_reporter)

    show_stats()


if __name__ == '__main__':
    main()
