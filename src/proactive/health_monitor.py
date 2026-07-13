"""Proactive health monitoring — detect degrading components and pattern cascades.

Uses component_health table and cross-app pattern data to generate warnings
before failures fully manifest. CVE checks query the OCI registry for SARIF data.
"""

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Dict

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


def _correlate_nightly_chain(status, history):
    """Correlate nightly status + history into a 4-step chain view.

    Pure function — no DB or API calls.
    """
    steps = []
    fbc_component = status.get('fbc_component', '')

    # Step 1: GHA Trigger
    gha = status.get('gha_validation')
    if gha:
        conclusion = gha.get('conclusion', 'unknown')
        gha_status = 'pass' if conclusion == 'success' else 'fail'
        steps.append({
            'name': 'GHA Trigger',
            'status': gha_status,
            'timestamp': gha.get('created_at'),
            'detail': 'trigger-nightlies.yaml {}'.format(conclusion),
            'url': gha.get('url'),
        })
    else:
        steps.append({
            'name': 'GHA Trigger',
            'status': 'skip',
            'timestamp': None,
            'detail': 'no GHA data (token missing?)',
            'url': None,
        })

    # Step 2: Operator Build — nightly builds excluding FBC fragment
    nightly_builds = history.get('nightly_builds', [])
    operator_builds = [b for b in nightly_builds if b['component_name'] != fbc_component]
    if operator_builds:
        failed = [b for b in operator_builds if b.get('status') == 'Failed']
        total = len(operator_builds)
        ts = operator_builds[0].get('build_completion_time')
        timestamp = ts.isoformat() if hasattr(ts, 'isoformat') else (str(ts) if ts else None)
        if failed:
            steps.append({
                'name': 'Operator Build',
                'status': 'fail',
                'timestamp': timestamp,
                'detail': '{}/{} failed'.format(len(failed), total),
                'url': None,
            })
        else:
            steps.append({
                'name': 'Operator Build',
                'status': 'pass',
                'timestamp': timestamp,
                'detail': '{}/{} passed'.format(total, total),
                'url': None,
            })
    else:
        steps.append({
            'name': 'Operator Build',
            'status': 'skip',
            'timestamp': None,
            'detail': 'no recent nightly builds',
            'url': None,
        })

    # Step 3: FBC Fragment
    fbc_health = status.get('fbc_health')
    fbc_builds = [b for b in nightly_builds if b['component_name'] == fbc_component]
    if fbc_health:
        fbc_st = fbc_health.get('current_status', 'unknown')
        ts = fbc_builds[0]['build_completion_time'] if fbc_builds else None
        timestamp = ts.isoformat() if hasattr(ts, 'isoformat') else (str(ts) if ts else None)
        fbc_step = {
            'name': 'FBC Fragment',
            'status': 'pass' if fbc_st == 'Succeeded' else 'fail',
            'timestamp': timestamp,
            'detail': '{} build {}'.format(fbc_component, 'succeeded' if fbc_st == 'Succeeded' else 'failed'),
            'url': None,
        }
        blockers = status.get('blockers', [])
        if blockers:
            fbc_step['blockers'] = [b['component'] for b in blockers]
        steps.append(fbc_step)
    elif fbc_builds:
        fbc_st = fbc_builds[0].get('status', 'unknown')
        ts = fbc_builds[0]['build_completion_time']
        timestamp = ts.isoformat() if hasattr(ts, 'isoformat') else str(ts)
        steps.append({
            'name': 'FBC Fragment',
            'status': 'pass' if fbc_st == 'Succeeded' else 'fail',
            'timestamp': timestamp,
            'detail': '{} build {}'.format(fbc_component, fbc_st.lower()),
            'url': None,
        })
    else:
        steps.append({
            'name': 'FBC Fragment',
            'status': 'skip',
            'timestamp': None,
            'detail': 'no FBC data',
            'url': None,
        })

    # Step 4: PCC Cache
    pcc = status.get('pcc_freshness')
    if pcc:
        pcc_st = pcc.get('status', 'unknown')
        if pcc_st == 'fresh':
            steps.append({
                'name': 'PCC Cache',
                'status': 'pass',
                'timestamp': None,
                'detail': '{} versions cached'.format(pcc.get('cached_versions', 0)),
                'url': None,
            })
        elif pcc_st == 'stale':
            steps.append({
                'name': 'PCC Cache',
                'status': 'fail',
                'timestamp': None,
                'detail': 'cache stale',
                'url': None,
            })
        else:
            steps.append({
                'name': 'PCC Cache',
                'status': 'skip',
                'timestamp': None,
                'detail': 'PCC status unknown',
                'url': None,
            })
    else:
        steps.append({
            'name': 'PCC Cache',
            'status': 'skip',
            'timestamp': None,
            'detail': 'PCC check unavailable',
            'url': None,
        })

    # Derive chain_date from GHA or first build
    chain_date = None
    if gha and gha.get('created_at'):
        chain_date = gha['created_at'][:10]
    elif nightly_builds:
        ts = nightly_builds[0].get('build_completion_time')
        if ts:
            chain_date = str(ts)[:10]

    # Derive chain_status
    statuses = [s['status'] for s in steps]
    if all(s == 'skip' for s in statuses):
        chain_status = 'unknown'
    elif any(s == 'fail' for s in statuses):
        chain_status = 'broken'
    else:
        chain_status = 'healthy'

    break_point = None
    if chain_status == 'broken':
        for s in steps:
            if s['status'] == 'fail':
                break_point = s['name']
                break

    return {
        'chain_date': chain_date,
        'steps': steps,
        'chain_status': chain_status,
        'break_point': break_point,
    }


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

    def run_checks(self, application=None):
        """Run all proactive checks. Returns list of warnings."""
        warnings = []
        warnings.extend(self.get_degrading_components(application=application))
        warnings.extend(self.get_pattern_cascades())
        warnings.extend(self.get_repeat_failures())
        warnings.extend(self.get_cve_warnings())
        warnings.extend(self.get_stale_nightly_builds())
        warnings.extend(self.get_stale_warnings())
        warnings.extend(self.get_pcc_freshness_warnings())
        return warnings

    def get_degrading_components(self, application=None):
        """Find components with health_status = warning/critical or consecutive_failures >= 2."""
        with self.db.connection() as conn:
            cursor = conn.cursor()
            if application:
                cursor.execute("""
                    SELECT component_name, application, health_status, health_score,
                           consecutive_failures, success_rate_last_7d, success_rate_last_30d,
                           total_failures_last_7d
                    FROM component_health
                    WHERE (health_status IN ('warning', 'critical')
                       OR consecutive_failures >= 2)
                      AND application = %s
                    ORDER BY health_score ASC NULLS FIRST, consecutive_failures DESC
                """, (application,))
            else:
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
        now = datetime.now(UTC)
        for r in rows:
            last_build = r['last_successful_build']
            if last_build is None:
                severity = 'critical'
                msg = '{} ({}) — no successful build on record'.format(
                    r['component_name'], r['application'] or '?')
            else:
                if last_build.tzinfo is None:
                    last_build = last_build.replace(tzinfo=UTC)
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

        gha_validation = None
        try:
            from clients.github_client import GitHubClient
            gh = GitHubClient(token=os.environ.get('GITHUB_TOKEN'))
            runs = gh.get_workflow_runs(
                'red-hat-data-services', 'rhods-devops-infra',
                'trigger-nightlies.yaml', limit=3)
            if runs:
                latest = runs[0]
                gha_validation = {
                    'conclusion': latest.get('conclusion'),
                    'status': latest.get('status'),
                    'created_at': latest.get('created_at'),
                    'url': latest.get('html_url'),
                }
        except Exception:
            pass

        pcc_freshness = self._check_pcc_freshness()

        return {
            'application': application,
            'fbc_component': fbc_name,
            'fbc_health': fbc_health,
            'fbc_image': fbc_image,
            'fbc_conforma': fbc_conforma,
            'blockers': blockers,
            'blockers_count': len(blockers),
            'gha_validation': gha_validation,
            'pcc_freshness': pcc_freshness,
        }

    def get_nightly_chain(self, application):
        """Get the nightly build chain view — 4-step timeline."""
        status = self.get_nightly_status(application)
        from repositories.build_failure_repository import BuildFailureRepository
        build_repo = BuildFailureRepository(self.db)
        history = build_repo.get_nightly_history(application, days=3)
        result = _correlate_nightly_chain(status, history)
        result['application'] = application
        return result

    def get_pcc_freshness_warnings(self):
        """Generate HealthWarning entries if the PCC cache is stale."""
        pcc = self._check_pcc_freshness()
        if not pcc or pcc.get('status') != 'stale':
            return []

        missing = pcc.get('missing_versions', [])
        preview = ', '.join(missing[:5])
        if len(missing) > 5:
            preview += ' (+{} more)'.format(len(missing) - 5)

        return [HealthWarning(
            component_name='pcc-cache',
            application='(release-infra)',
            signal_type='pcc_stale',
            severity='critical',
            message='PCC cache is stale — {} version(s) in registry not in cache: {}. '
                    'FBC fragment builds will produce catalogs missing these versions. '
                    'Run regen-pcc-cache workflow in RHOAI-Build-Config.'.format(
                        len(missing), preview),
            evidence=pcc,
        )]

    def _check_pcc_freshness(self):
        """Check if the PCC (Pre-Computed Catalog) cache is up to date.

        Compares the shipped versions cached in RHOAI-Build-Config against
        the actual bundle tags published in registry.redhat.io. If the registry
        has versions not present in the PCC cache, the FBC fragment builds will
        produce catalogs missing those versions — potentially pruning shipped
        releases from the operator channel.

        Returns a dict with status ('fresh', 'stale', 'unknown'), details,
        and the last regen-pcc-cache workflow run info.
        """
        build_config_owner = os.environ.get(
            'GITHUB_BUILD_CONFIG_OWNER', 'red-hat-data-services')
        build_config_repo = os.environ.get(
            'GITHUB_BUILD_CONFIG_REPO', 'RHOAI-Build-Config')
        token = os.environ.get('GITHUB_TOKEN', '')

        if not token:
            return None

        result = {
            'status': 'unknown',
            'cached_versions': 0,
            'registry_versions': 0,
            'missing_versions': [],
            'last_regen': None,
        }

        try:
            from clients.github_client import GitHubClient
            gh = GitHubClient(token=token)

            runs = gh.get_workflow_runs(
                build_config_owner, build_config_repo,
                'regen-pcc-cache.yaml', limit=1)
            if runs:
                latest = runs[0]
                result['last_regen'] = {
                    'conclusion': latest.get('conclusion'),
                    'date': latest.get('created_at'),
                    'url': latest.get('html_url'),
                }

            cached_content = gh.get_file_content(
                build_config_owner, build_config_repo,
                'pcc/shipped_rhoai_versions_granular.txt')
            if not cached_content:
                logger.debug("PCC freshness: could not read shipped versions file")
                return result

            cached_versions = set()
            for line in cached_content.strip().splitlines():
                tag = line.strip()
                if tag:
                    cached_versions.add(tag)
            result['cached_versions'] = len(cached_versions)

            from clients.registry_client import RegistryClient
            rc = RegistryClient()
            registry_tags = rc.list_tags(
                'registry.redhat.io', 'rhoai/odh-operator-bundle')

            if not registry_tags:
                logger.debug("PCC freshness: could not list registry tags "
                             "(may need registry.redhat.io credentials)")
                return result

            registry_versions = {t for t in registry_tags if t.startswith('v')}
            result['registry_versions'] = len(registry_versions)

            missing = sorted(registry_versions - cached_versions)
            result['missing_versions'] = missing

            if missing:
                result['status'] = 'stale'
                logger.warning(
                    "PCC cache is stale: %d version(s) in registry.redhat.io "
                    "not in PCC cache: %s", len(missing), ', '.join(missing[:5]))
            else:
                result['status'] = 'fresh'

        except Exception as exc:
            logger.debug("PCC freshness check failed: %s", exc)

        return result

    def get_stale_components(self, application=None, use_cache=True,
                              diagnose=True):
        """Detect components where branch HEAD is ahead of lastBuiltCommit.

        Compares each component's last built commit (from cluster Component CR)
        against the GitHub branch HEAD. Returns only components with a mismatch.
        """
        from clients.github_client import GitHubClient, parse_github_repo
        from clients.kubernetes import KubernetesClient
        from proactive.ref_cache import CacheConfig, RefCache

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

        unique_ref_keys = []
        seen = set()
        for c in candidates:
            key = '{}/{}/{}'.format(c['_owner'], c['_repo'], c['branch'])
            if key not in seen:
                seen.add(key)
                unique_ref_keys.append(key)

        cache = RefCache(CacheConfig.from_env()) if use_cache else None
        cached = cache.get_batch(unique_ref_keys) if cache else {}

        to_fetch = [k for k in unique_ref_keys if cached.get(k) is None]

        def _fetch_head(cache_key):
            parts = cache_key.split('/', 2)
            try:
                sha = gh.get_ref_sha(parts[0], parts[1], parts[2])
                return (cache_key, sha)
            except Exception:
                return (cache_key, None)

        fetched = {}
        if to_fetch:
            with ThreadPoolExecutor(max_workers=10) as pool:
                for key, sha in pool.map(_fetch_head, to_fetch):
                    if sha:
                        fetched[key] = sha

            if cache and fetched:
                cache.put_batch(fetched)

        ref_shas = {}
        for key in unique_ref_keys:
            ref_shas[key] = cached.get(key) or fetched.get(key)

        api_errors = sum(1 for k in to_fetch if k not in fetched)

        stale = []
        for comp in candidates:
            key = '{}/{}/{}'.format(comp['_owner'], comp['_repo'], comp['branch'])
            head = ref_shas.get(key)
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
                'nudges': comp.get('nudges', []),
            })

        if diagnose and stale:
            stale = self._diagnose_stale(kc, stale, all_comps)

        stale.sort(key=lambda s: s['component'])
        result = {
            'application': app_name,
            'stale': stale,
            'stale_count': len(stale),
            'checked': len(candidates),
            'skipped': skipped,
            'unique_refs': len(unique_ref_keys),
            'api_calls': len(to_fetch),
        }
        if cache:
            result['cache'] = cache.stats()
        if api_errors:
            result['api_errors'] = api_errors
            result['warning'] = '{} of {} refs failed GitHub API lookup (rate limit?)'.format(
                api_errors, len(to_fetch))
        return result

    def _diagnose_stale(self, kc, stale_list, all_comps):
        """Run trigger diagnosis on each stale component."""
        from proactive.trigger_diagnosis import diagnose_stale_trigger

        try:
            pac_repos = kc.list_pac_repositories()
        except Exception as exc:
            logger.debug("Failed to fetch PaC repositories: %s", exc)
            pac_repos = []

        all_names = {c['name'] for c in all_comps}

        for entry in stale_list:
            recent_runs = []
            try:
                recent_runs = kc.list_recent_pipelineruns(entry['component'])
            except Exception as exc:
                logger.debug("Failed to fetch PipelineRuns for %s: %s",
                             entry['component'], exc)

            diagnosis = diagnose_stale_trigger(
                component_name=entry['component'],
                repository_url=entry.get('repository_url', ''),
                pac_repositories=pac_repos,
                nudge_refs=entry.get('nudges', []),
                all_component_names=all_names,
                recent_pipelineruns=recent_runs,
                head_commit=entry.get('head_commit_full', ''),
            )
            entry['diagnosis'] = {
                'cause': diagnosis.cause,
                'severity': diagnosis.severity,
                'detail': diagnosis.detail,
            }

        return stale_list

    def get_stale_warnings(self, application=None):
        """Generate HealthWarning entries for stale components."""
        try:
            result = self.get_stale_components(application, diagnose=False)
        except Exception as exc:
            logger.debug("Stale check skipped: %s", exc)
            return []

        warnings = []
        app_name = result.get('application', '')
        for entry in result.get('stale', []):
            warnings.append(HealthWarning(
                component_name=entry['component'],
                application=app_name,
                signal_type='commit_staleness',
                severity='warning',
                message='Branch HEAD {} is ahead of built commit {}'.format(
                    entry['head_commit'], entry['built_commit']),
                evidence={
                    'branch': entry.get('branch', ''),
                    'head_commit': entry.get('head_commit', ''),
                    'built_commit': entry.get('built_commit', ''),
                },
            ))
        return warnings

    def check_snapshot_freshness(self, application=None):
        """Compare snapshot images against latest builds to detect stale images.

        For each component in the latest Snapshot, checks whether the image
        matches the most recent successful build. Flags components where:
        - The latest build failed (snapshot has an old pre-failure image)
        - A newer successful build exists (snapshot wasn't regenerated)
        - No builds are found (component may be misconfigured)
        """
        from clients.tekton_results import TektonResultsClient

        namespace = os.environ.get('NAMESPACE', '')
        app_name = application or os.environ.get('APPLICATION_NAME', '')

        if not namespace:
            return {'application': app_name, 'error': 'NAMESPACE not set'}

        from clients.konflux_client import KonfluxClient
        kfx = KonfluxClient(namespace=namespace)

        snapshots = kfx.get_snapshots(app_filter=app_name, limit=1)
        if not snapshots:
            return {'application': app_name, 'error': 'No snapshots found'}

        snap = snapshots[0]
        snap_name = snap.get('metadata', {}).get('name', '')
        snap_created = snap.get('metadata', {}).get('creationTimestamp', '')
        snap_components = {}
        for c in snap.get('spec', {}).get('components', []):
            name = c.get('name', '')
            image = c.get('containerImage', '')
            if name and image:
                snap_components[name] = image

        tr = TektonResultsClient(namespace=namespace)
        recent_builds = tr.query_pipelinerun_records(app_name, page_size=500)

        latest_by_comp = {}
        for pr in recent_builds:
            labels = pr.get('metadata', {}).get('labels', {})
            comp = labels.get('appstudio.openshift.io/component', '')
            event_type = labels.get('pipelinesascode.tekton.dev/event-type', '')
            if not comp or (event_type and event_type not in ('push', 'incoming')):
                continue

            created = pr.get('metadata', {}).get('creationTimestamp', '')
            prev = latest_by_comp.get(comp)
            if prev and prev['created'] >= created:
                continue

            conditions = pr.get('status', {}).get('conditions', [])
            succeeded = bool(conditions and conditions[-1].get('status') == 'True')
            reason = conditions[-1].get('reason', '') if conditions else ''

            pr_results = {}
            for r in pr.get('status', {}).get('results', []):
                pr_results[r.get('name', '')] = r.get('value', '')

            image_url = pr_results.get('IMAGE_URL', '')
            image_digest = pr_results.get('IMAGE_DIGEST', '')
            output_image = '{}@{}'.format(image_url, image_digest) if image_url and image_digest else ''

            latest_by_comp[comp] = {
                'created': created,
                'succeeded': succeeded,
                'pr_name': pr.get('metadata', {}).get('name', ''),
                'output_image': output_image,
                'reason': reason,
            }

        fresh = []
        stale = []
        no_builds = []

        def _digest(image_url):
            if '@sha256:' in image_url:
                return image_url.split('@')[-1]
            return ''

        for comp_name, snap_image in sorted(snap_components.items()):
            latest = latest_by_comp.get(comp_name)
            if not latest:
                no_builds.append({
                    'component': comp_name,
                    'snapshot_image': snap_image,
                })
                continue

            if not latest['succeeded']:
                stale.append({
                    'component': comp_name,
                    'snapshot_image': snap_image,
                    'reason': 'latest_build_failed',
                    'latest_build_date': latest['created'],
                    'latest_build_pr': latest['pr_name'],
                    'latest_build_reason': latest['reason'],
                })
            elif latest['output_image']:
                snap_digest = _digest(snap_image)
                build_digest = _digest(latest['output_image'])
                if snap_digest and build_digest and snap_digest != build_digest:
                    stale.append({
                        'component': comp_name,
                        'snapshot_image': snap_image,
                        'reason': 'newer_build_available',
                        'latest_build_date': latest['created'],
                        'latest_build_pr': latest['pr_name'],
                        'latest_build_image': latest['output_image'],
                    })
                else:
                    fresh.append(comp_name)
            else:
                fresh.append(comp_name)

        return {
            'application': app_name,
            'snapshot': snap_name,
            'snapshot_created': snap_created,
            'total_components': len(snap_components),
            'fresh_count': len(fresh),
            'stale': stale,
            'no_builds': no_builds,
            'stale_count': len(stale),
            'no_builds_count': len(no_builds),
        }
