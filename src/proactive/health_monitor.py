"""Proactive health monitoring — detect degrading components and pattern cascades.

Uses component_health table and cross-app pattern data to generate warnings
before failures fully manifest. CVE checks query the OCI registry for SARIF data.
"""

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Dict

from datetime import datetime, timezone

from logger import setup_logger

logger = setup_logger(__name__)


def _nightly_component_for_app(application):
    """Derive FBC fragment component name from application.

    rhoai-v3-5-ea-2 → rhoai-fbc-fragment-v3-5-ea-2
    rhoai-v3-6      → rhoai-fbc-fragment-v3-6
    """
    parts = application.split('-v', 1)
    if len(parts) != 2:
        return '{}-fbc-fragment'.format(application)
    prefix = parts[0]
    version = parts[1]
    return '{}-fbc-fragment-v{}'.format(prefix, version)


@dataclass
class HealthWarning:
    """A proactive warning about a component or pattern."""
    component_name: str
    application: str
    signal_type: str  # degrading_health, pattern_cascade, dependency_risk
    severity: str  # critical, warning, info
    message: str
    evidence: Dict[str, Any]


class HealthMonitor:
    """Detects degrading components and cross-app pattern cascades."""

    def __init__(self, db):
        self.db = db

    def run_checks(self):
        """Run all proactive checks. Returns list of warnings."""
        warnings = []
        warnings.extend(self.get_degrading_components())
        warnings.extend(self.get_pattern_cascades())
        warnings.extend(self.get_repeat_failures())
        warnings.extend(self.get_cve_warnings())
        warnings.extend(self.get_stale_nightly_builds())
        return warnings

    def get_degrading_components(self):
        """Find components with health_status = warning/critical or consecutive_failures >= 2."""
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT component_name, application, health_status, health_score,
                       consecutive_failures, success_rate_last_7d, success_rate_last_30d,
                       total_failures_last_7d
                FROM component_health
                WHERE health_status IN ('warning', 'critical')
                   OR consecutive_failures >= 2
                ORDER BY health_score ASC NULLS FIRST, consecutive_failures DESC
            """)
            cols = [d[0] for d in cursor.description]
            rows = [dict(zip(cols, row)) for row in cursor.fetchall()]

        warnings = []
        for r in rows:
            severity = 'critical' if r['health_status'] == 'critical' or (r['consecutive_failures'] or 0) >= 5 else 'warning'
            score_str = '{}%'.format(r['health_score']) if r['health_score'] is not None else 'N/A'
            msg = '{} ({}) — health={}, consecutive_failures={}, 7d_success_rate={}'.format(
                r['component_name'], r['application'] or '?',
                score_str, r['consecutive_failures'] or 0,
                '{}%'.format(r['success_rate_last_7d']) if r['success_rate_last_7d'] is not None else 'N/A'
            )
            warnings.append(HealthWarning(
                component_name=r['component_name'],
                application=r['application'] or '',
                signal_type='degrading_health',
                severity=severity,
                message=msg,
                evidence=r
            ))
        return warnings

    def get_pattern_cascades(self):
        """Detect patterns spreading from one app to another in the last 14 days."""
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                WITH pattern_timeline AS (
                    SELECT
                        ep.id as pattern_id,
                        ep.pattern_name,
                        COALESCE(bf.application, cr.application) as application,
                        MIN(COALESCE(bf.first_detected_at, cr.first_detected_at)) as first_seen
                    FROM error_patterns ep
                    JOIN ai_analysis aa ON aa.error_pattern_id = ep.id
                    LEFT JOIN build_failures bf ON bf.id = aa.build_failure_id
                    LEFT JOIN conforma_results cr ON cr.id = aa.conforma_result_id
                    WHERE COALESCE(bf.first_detected_at, cr.first_detected_at) > NOW() - INTERVAL '30 days'
                    GROUP BY ep.id, ep.pattern_name, COALESCE(bf.application, cr.application)
                )
                SELECT p1.pattern_name,
                       p1.application as source_app,
                       p2.application as spread_to_app,
                       p1.first_seen as source_date,
                       p2.first_seen as spread_date
                FROM pattern_timeline p1
                JOIN pattern_timeline p2 ON p1.pattern_id = p2.pattern_id
                WHERE p1.application != p2.application
                  AND p2.first_seen > p1.first_seen
                  AND p2.first_seen < p1.first_seen + INTERVAL '14 days'
                ORDER BY p2.first_seen DESC
            """)
            cols = [d[0] for d in cursor.description]
            rows = [dict(zip(cols, row)) for row in cursor.fetchall()]

        warnings = []
        seen = set()
        for r in rows:
            key = (r['pattern_name'], r['spread_to_app'])
            if key in seen:
                continue
            seen.add(key)
            msg = 'Pattern "{}" spread from {} to {} (source: {}, spread: {})'.format(
                r['pattern_name'], r['source_app'], r['spread_to_app'],
                r['source_date'].strftime('%Y-%m-%d') if r['source_date'] else '?',
                r['spread_date'].strftime('%Y-%m-%d') if r['spread_date'] else '?'
            )
            warnings.append(HealthWarning(
                component_name='(cross-app)',
                application=r['spread_to_app'],
                signal_type='pattern_cascade',
                severity='warning',
                message=msg,
                evidence=r
            ))
        return warnings

    def get_repeat_failures(self):
        """Find components that keep failing with the same error after fixes were attempted."""
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    bf.component_name,
                    bf.application,
                    a.failure_category,
                    COUNT(*) as failure_count,
                    MAX(ra.attempted_at) as last_fix_attempt
                FROM build_failures bf
                JOIN ai_analysis a ON a.build_failure_id = bf.id
                JOIN resolution_attempts ra ON ra.build_failure_id = bf.id
                WHERE bf.is_resolved = FALSE
                  AND ra.was_successful = FALSE
                  AND bf.first_detected_at > NOW() - INTERVAL '30 days'
                GROUP BY bf.component_name, bf.application, a.failure_category
                HAVING COUNT(*) >= 2
                ORDER BY COUNT(*) DESC
            """)
            cols = [d[0] for d in cursor.description]
            rows = [dict(zip(cols, row)) for row in cursor.fetchall()]

        warnings = []
        for r in rows:
            msg = '{} ({}) — {} failed fixes for "{}", still unresolved'.format(
                r['component_name'], r['application'],
                r['failure_count'], r['failure_category']
            )
            warnings.append(HealthWarning(
                component_name=r['component_name'],
                application=r['application'],
                signal_type='repeat_failure',
                severity='critical' if r['failure_count'] >= 3 else 'warning',
                message=msg,
                evidence=r
            ))
        return warnings

    def get_cve_warnings(self, application=None):
        """Check latest snapshot for critical/high CVEs via SARIF referrers."""
        namespace = os.environ.get('NAMESPACE', '')
        app_name = application or os.environ.get('APPLICATION_NAME', '')
        if not namespace or not app_name:
            return []

        try:
            from clients.konflux_client import KonfluxClient
            from clients.registry_client import RegistryClient

            kc = KonfluxClient(namespace=namespace)
            snapshots = kc.get_snapshots(app_filter=app_name, limit=1)
            if not snapshots:
                return []

            snapshot = snapshots[0]
            components = snapshot.get('spec', {}).get('components', [])
            rc = RegistryClient()
            batch = rc.fetch_sarif_batch(components, timeout=60)

            severity_map = {'error': 'critical', 'warning': 'high', 'note': 'medium'}
            warnings = []
            for name, results in batch.items():
                if not results:
                    continue

                counts = {'critical': 0, 'high': 0, 'medium': 0, 'low': 0}
                for r in results:
                    sev = severity_map.get(r.get('level', ''), 'low')
                    counts[sev] += 1

                if counts['critical'] > 0:
                    msg = '{} — {} critical, {} high CVEs in latest image'.format(
                        name, counts['critical'], counts['high'])
                    warnings.append(HealthWarning(
                        component_name=name,
                        application=app_name,
                        signal_type='critical_cves',
                        severity='critical',
                        message=msg,
                        evidence=counts,
                    ))
                elif counts['high'] >= 5:
                    msg = '{} — {} high CVEs in latest image'.format(name, counts['high'])
                    warnings.append(HealthWarning(
                        component_name=name,
                        application=app_name,
                        signal_type='high_cves',
                        severity='warning',
                        message=msg,
                        evidence=counts,
                    ))

            return warnings
        except Exception as e:
            logger.debug("CVE health check skipped: %s", e)
            return []

    def get_stale_nightly_builds(self, staleness_hours=None):
        """Detect FBC fragment components with no recent successful build."""
        threshold = staleness_hours or int(os.environ.get('NIGHTLY_STALENESS_HOURS', '24'))
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT component_name, application, last_successful_build,
                       last_failed_build, current_status
                FROM component_health
                WHERE component_name LIKE '%%fbc-fragment%%'
                  AND (last_successful_build IS NULL
                       OR last_successful_build < NOW() - (%s || ' hours')::INTERVAL)
                ORDER BY last_successful_build ASC NULLS FIRST
            """, (threshold,))
            cols = [d[0] for d in cursor.description]
            rows = [dict(zip(cols, row)) for row in cursor.fetchall()]

        warnings = []
        now = datetime.now(timezone.utc)
        for r in rows:
            last_build = r['last_successful_build']
            if last_build is None:
                severity = 'critical'
                msg = '{} ({}) — no successful build on record'.format(
                    r['component_name'], r['application'] or '?')
            else:
                if last_build.tzinfo is None:
                    last_build = last_build.replace(tzinfo=timezone.utc)
                hours_ago = (now - last_build).total_seconds() / 3600
                severity = 'critical' if hours_ago >= 48 else 'warning'
                msg = '{} ({}) — last successful build {:.0f}h ago ({})'.format(
                    r['component_name'], r['application'] or '?',
                    hours_ago, last_build.strftime('%Y-%m-%d %H:%M'))

            warnings.append(HealthWarning(
                component_name=r['component_name'],
                application=r['application'] or '',
                signal_type='stale_nightly',
                severity=severity,
                message=msg,
                evidence=r,
            ))
        return warnings

    def get_component_health_summary(self, application=None):
        """Get health summary for all components, optionally filtered by app."""
        with self.db.connection() as conn:
            cursor = conn.cursor()
            if application:
                cursor.execute("""
                    SELECT component_name, application, current_status, health_score,
                           health_status, consecutive_failures,
                           success_rate_last_7d, total_failures_last_7d,
                           last_successful_build, last_failed_build
                    FROM component_health
                    WHERE application = %s
                    ORDER BY health_score ASC NULLS FIRST, consecutive_failures DESC
                """, (application,))
            else:
                cursor.execute("""
                    SELECT component_name, application, current_status, health_score,
                           health_status, consecutive_failures,
                           success_rate_last_7d, total_failures_last_7d,
                           last_successful_build, last_failed_build
                    FROM component_health
                    ORDER BY health_score ASC NULLS FIRST, consecutive_failures DESC
                """)
            cols = [d[0] for d in cursor.description]
            return [dict(zip(cols, row)) for row in cursor.fetchall()]

    def get_nightly_status(self, application):
        """Assemble nightly status: FBC fragment health + build blockers.

        Returns a dict suitable for CLI, MCP, and API consumption.
        """
        fbc_name = _nightly_component_for_app(application)

        fbc_health = None
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT component_name, last_successful_build, last_failed_build,
                       current_status, health_score
                FROM component_health
                WHERE component_name = %s
            """, (fbc_name,))
            row = cursor.fetchone()
            if row:
                cols = [d[0] for d in cursor.description]
                fbc_health = dict(zip(cols, row))

        fbc_image = None
        try:
            from clients.kubernetes import KubernetesClient
            kc = KubernetesClient(namespace=os.environ.get('NAMESPACE', 'rhoai-tenant'))
            meta = kc.get_component_metadata(fbc_name)
            if meta:
                fbc_image = meta.get('container_image') or meta.get('last_promoted_image')
        except Exception:
            pass

        fbc_conforma = None
        try:
            from repositories.conforma_repository import ConformaRepository
            conforma_repo = ConformaRepository(self.db)
            violation = conforma_repo.get_violation_details(fbc_name, application)
            if violation:
                fbc_conforma = {
                    'violations_count': violation.get('violations_count', 0),
                    'warnings_count': violation.get('warnings_count', 0),
                    'scenario': violation.get('scenario', ''),
                    'violation_summary': violation.get('violation_summary', ''),
                }
        except Exception:
            pass

        from repositories.build_failure_repository import BuildFailureRepository
        build_repo = BuildFailureRepository(self.db)
        triage = build_repo.get_triage_summary(application)
        failing = [c for c in triage.get('failing_components', [])
                    if c['component'] != fbc_name]

        comp_names = [c['component'] for c in failing]
        last_success_map = {}
        if comp_names:
            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT component_name, last_successful_build
                    FROM component_health
                    WHERE component_name = ANY(%s)
                """, (comp_names,))
                for row in cursor.fetchall():
                    if row[1]:
                        last_success_map[row[0]] = row[1]

        blockers = []
        for comp in failing:
            comp_name = comp['component']

            blocker = {
                'component': comp_name,
                'error_type': comp.get('error_type', ''),
                'error_message': comp.get('error_message', ''),
                'first_detected_at': comp.get('first_detected_at'),
                'last_successful_build': last_success_map.get(comp_name),
                'has_analysis': comp.get('ai_analyzed', False),
            }

            if comp.get('ai_analyzed'):
                try:
                    from repositories.ai_analysis_repository import AIAnalysisRepository
                    ai_repo = AIAnalysisRepository(self.db)
                    analysis = ai_repo.get_analysis_by_component(comp_name, application)
                    if analysis:
                        blocker['failure_category'] = analysis.get('failure_category', '')
                        blocker['root_cause_summary'] = (analysis.get('root_cause') or '')[:150]
                except Exception:
                    pass

            blockers.append(blocker)

        return {
            'application': application,
            'fbc_component': fbc_name,
            'fbc_health': fbc_health,
            'fbc_image': fbc_image,
            'fbc_conforma': fbc_conforma,
            'blockers': blockers,
            'blockers_count': len(blockers),
        }

    def get_stale_components(self, application=None):
        """Detect components where branch HEAD is ahead of lastBuiltCommit.

        Compares each component's last built commit (from cluster Component CR)
        against the GitHub branch HEAD. Returns only components with a mismatch.
        """
        from clients.github_client import GitHubClient, parse_github_repo
        from clients.kubernetes import KubernetesClient

        namespace = os.environ.get('NAMESPACE', '')
        app_name = application or os.environ.get('APPLICATION_NAME', '')
        token = os.environ.get('GITHUB_TOKEN', '')
        if not namespace:
            return {'application': app_name, 'stale': [], 'skipped': 0,
                    'error': 'NAMESPACE not set'}

        kc = KubernetesClient(namespace=namespace)
        all_comps = kc.list_components(application=app_name or None)

        gh = GitHubClient(token)
        candidates = []
        skipped = 0
        for comp in all_comps:
            repo_url = comp.get('repository_url', '')
            built = comp.get('last_built_commit', '')
            branch = comp.get('branch', '')
            parsed = parse_github_repo(repo_url)
            if not parsed or not branch:
                skipped += 1
                continue
            if not built:
                skipped += 1
                continue
            candidates.append({**comp, '_owner': parsed[0], '_repo': parsed[1]})

        unique_refs = {}
        for c in candidates:
            key = (c['_owner'], c['_repo'], c['branch'])
            if key not in unique_refs:
                unique_refs[key] = None

        def _fetch_head(key):
            owner, repo, branch = key
            try:
                sha = gh.get_ref_sha(owner, repo, branch)
                return (key, sha)
            except Exception:
                return (key, None)

        with ThreadPoolExecutor(max_workers=10) as pool:
            for key, sha in pool.map(lambda k: _fetch_head(k), unique_refs.keys()):
                unique_refs[key] = sha

        api_errors = sum(1 for v in unique_refs.values() if v is None)

        stale = []
        for comp in candidates:
            key = (comp['_owner'], comp['_repo'], comp['branch'])
            head = unique_refs.get(key)
            if not head:
                continue
            if head == comp['last_built_commit']:
                continue
            stale.append({
                'component': comp['name'],
                'application': comp.get('application', ''),
                'repository_url': comp.get('repository_url', ''),
                'branch': comp['branch'],
                'built_commit': comp['last_built_commit'][:12],
                'head_commit': head[:12],
                'built_commit_full': comp['last_built_commit'],
                'head_commit_full': head,
            })

        stale.sort(key=lambda s: s['component'])
        result = {
            'application': app_name,
            'stale': stale,
            'stale_count': len(stale),
            'checked': len(candidates),
            'skipped': skipped,
            'unique_refs': len(unique_refs),
        }
        if api_errors:
            result['api_errors'] = api_errors
            result['warning'] = '{} of {} unique refs failed GitHub API lookup (rate limit?)'.format(
                api_errors, len(unique_refs))
        return result
