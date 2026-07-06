"""Local Conforma (EC) policy evaluation against cluster snapshots.

Runs `ec validate image` locally against snapshot components using
any policy from the cluster (future, stage, prod). Results go into
the existing conforma_results table with is_future=True so they
appear in `ic get conforma --all`.

This replicates what the conforma-reporter does, but independently
from the cluster data — no need to fetch the reporter CSV.
"""

import json
import os
import shutil
import subprocess
import tempfile

from clients.konflux_client import KonfluxClient
from logger import setup_logger
from repositories import ConformaRepository, DatabaseConnection

logger = setup_logger(__name__)

POLICY_TYPES = {
    'registry': 'registry-rhoai-{tier}',
    'fbc': 'fbc-rhoai-{tier}',
    'chart': 'registry-rhoai-chart-{tier}',
}


class ConformaEvaluator:
    """Runs ec validate locally against snapshot images."""

    def __init__(self, config, db=None, conforma_repo=None, konflux=None):
        if db is None:
            db = DatabaseConnection(config.db)
        self.config = config
        self.conforma_repo = conforma_repo or ConformaRepository(db)
        self.konflux = konflux or KonfluxClient(namespace=config.k8s.namespace)

    def get_snapshot_components(self, app_name, component_filter=None):
        """Get components from the latest snapshot with the most entries."""
        snapshots = self.konflux.get_snapshots(app_filter=app_name, limit=20)
        if not snapshots:
            return None, []

        best = max(snapshots, key=lambda s: len(s.get('spec', {}).get('components', [])))
        snap_name = best.get('metadata', {}).get('name', '?')
        components = best.get('spec', {}).get('components', [])

        if component_filter:
            components = [c for c in components if component_filter in c.get('name', '')]

        return snap_name, components

    def resolve_policies(self, app_name, tier='future'):
        """Resolve EC policy names for a given tier.

        Returns list of (policy_type, policy_name) tuples that exist on the cluster.
        """
        version = _extract_version(app_name)

        if tier == 'future':
            candidates = {
                'registry': 'registry-rhoai-prod-{}-future'.format(version),
                'fbc': 'fbc-rhoai-prod-{}-future'.format(version),
                'chart': 'registry-rhoai-chart-prod-{}-future'.format(version),
            }
        elif tier == 'stage':
            candidates = {
                'registry': 'registry-rhoai-stage',
                'fbc': 'fbc-rhoai-stage',
                'chart': 'registry-rhoai-chart-stage',
            }
        else:
            candidates = {
                'registry': 'registry-rhoai-prod',
                'fbc': 'fbc-rhoai-prod',
                'chart': 'registry-rhoai-chart-prod',
            }

        found = []
        all_policies = self.konflux.get_ec_policies()
        known = {p.get('metadata', {}).get('name') for p in all_policies}
        for ptype, name in candidates.items():
            if name in known:
                found.append((ptype, name))
            else:
                logger.debug("Policy not found on cluster: %s", name)
        return found

    def categorize_component(self, comp_name):
        """Classify a component as fbc, chart, or registry (default)."""
        name = comp_name.lower()
        if 'fbc-fragment' in name:
            return 'fbc'
        if '-chart-' in name or name.endswith('-chart'):
            return 'chart'
        return 'registry'

    def evaluate(self, components, policy_name, workers=5):
        """Run ec validate against components with the given policy.

        Returns parsed JSON output from ec validate.
        """
        ec_bin = shutil.which('ec')
        if not ec_bin:
            raise RuntimeError("ec CLI not found. Install from https://github.com/enterprise-contract/ec-cli")

        snapshot_spec = {
            'components': [
                {'name': c['name'], 'containerImage': c['containerImage']}
                for c in components
            ]
        }

        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(snapshot_spec, f)
            snap_file = f.name

        namespace = self.config.k8s.namespace
        policy_ref = '{}/{}'.format(namespace, policy_name)

        cmd = [
            ec_bin, 'validate', 'image',
            '--images', snap_file,
            '--policy', policy_ref,
            '--output', 'json',
            '--strict=false',
            '--ignore-rekor',
            '--workers', str(workers),
        ]

        logger.info("Running: ec validate --policy %s (%d components, %d workers)",
                     policy_name, len(components), workers)

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=3600
            )
            output = result.stdout
            if not output:
                logger.error("ec validate produced no output. stderr: %s", result.stderr[:500])
                return None

            return json.loads(output)
        except subprocess.TimeoutExpired:
            logger.error("ec validate timed out after 1 hour")
            return None
        except json.JSONDecodeError as e:
            logger.error("Failed to parse ec validate output: %s", e)
            return None
        finally:
            os.unlink(snap_file)

    def save_results(self, app_name, policy_name, eval_output, snap_name=''):
        """Parse ec validate JSON and save failing components to DB."""
        if not eval_output:
            return 0

        ec_components = eval_output.get('components', [])
        grouped = _group_by_component(ec_components)
        saved = 0

        for comp_name, data in grouped.items():
            if data['violations_count'] == 0:
                continue

            scenario = 'conforma-evaluate-{}'.format(policy_name)
            pr_name = 'evaluate-{}-{}'.format(policy_name, comp_name)

            violations = {
                'violations_count': data['violations_count'],
                'warnings_count': data['warnings_count'],
                'successes_count': data['successes_count'],
                'violation_summary': data['violation_summary'],
                'violation_details': None,
            }
            comp_info = {
                'snapshot_name': snap_name,
                'container_image': data.get('container_image', ''),
                'repository_url': '',
                'commit_sha': '',
                'commit_url': '',
            }
            ok = self.conforma_repo.upsert_violation(
                application=app_name, component=comp_name,
                scenario=scenario, pr_name=pr_name, pr_uid='',
                violations=violations, comp_info=comp_info,
                is_future=True,
            )
            if ok:
                saved += 1
        return saved

    def run(self, app_name=None, policy_tier='future', workers=5,
            component_filter=None):
        """Full pipeline: snapshot → evaluate → save.

        Returns dict with stats.
        """
        app_name = app_name or self.config.k8s.application_name

        logger.info("Resolving policies for {} (tier: {})...".format(app_name, policy_tier))
        policies = self.resolve_policies(app_name, tier=policy_tier)
        if not policies:
            logger.info("No {} policies found on cluster for {}".format(policy_tier, app_name))
            return {'evaluated': 0, 'failing': 0, 'violations': 0}

        logger.info("Fetching snapshot...")
        snap_name, all_components = self.get_snapshot_components(app_name, component_filter)
        if not all_components:
            logger.info("No components found in snapshot for {}".format(app_name))
            return {'evaluated': 0, 'failing': 0, 'violations': 0}

        logger.info("Snapshot: {} ({} components)".format(snap_name, len(all_components)))

        total_saved = 0
        total_violations = 0
        total_evaluated = 0

        for ptype, policy_name in policies:
            comps = [c for c in all_components
                     if self.categorize_component(c['name']) == ptype]
            if not comps:
                continue

            logger.info("\nEvaluating {} components with {} ({})...".format(
                len(comps), policy_name, ptype))
            if len(comps) > 10:
                est_min = len(comps) * 24 / 60
                logger.info("Estimated time: ~{:.0f} minutes".format(est_min))

            eval_output = self.evaluate(comps, policy_name, workers=workers)
            if eval_output is None:
                logger.info("Evaluation failed for {}".format(policy_name))
                continue

            ec_comps = eval_output.get('components', [])
            grouped = _group_by_component(ec_comps)
            failing = {k: v for k, v in grouped.items() if v['violations_count'] > 0}

            for name, data in grouped.items():
                sym = '✗' if data['violations_count'] > 0 else '✓'
                logger.info("  {} {}: {} violations, {} warnings".format(
                    sym, name, data['violations_count'], data['warnings_count']))

            total_evaluated += len(grouped)
            total_violations += sum(d['violations_count'] for d in failing.values())
            total_saved += self.save_results(app_name, policy_name, eval_output, snap_name)

        logger.info("\n" + "=" * 50)
        logger.info("Evaluation complete")
        logger.info("  Components evaluated: {}".format(total_evaluated))
        logger.info("  Components failing: {}".format(total_saved))
        logger.info("  Total violations: {}".format(total_violations))
        logger.info("  Results saved. View with: ic get conforma --all")

        return {
            'evaluated': total_evaluated,
            'failing': total_saved,
            'violations': total_violations,
        }


def _extract_version(app_name):
    """rhoai-v3-5 → v3-5, rhoai-v3-5-ea-2 → v3-5-ea-2"""
    parts = app_name.split('-')
    for i, p in enumerate(parts):
        if p.startswith('v') and any(c.isdigit() for c in p):
            return '-'.join(parts[i:])
    return app_name


def _group_by_component(ec_components):
    """Group ec validate per-image results by component name.

    ec validate returns one entry per image (including per-arch images).
    We aggregate to one entry per logical component (dedup arch suffixes).
    """
    grouped = {}
    for c in ec_components:
        raw_name = c.get('name', '')
        name = _strip_arch_suffix(raw_name)
        if name not in grouped:
            grouped[name] = {
                'violations_count': 0,
                'warnings_count': 0,
                'successes_count': 0,
                'violation_summary': '',
                'container_image': c.get('containerImage', ''),
                '_violations': [],
            }
        entry = grouped[name]
        violations = c.get('violations', [])
        warnings = c.get('warnings', [])
        successes = c.get('successes', [])
        entry['violations_count'] += len(violations)
        entry['warnings_count'] += len(warnings)
        entry['successes_count'] += len(successes)
        for v in violations:
            entry['_violations'].append(v)

    for _name, entry in grouped.items():
        lines = []
        seen_codes = set()
        for v in entry['_violations']:
            code = v.get('metadata', {}).get('code', 'unknown')
            msg = v.get('msg', '')
            if code not in seen_codes:
                lines.append('✕ [Violation] {}'.format(code))
                if msg:
                    lines.append('  {}'.format(msg[:200]))
                seen_codes.add(code)
        entry['violation_summary'] = '\n'.join(lines)
        del entry['_violations']

    return grouped


def _strip_arch_suffix(name):
    """Remove per-arch suffixes: comp-sha256:abc123-amd64 → comp"""
    if '-sha256:' in name:
        return name.split('-sha256:')[0]
    return name
