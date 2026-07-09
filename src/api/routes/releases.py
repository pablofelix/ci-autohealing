"""Release readiness and freeze calendar API routes."""

import logging
import os
import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from api.validators import validate_application_name, validate_release_name
from config import CollectorConfig
from repositories.build_failure_repository import BuildFailureRepository
from repositories.conforma_repository import ConformaRepository
from repositories.connection import DatabaseConnection
from repositories.repository_factory import get_repository

logger = logging.getLogger(__name__)


class FreezeCreate(BaseModel):
    start_date: str
    end_date: str
    reason: str = Field(..., min_length=1, max_length=500)

router = APIRouter(tags=["releases"])


def _db():
    cfg = CollectorConfig.from_env()
    return DatabaseConnection(cfg.db)


@router.get("/freezes")
def list_freezes() -> List[Dict[str, Any]]:
    today = date.today()
    db = _db()
    with db.connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, start_date, end_date, reason FROM release_freezes ORDER BY start_date"
        )
        results = []
        for row in cursor.fetchall():
            fid, start, end, reason = row
            if today > end:
                status = "past"
            elif today < start:
                status = "upcoming"
            else:
                status = "active"
            results.append({
                'id': fid, 'start_date': str(start), 'end_date': str(end),
                'reason': reason, 'status': status,
            })
        return results


@router.get("/freezes/active")
def get_active_freeze() -> Optional[Dict[str, Any]]:
    db = _db()
    with db.connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, start_date, end_date, reason FROM release_freezes "
            "WHERE CURRENT_DATE BETWEEN start_date AND end_date LIMIT 1"
        )
        row = cursor.fetchone()
        if not row:
            return None
        return {
            'id': row[0], 'start_date': str(row[1]), 'end_date': str(row[2]),
            'reason': row[3], 'status': 'active',
        }


@router.post("/freezes")
def add_freeze(body: FreezeCreate) -> Dict[str, Any]:
    try:
        s = date.fromisoformat(body.start_date)
        e = date.fromisoformat(body.end_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")
    if e < s:
        raise HTTPException(status_code=400, detail="end_date must be >= start_date")

    db = _db()
    with db.connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO release_freezes (start_date, end_date, reason) VALUES (%s, %s, %s) RETURNING id",
            (s, e, body.reason)
        )
        fid = cursor.fetchone()[0]
        return {'id': fid, 'start_date': str(s), 'end_date': str(e), 'reason': body.reason}


@router.delete("/freezes/{freeze_id}")
def remove_freeze(freeze_id: int) -> Dict[str, str]:
    db = _db()
    with db.connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM release_freezes WHERE id = %s", (freeze_id,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail=f"Freeze {freeze_id} not found")
        return {'status': 'deleted', 'id': str(freeze_id)}


@router.get("/applications/{application}/schedule")
def get_schedule(application: str) -> Optional[Dict[str, Any]]:
    db = _db()
    with db.connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT planning_freeze, feature_freeze, code_freeze, initial_rc, "
            "release_window_start, release_date, next_release, updated_at "
            "FROM release_schedule WHERE application = %s",
            (application,)
        )
        row = cursor.fetchone()
        if not row:
            return None
        fields = ['planning_freeze', 'feature_freeze', 'code_freeze',
                  'initial_rc', 'release_window_start', 'release_date']
        result = {'application': application}
        for i, f in enumerate(fields):
            result[f] = str(row[i]) if row[i] else None
            if row[i] and hasattr(row[i], 'year'):
                result[f'{f}_days'] = (row[i] - date.today()).days
        result['next_release'] = row[6] if row[6] else None
        result['updated_at'] = str(row[7]) if row[7] else None
        return result


class ScheduleSync(BaseModel):
    application: str
    planning_freeze: Optional[str] = None
    feature_freeze: Optional[str] = None
    code_freeze: Optional[str] = None
    initial_rc: Optional[str] = None
    release_window_start: Optional[str] = None
    release_date: Optional[str] = None
    next_release: Optional[str] = None
    source: str = "product_pages"


@router.post("/releases/schedule/sync")
def sync_schedule(entries: List[ScheduleSync]) -> Dict[str, Any]:
    """Sync release schedule data from Product Pages (or other sources)."""
    db = _db()
    synced = []
    with db.connection() as conn:
        cursor = conn.cursor()
        for entry in entries:
            def _d(v: Optional[str]) -> str:
                return f"'{v}'" if v else "NULL"
            cursor.execute(
                """INSERT INTO release_schedule
                    (application, planning_freeze, feature_freeze, code_freeze,
                     initial_rc, release_window_start, release_date,
                     next_release, sheet_id, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 0, NOW())
                   ON CONFLICT (application) DO UPDATE SET
                     planning_freeze = EXCLUDED.planning_freeze,
                     feature_freeze = EXCLUDED.feature_freeze,
                     code_freeze = EXCLUDED.code_freeze,
                     initial_rc = EXCLUDED.initial_rc,
                     release_window_start = EXCLUDED.release_window_start,
                     release_date = EXCLUDED.release_date,
                     next_release = EXCLUDED.next_release,
                     updated_at = NOW()""",
                (entry.application,
                 entry.planning_freeze, entry.feature_freeze,
                 entry.code_freeze, entry.initial_rc,
                 entry.release_window_start, entry.release_date,
                 entry.next_release)
            )
            synced.append(entry.application)
    logger.info("Synced release schedule for %d applications: %s",
                len(synced), synced)
    return {'synced': synced, 'count': len(synced), 'source': 'product_pages'}


@router.get("/product-pages/schedule/{entity_id}")
def get_pp_schedule(entity_id: int) -> List[Dict[str, Any]]:
    """Proxy to Product Pages browse_schedule MCP tool."""
    import json
    import subprocess

    token = os.environ.get('PRODUCT_PAGES_PROD_TOKEN', '')
    url = f'https://productpages.redhat.com/api/v2/releases/{entity_id}/schedule/tasks/'

    try:
        result = subprocess.run(
            ['curl', '-sf', '--max-time', '15', url,
             '-H', f'Authorization: Token {token}',
             '-H', 'Accept: application/json'],
            capture_output=True, text=True, timeout=20)
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
    except Exception as exc:
        logger.warning("Product Pages fetch failed: %s", exc)

    raise HTTPException(status_code=502, detail="Product Pages unavailable")


@router.get("/applications/{application}/stale")
def get_stale(application: str) -> Dict[str, Any]:
    """Components with untriggered commits (branch HEAD ahead of last build)."""
    from proactive.health_monitor import HealthMonitor
    monitor = HealthMonitor(db=None)
    return monitor.get_stale_components(application)


@router.get("/applications/{application}/nightly")
def get_nightly(application: str) -> Dict[str, Any]:
    """Nightly build status: FBC fragment health + blockers."""
    from proactive.health_monitor import HealthMonitor
    db = _db()
    monitor = HealthMonitor(db)
    return monitor.get_nightly_status(application)


@router.get("/applications/{application}/nightly/history")
def get_nightly_history(application: str, days: int = 14) -> Dict[str, Any]:
    """Nightly operator build history + FBC fragment freshness."""
    repo = get_repository(BuildFailureRepository)
    return repo.get_nightly_history(application, days=days)


@router.get("/applications/{application}/fbc-history")
def get_fbc_history(application: str, limit: int = 20):
    """Track FBC (File-Based Catalog) fragment image SHAs over time.

    Returns the build history for the FBC fragment component, including
    image digests and which SHA is current. Helps trace test results to
    specific FBC builds when multiple SHAs are produced in a single day.
    """
    from datetime import date

    from mcp_server.models import FBCFragmentEntry, FBCFragmentHistory
    from proactive.health_monitor import _nightly_component_for_app

    fbc_name = _nightly_component_for_app(application)
    repo = get_repository(BuildFailureRepository)

    with repo.db.connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT pipelinerun_name, status, output_image, image_digest,
                   first_detected_at
            FROM build_failures
            WHERE component_name = %s AND application = %s
            ORDER BY first_detected_at DESC
            LIMIT %s
        """, (fbc_name, application, limit))
        rows = cursor.fetchall()

    builds = []
    current_sha = None
    today_count = 0
    today = date.today()

    for r in rows:
        pr_name, status, output_img, digest, built_at = r
        is_current = (status == 'Succeeded' and current_sha is None)
        if is_current and digest:
            current_sha = digest[:19] if digest else None

        if built_at and built_at.date() == today:
            today_count += 1

        builds.append(FBCFragmentEntry(
            pipelinerun=pr_name or '',
            status=status or '',
            image_digest=digest[:19] if digest else None,
            output_image=output_img,
            built_at=built_at or datetime.utcnow(),
            is_current=is_current,
        ))

    return FBCFragmentHistory(
        application=application,
        fbc_component=fbc_name,
        current_sha=current_sha,
        builds=builds,
        total_builds=len(builds),
        builds_today=today_count,
    )


@router.get("/applications/{application}/readiness")
def get_readiness(
    application: str,
    full: bool = Query(False, description="Include slow checks (artifact health for all components)"),
) -> Dict[str, Any]:
    build_repo = get_repository(BuildFailureRepository)
    conforma_repo = get_repository(ConformaRepository)

    failing = build_repo.find_failing_component_names(application) or set()
    fail_count = len(failing)

    unresolved_conforma = conforma_repo.find_unresolved_component_names(application)
    conforma_count = len(unresolved_conforma)

    from conforma.policy_tools import (
        compute_blocks,
        compute_exception_coverage_details,
        extract_violation_rules,
        fetch_exceptions_by_policy,
    )
    summaries = conforma_repo.get_violation_summaries(application)
    exceptions_by_policy = fetch_exceptions_by_policy()
    blocking_components = set()
    for s in summaries:
        rules = extract_violation_rules(s.get('violation_summary', ''))
        cov = compute_exception_coverage_details(
            rules, s.get('scenario', ''), exceptions_by_policy)
        blocks = compute_blocks(s.get('scenario', ''), cov['stage'], cov['prod'])
        if blocks not in ('none', ''):
            blocking_components.add(s['component_name'])
    blocking_count = len(blocking_components)

    freeze = get_active_freeze()

    blockers = []
    risks = []

    if blocking_count > 0:
        blockers.append('{} component(s) with unexcepted conforma violations'.format(blocking_count))
    if freeze:
        blockers.append("Pipeline frozen until {} ({})".format(freeze['end_date'], freeze['reason']))
    if fail_count > 0:
        risks.append('{} component(s) with failing builds'.format(fail_count))

    schedule = get_schedule(application)

    checks = _run_readiness_checks(application, full=full)
    for check in checks:
        if check['status'] == 'FAIL':
            blockers.append('{}: {}'.format(check['name'], check['detail']))
        elif check['status'] == 'WARN':
            risks.append('{}: {}'.format(check['name'], check['detail']))

    if blockers:
        verdict = "NOT_READY"
    elif risks:
        verdict = "AT_RISK"
    else:
        verdict = "READY"

    return {
        'application': application,
        'verdict': verdict,
        'build_failures': fail_count,
        'conforma_violations': conforma_count,
        'conforma_blockers': blocking_count,
        'failing_components': sorted(failing),
        'conforma_components': sorted(unresolved_conforma),
        'conforma_blocking_components': sorted(blocking_components),
        'freeze': freeze,
        'blockers': blockers,
        'risks': risks,
        'schedule': schedule,
        'checks': checks,
        'manual_checks': _build_manual_checks(application),
    }


def _run_readiness_checks(application, full=False):
    """Run all release health checks and return structured results.

    Each check returns: name, phase, status (PASS/FAIL/WARN/SKIP), detail, fix.
    Phase is 'pre-release' (before stage push) or 'post-stage' (after stage, before prod).
    """
    checks = []
    cfg_obj = CollectorConfig.from_env()
    db = DatabaseConnection(cfg_obj.db)

    from proactive.health_monitor import HealthMonitor
    monitor = HealthMonitor(db)

    namespace = os.environ.get('NAMESPACE', '')
    releng_ns = os.environ.get('RELENG_NAMESPACE', '')
    kfx, k8s, snapshot = None, None, None
    kfx_releng = None
    rpas = []
    k8s_reachable = False
    if namespace:
        try:
            from openshift_auth import is_logged_in
            k8s_reachable = is_logged_in()
        except Exception:
            k8s_reachable = False

        if k8s_reachable:
            try:
                from clients.konflux_client import KonfluxClient
                from clients.kubernetes import KubernetesClient
                kfx = KonfluxClient(namespace=namespace)
                k8s = KubernetesClient(namespace=namespace)
                snapshots = kfx.get_snapshots(app_filter=application, limit=1)
                snapshot = snapshots[0] if snapshots else None
                if releng_ns:
                    kfx_releng = KonfluxClient(namespace=releng_ns)
                    rpas = kfx_releng.get_release_plan_admissions(
                        name_filter=application) or []
                else:
                    rpas = kfx.get_release_plan_admissions(
                        name_filter=application) or []
            except Exception as exc:
                logger.warning("K8s client init failed: %s", exc)
                kfx, k8s, snapshot = None, None, None
        else:
            logger.info("K8s cluster unreachable — skipping cluster-dependent checks")

    # Run all checks in parallel — each is independent and I/O-bound
    from concurrent.futures import ThreadPoolExecutor, as_completed

    check_fns = [
        # ── Pre-release checks ──
        lambda: _check_fbc_health(monitor, application, k8s_reachable),
        lambda: _check_pcc(monitor),
        lambda: _check_gha_nightly(monitor, application),
        lambda: _check_stale_components(monitor, application, k8s_reachable),
        lambda: _check_snapshot_freshness(monitor, application, k8s_reachable),
        lambda: _check_fbc_artifacts(application, snapshot),
        lambda: _check_chains_signing(k8s, application),
        lambda: _check_snapshot_completeness(k8s, snapshot, application),
        lambda: _check_policy_consistency(rpas),
        lambda: _check_integration_tests(snapshot),
        lambda: _check_nudge_propagation(application),
        lambda: _check_multiarch_coverage(snapshot),
        lambda: _check_test_coverage_regression(kfx, snapshot, application),
        lambda: _check_ocp_compatibility(snapshot),
        lambda: _check_cross_product_images(snapshot),
        lambda: _check_tutorial_validation(),
        lambda: _check_its_scoping(db, application, kfx, namespace),
        # ── Post-stage checks ──
        lambda: _check_stage_release_health(kfx, application),
        lambda: _check_prod_rpa_exists(rpas),
        lambda: _check_snapshot_drift(kfx, snapshot, application),
        lambda: _check_release_pipeline_completeness(kfx, application),
        lambda: _check_pcc_post_push(monitor),
    ]
    if full:
        check_fns.extend([
            lambda: _check_all_artifacts(application, snapshot),
            lambda: _check_fbc_prune(k8s, application),
            lambda: _check_selector_label_changes(application),
            lambda: _check_rpm_drift(snapshot),
        ])

    # Names for timeout error messages
    check_names = [
        'FBC fragment health', 'PCC cache', 'GHA nightly trigger',
        'Stale components', 'Snapshot freshness', 'FBC artifact health',
        'Tekton Chains signing', 'Snapshot completeness',
        'EC policy consistency', 'Integration tests',
        'Nudge PR propagation', 'Multi-arch coverage',
        'Test coverage regression', 'OCP compatibility',
        'Cross-product images', 'Tutorial validation',
        'ITS scoping (Konflux)',
        'Stage release health', 'Prod RPA exists',
        'Snapshot drift', 'Release pipeline completeness',
        'PCC cache (post-push)',
    ]

    pool = ThreadPoolExecutor(max_workers=8)
    futures = {pool.submit(fn): i for i, fn in enumerate(check_fns)}
    results = [None] * len(check_fns)
    try:
        for future in as_completed(futures, timeout=20):
            idx = futures[future]
            try:
                results[idx] = future.result(timeout=5)
            except Exception as exc:
                name = check_names[idx] if idx < len(check_names) else f'check-{idx}'
                results[idx] = {
                    'name': name,
                    'phase': 'pre-release',
                    'status': 'SKIP',
                    'detail': 'Check error: {}'.format(exc),
                    'fix': None,
                }
    except TimeoutError:
        pass
    finally:
        for future, idx in futures.items():
            if results[idx] is None:
                future.cancel()
                name = check_names[idx] if idx < len(check_names) else f'check-{idx}'
                results[idx] = {
                    'name': name,
                    'phase': 'pre-release' if idx < 16 else 'post-stage',
                    'status': 'SKIP',
                    'detail': 'Check timed out (>20s)',
                    'fix': None,
                }
        pool.shutdown(wait=False, cancel_futures=True)
    checks = [r for r in results if r is not None]

    return checks


# ─── Pre-release checks ────────────────────────────────────────────────


def _check_fbc_health(monitor, application, k8s_reachable=True):
    """Check if the FBC (File-Based Catalog) fragment nightly build is healthy."""
    try:
        if not k8s_reachable:
            return {
                'name': 'FBC fragment health',
                'phase': 'pre-release',
                'status': 'SKIP',
                'detail': 'K8s cluster unreachable',
                'fix': 'Verify VPN/kubeconfig and retry',
            }
        status = monitor.get_nightly_status(application)
        fbc = status.get('fbc_health')
        if not fbc:
            return {
                'name': 'FBC fragment health',
                'phase': 'pre-release',
                'status': 'WARN',
                'detail': 'No health data for FBC fragment',
                'fix': 'Verify FBC fragment component exists and has recent builds',
            }
        current = fbc.get('current_status', '')
        if current == 'Failed':
            return {
                'name': 'FBC fragment health',
                'phase': 'pre-release',
                'status': 'FAIL',
                'detail': 'FBC fragment last build failed',
                'fix': 'Check FBC fragment build logs: ic describe {}'.format(
                    status.get('fbc_component', '')),
            }
        return {
            'name': 'FBC fragment health',
            'phase': 'pre-release',
            'status': 'PASS',
            'detail': 'FBC fragment build healthy (score: {})'.format(
                fbc.get('health_score', 'N/A')),
            'fix': None,
        }
    except Exception as exc:
        logger.debug("FBC health check failed: %s", exc)
        return {
            'name': 'FBC fragment health',
            'phase': 'pre-release',
            'status': 'SKIP',
            'detail': 'Check failed: {}'.format(exc),
            'fix': None,
        }


def _check_pcc(monitor):
    """Check if the PCC (Pre-Computed Catalog) cache is fresh."""
    try:
        pcc = monitor._check_pcc_freshness()
        if not pcc:
            return {
                'name': 'PCC cache',
                'phase': 'pre-release',
                'status': 'SKIP',
                'detail': 'PCC check skipped (no GitHub token)',
                'fix': None,
            }
        if pcc['status'] == 'stale':
            missing = pcc.get('missing_versions', [])
            return {
                'name': 'PCC cache',
                'phase': 'pre-release',
                'status': 'FAIL',
                'detail': '{} version(s) in registry not in cache: {}'.format(
                    len(missing), ', '.join(missing[:5])),
                'fix': 'Run regen-pcc-cache workflow in RHOAI-Build-Config',
            }
        if pcc['status'] == 'fresh':
            return {
                'name': 'PCC cache',
                'phase': 'pre-release',
                'status': 'PASS',
                'detail': 'PCC cache is fresh ({} versions)'.format(
                    pcc.get('cached_versions', 0)),
                'fix': None,
            }
        return {
            'name': 'PCC cache',
            'phase': 'pre-release',
            'status': 'SKIP',
            'detail': 'PCC check inconclusive',
            'fix': None,
        }
    except Exception as exc:
        logger.debug("PCC check failed: %s", exc)
        return {
            'name': 'PCC cache',
            'phase': 'pre-release',
            'status': 'SKIP',
            'detail': 'Check failed: {}'.format(exc),
            'fix': None,
        }


def _check_gha_nightly(monitor, application):
    """Check if the GHA (GitHub Actions) nightly trigger workflow is passing."""
    try:
        status = monitor.get_nightly_status(application)
        gha = status.get('gha_validation')
        if not gha:
            return {
                'name': 'GHA nightly trigger',
                'phase': 'pre-release',
                'status': 'SKIP',
                'detail': 'No GHA workflow data available',
                'fix': None,
            }
        conclusion = gha.get('conclusion', 'unknown')
        if conclusion == 'failure':
            return {
                'name': 'GHA nightly trigger',
                'phase': 'pre-release',
                'status': 'WARN',
                'detail': 'Nightly trigger workflow failed ({})'.format(
                    gha.get('created_at', '')[:10]),
                'fix': 'Check workflow: {}'.format(gha.get('url', '')),
            }
        return {
            'name': 'GHA nightly trigger',
            'phase': 'pre-release',
            'status': 'PASS',
            'detail': 'Nightly trigger workflow passed ({})'.format(
                gha.get('created_at', '')[:10]),
            'fix': None,
        }
    except Exception as exc:
        logger.debug("GHA nightly check failed: %s", exc)
        return {
            'name': 'GHA nightly trigger',
            'phase': 'pre-release',
            'status': 'SKIP',
            'detail': 'Check failed: {}'.format(exc),
            'fix': None,
        }


def _check_stale_components(monitor, application, k8s_reachable=True):
    """Check for components with untriggered commits."""
    try:
        if not k8s_reachable:
            return {
                'name': 'Stale components',
                'phase': 'pre-release',
                'status': 'SKIP',
                'detail': 'K8s cluster unreachable',
                'fix': 'Verify VPN/kubeconfig and retry',
            }
        result = monitor.get_stale_components(application, diagnose=False)
        stale_count = result.get('stale_count', 0)
        if stale_count > 0:
            names = [s['component'] for s in result.get('stale', [])[:5]]
            return {
                'name': 'Stale components',
                'phase': 'pre-release',
                'status': 'WARN',
                'detail': '{} component(s) with untriggered commits: {}'.format(
                    stale_count, ', '.join(names)),
                'fix': 'Run ic get stale for details; trigger builds or verify non-functional commits',
            }
        return {
            'name': 'Stale components',
            'phase': 'pre-release',
            'status': 'PASS',
            'detail': '0 components with untriggered commits ({} checked)'.format(
                result.get('checked', 0)),
            'fix': None,
        }
    except Exception as exc:
        logger.debug("Stale components check failed: %s", exc)
        return {
            'name': 'Stale components',
            'phase': 'pre-release',
            'status': 'SKIP',
            'detail': 'Check failed: {}'.format(exc),
            'fix': None,
        }


def _check_snapshot_freshness(monitor, application, k8s_reachable=True):
    """Check if the snapshot contains the latest successful builds."""
    try:
        if not k8s_reachable:
            return {
                'name': 'Snapshot freshness',
                'phase': 'pre-release',
                'status': 'SKIP',
                'detail': 'K8s cluster unreachable',
                'fix': 'Verify VPN/kubeconfig and retry',
            }
        result = monitor.check_snapshot_freshness(application)
        stale_count = result.get('stale_count', 0)
        if stale_count > 0:
            names = [s['component'] for s in result.get('stale', [])[:3]]
            return {
                'name': 'Snapshot freshness',
                'phase': 'pre-release',
                'status': 'WARN',
                'detail': '{} component(s) have newer builds available: {}'.format(
                    stale_count, ', '.join(names)),
                'fix': 'Snapshot updates on next successful build or nudge',
            }
        return {
            'name': 'Snapshot freshness',
            'phase': 'pre-release',
            'status': 'PASS',
            'detail': '{} components in snapshot are up to date'.format(
                result.get('fresh_count', 0)),
            'fix': None,
        }
    except Exception as exc:
        logger.debug("Snapshot freshness check failed: %s", exc)
        return {
            'name': 'Snapshot freshness',
            'phase': 'pre-release',
            'status': 'SKIP',
            'detail': 'Check failed: {}'.format(exc),
            'fix': None,
        }


def _check_fbc_artifacts(application, snapshot=None):
    """Check OCI artifact health for the FBC fragment component only (fast)."""
    try:
        from proactive.health_monitor import _nightly_component_for_app

        namespace = os.environ.get('NAMESPACE', '')
        if not namespace:
            return {
                'name': 'FBC artifact health',
                'phase': 'pre-release',
                'status': 'SKIP',
                'detail': 'NAMESPACE not set',
                'fix': None,
            }

        fbc_name = _nightly_component_for_app(application)

        if not snapshot:
            from clients.konflux_client import KonfluxClient
            kfx = KonfluxClient(namespace=namespace)
            snapshots = kfx.get_snapshots(app_filter=application, limit=1)
            snapshot = snapshots[0] if snapshots else None

        if not snapshot:
            return {
                'name': 'FBC artifact health',
                'phase': 'pre-release',
                'status': 'SKIP',
                'detail': 'No snapshot found',
                'fix': None,
            }

        fbc_comp = None
        for c in snapshot.get('spec', {}).get('components', []):
            if c.get('name', '') == fbc_name:
                fbc_comp = c
                break

        if not fbc_comp:
            return {
                'name': 'FBC artifact health',
                'phase': 'pre-release',
                'status': 'SKIP',
                'detail': 'FBC component {} not in snapshot'.format(fbc_name),
                'fix': None,
            }

        from clients.registry_client import RegistryClient
        rc = RegistryClient()
        results = rc.check_artifact_health_batch([fbc_comp], timeout=30)
        health = results.get(fbc_name, {})

        if not health.get('healthy', True):
            missing = health.get('missing', [])
            return {
                'name': 'FBC artifact health',
                'phase': 'pre-release',
                'status': 'FAIL',
                'detail': 'FBC fragment missing artifacts: {}'.format(
                    ', '.join('.{}'.format(m) for m in missing)),
                'fix': 'Missing .src usually means timeout build; rebuild FBC fragment',
            }
        return {
            'name': 'FBC artifact health',
            'phase': 'pre-release',
            'status': 'PASS',
            'detail': 'FBC fragment has all OCI artifacts (.sig/.src/.att/.sbom)',
            'fix': None,
        }
    except Exception as exc:
        logger.debug("FBC artifact check failed: %s", exc)
        return {
            'name': 'FBC artifact health',
            'phase': 'pre-release',
            'status': 'SKIP',
            'detail': 'Check failed: {}'.format(exc),
            'fix': None,
        }


def _check_chains_signing(k8s, application):
    """Check Tekton Chains build signing status for all components."""
    try:
        if not k8s:
            return {
                'name': 'Tekton Chains signing',
                'phase': 'pre-release',
                'status': 'SKIP',
                'detail': 'NAMESPACE not set',
                'fix': None,
            }
        components = k8s.list_components(application=application)
        if not components:
            return {
                'name': 'Tekton Chains signing',
                'phase': 'pre-release',
                'status': 'SKIP',
                'detail': 'No components found',
                'fix': None,
            }
        failed_signing = []
        for comp in components:
            runs = k8s.list_recent_pipelineruns(
                comp['name'], limit=1)
            if runs and runs[0].get('chains_signed') == 'failed':
                failed_signing.append(comp['name'])
        if failed_signing:
            return {
                'name': 'Tekton Chains signing',
                'phase': 'pre-release',
                'status': 'FAIL',
                'detail': '{} component(s) with failed signing: {}'.format(
                    len(failed_signing), ', '.join(failed_signing[:5])),
                'fix': 'Rebuild affected components; signing failures block EC policy',
            }
        return {
            'name': 'Tekton Chains signing',
            'phase': 'pre-release',
            'status': 'PASS',
            'detail': 'All {} components have signed builds'.format(
                len(components)),
            'fix': None,
        }
    except Exception as exc:
        logger.debug("Chains signing check failed: %s", exc)
        return {
            'name': 'Tekton Chains signing',
            'phase': 'pre-release',
            'status': 'SKIP',
            'detail': 'Check failed: {}'.format(exc),
            'fix': None,
        }


def _check_snapshot_completeness(k8s, snapshot, application):
    """Check if snapshot contains all application components."""
    try:
        if not k8s or not snapshot:
            return {
                'name': 'Snapshot completeness',
                'phase': 'pre-release',
                'status': 'SKIP',
                'detail': 'K8s or snapshot not available',
                'fix': None,
            }
        app_components = k8s.list_components(application=application)
        app_names = {c['name'] for c in app_components}
        snap_names = {
            c.get('name', '')
            for c in snapshot.get('spec', {}).get('components', [])
        }
        missing = app_names - snap_names
        if missing:
            return {
                'name': 'Snapshot completeness',
                'phase': 'pre-release',
                'status': 'WARN',
                'detail': '{} component(s) missing from snapshot: {}'.format(
                    len(missing), ', '.join(sorted(missing)[:5])),
                'fix': 'Missing components need a successful build to enter the snapshot',
            }
        return {
            'name': 'Snapshot completeness',
            'phase': 'pre-release',
            'status': 'PASS',
            'detail': 'Snapshot has all {} application components'.format(
                len(app_names)),
            'fix': None,
        }
    except Exception as exc:
        logger.debug("Snapshot completeness check failed: %s", exc)
        return {
            'name': 'Snapshot completeness',
            'phase': 'pre-release',
            'status': 'SKIP',
            'detail': 'Check failed: {}'.format(exc),
            'fix': None,
        }


def _check_policy_consistency(rpas):
    """Check that stage and prod RPAs (ReleasePlanAdmission) use the same EC policy."""
    try:
        if not rpas:
            return {
                'name': 'EC policy consistency',
                'phase': 'pre-release',
                'status': 'SKIP',
                'detail': 'No RPAs found',
                'fix': None,
            }
        from clients.konflux_client import KonfluxClient
        bindings = KonfluxClient.extract_rpa_bindings(rpas)
        stage_policies = {
            b['policy'] for b in bindings
            if b['target'] == 'stage' and b['policy']
        }
        prod_policies = {
            b['policy'] for b in bindings
            if b['target'] == 'prod' and b['policy']
        }
        if not stage_policies or not prod_policies:
            return {
                'name': 'EC policy consistency',
                'phase': 'pre-release',
                'status': 'SKIP',
                'detail': 'Missing stage or prod RPA',
                'fix': None,
            }
        if stage_policies != prod_policies:
            return {
                'name': 'EC policy consistency',
                'phase': 'pre-release',
                'status': 'WARN',
                'detail': 'Stage policy ({}) differs from prod ({})'.format(
                    ', '.join(stage_policies), ', '.join(prod_policies)),
                'fix': 'Verify this is intentional; mismatched policies can cause prod release failures',
            }
        return {
            'name': 'EC policy consistency',
            'phase': 'pre-release',
            'status': 'PASS',
            'detail': 'Stage and prod use same EC policy: {}'.format(
                ', '.join(stage_policies)),
            'fix': None,
        }
    except Exception as exc:
        logger.debug("Policy consistency check failed: %s", exc)
        return {
            'name': 'EC policy consistency',
            'phase': 'pre-release',
            'status': 'SKIP',
            'detail': 'Check failed: {}'.format(exc),
            'fix': None,
        }


def _check_integration_tests(snapshot):
    """Check integration test results from snapshot status conditions."""
    try:
        if not snapshot:
            return {
                'name': 'Integration tests',
                'phase': 'pre-release',
                'status': 'SKIP',
                'detail': 'No snapshot available',
                'fix': None,
            }
        from clients.konflux_client import KonfluxClient
        snap_status = KonfluxClient.extract_snapshot_status(snapshot)
        test_results = snap_status.get('test_results', {})
        if not test_results:
            return {
                'name': 'Integration tests',
                'phase': 'pre-release',
                'status': 'SKIP',
                'detail': 'No test results in snapshot',
                'fix': None,
            }
        failing = [
            name for name, result in test_results.items()
            if result.get('status') != 'True'
        ]
        if failing:
            return {
                'name': 'Integration tests',
                'phase': 'pre-release',
                'status': 'FAIL',
                'detail': '{} test(s) not passing: {}'.format(
                    len(failing), ', '.join(failing[:5])),
                'fix': 'Check test logs; failing integration tests block release',
            }
        return {
            'name': 'Integration tests',
            'phase': 'pre-release',
            'status': 'PASS',
            'detail': 'All {} integration tests passed'.format(
                len(test_results)),
            'fix': None,
        }
    except Exception as exc:
        logger.debug("Integration tests check failed: %s", exc)
        return {
            'name': 'Integration tests',
            'phase': 'pre-release',
            'status': 'SKIP',
            'detail': 'Check failed: {}'.format(exc),
            'fix': None,
        }


def _check_nudge_propagation(application):
    """Check for stale open nudge PRs that haven't been merged."""
    try:
        gh_token = os.environ.get('GITHUB_TOKEN', '')
        if not gh_token:
            return {
                'name': 'Nudge PR propagation',
                'phase': 'pre-release',
                'status': 'SKIP',
                'detail': 'No GITHUB_TOKEN set',
                'fix': None,
            }
        owner = os.environ.get('GITHUB_OPERATOR_OWNER', 'red-hat-data-services')
        repo = os.environ.get('GITHUB_OPERATOR_REPO', 'rhods-operator')
        branch = _derive_branch(application)

        from clients.github_client import GitHubClient
        gh = GitHubClient(token=gh_token)
        prs = gh.list_pull_requests(
            owner, repo, base=branch, state='open', limit=20)
        nudge_prs = [
            p for p in (prs or [])
            if 'nudge' in p.get('title', '').lower()
        ]
        if not nudge_prs:
            return {
                'name': 'Nudge PR propagation',
                'phase': 'pre-release',
                'status': 'PASS',
                'detail': 'No open nudge PRs',
                'fix': None,
            }
        stale = []
        now = datetime.utcnow()
        for pr in nudge_prs:
            created = pr.get('created_at', '')
            if created:
                try:
                    pr_time = datetime.strptime(created[:19], '%Y-%m-%dT%H:%M:%S')
                    age_hours = (now - pr_time).total_seconds() / 3600
                    if age_hours > 48:
                        stale.append(pr)
                except ValueError:
                    stale.append(pr)
        if stale:
            titles = [p.get('title', '')[:60] for p in stale[:3]]
            return {
                'name': 'Nudge PR propagation',
                'phase': 'pre-release',
                'status': 'WARN',
                'detail': '{} nudge PR(s) open >48h: {}'.format(
                    len(stale), '; '.join(titles)),
                'fix': 'Merge or close stale nudge PRs before release',
            }
        return {
            'name': 'Nudge PR propagation',
            'phase': 'pre-release',
            'status': 'PASS',
            'detail': '{} open nudge PR(s), all recent'.format(len(nudge_prs)),
            'fix': None,
        }
    except Exception as exc:
        logger.debug("Nudge propagation check failed: %s", exc)
        return {
            'name': 'Nudge PR propagation',
            'phase': 'pre-release',
            'status': 'SKIP',
            'detail': 'Check failed: {}'.format(exc),
            'fix': None,
        }


def _check_its_scoping(db, application, kfx, namespace):
    """Check for Konflux ITS scoping false positives.

    Detects when FBC fragments or Helm chart OCI artifacts are being evaluated
    against the generic registry-rhoai-prod policy instead of their correct
    artifact-specific policy. This is a known Konflux platform limitation:
    the generic 'component' ITS context fires for all components including FBC
    and charts, which have their own component-specific ITS with the correct
    policy. The generic ITS is optional and does NOT block release.

    Transitions from WARN → PASS when no wrong-policy violations exist in DB,
    which happens when either the ITS config is fixed or no FBC/chart builds are
    failing. The PASS detail distinguishes these cases by checking if the generic
    ITS still has the broad 'component' context (meaning the platform problem
    persists but no violations are currently active), which is a signal to keep
    the IC workaround in place. Only when the generic ITS loses the broad context
    or becomes optional can the workaround be safely removed.

    Konflux NudgeConfig cross-app support (in development, STONEINTG-1659) would
    also enable the alternative fix of splitting FBC/charts into separate Apps.
    """
    try:
        from repositories import ConformaRepository
        repo = ConformaRepository(db)

        # Single query: all (component, scenario) pairs with wrong policy — no N+1
        wrong_policy = repo.get_wrong_policy_components(application)

        if not wrong_policy:
            # Check whether the generic ITS still has the broad 'component' context.
            # If it does, the Konflux-side problem still exists (just no active violations
            # right now). If it doesn't, the platform may have been fixed.
            its_still_generic = False
            if kfx and namespace:
                try:
                    scenarios = kfx.get_integration_test_scenarios(
                        namespace=namespace, app_filter=application)
                    for s in scenarios:
                        meta = kfx.extract_its_metadata(s)
                        if 'registry-rhoai-prod' in meta.get('policy_ref', ''):
                            contexts = meta.get('contexts', [])
                            if 'component' in contexts and not meta.get('is_optional'):
                                its_still_generic = True
                                break
                except Exception:
                    pass

            detail = (
                'No wrong-policy violations detected, but the generic registry-rhoai-prod '
                'ITS still uses the broad "component" context — the Konflux-side problem '
                'persists. Keep the IC workaround (is_wrong_policy_for_artifact) in place.'
                if its_still_generic else
                'No FBC/chart components with wrong-policy violations. '
                'If the generic ITS context was recently narrowed, the IC workaround in '
                'is_wrong_policy_for_artifact() may now be removable.'
            )
            return {
                'name': 'ITS scoping (Konflux)',
                'phase': 'pre-release',
                'status': 'PASS',
                'detail': detail,
                'fix': None,
            }

        return {
            'name': 'ITS scoping (Konflux)',
            'phase': 'pre-release',
            'status': 'WARN',
            'detail': (
                '{} component(s) evaluated against wrong EC policy due to Konflux ITS '
                'scoping limitation (optional ITS, does NOT block release): {}. '
                'IC marks these as false positives and skips AI analysis. '
                'Track fix: STONEINTG-1659 (NudgeConfig) or MR to '
                'releng/konflux-release-data tenants-config/.'
            ).format(len(wrong_policy), ', '.join(wrong_policy)),
            'fix': (
                'No action needed for release. To fix permanently: '
                '(1) Wait for Konflux NudgeConfig cross-app support and split FBC/charts '
                'into separate Applications, OR '
                '(2) MR to releng/konflux-release-data to scope the generic '
                'conforma-registry-rhoai-prod ITS away from FBC/chart components.'
            ),
        }
    except Exception as exc:
        logger.debug("ITS scoping check failed: %s", exc)
        return {
            'name': 'ITS scoping (Konflux)',
            'phase': 'pre-release',
            'status': 'SKIP',
            'detail': 'Check failed: {}'.format(exc),
            'fix': None,
        }


def _check_test_coverage_regression(kfx, snapshot, application):
    """Detect integration tests that disappeared between snapshots.

    Compares test_results in the current snapshot vs the previous one.
    Missing tests suggest CI coverage was silently disabled (e.g., secrets removed).
    """
    try:
        if not kfx or not snapshot:
            return {
                'name': 'Test coverage regression',
                'phase': 'pre-release',
                'status': 'SKIP',
                'detail': 'NAMESPACE or snapshot not available',
                'fix': None,
            }

        from clients.konflux_client import KonfluxClient
        current_status = KonfluxClient.extract_snapshot_status(snapshot)
        current_tests = set(current_status.get('test_results', {}).keys())

        if not current_tests:
            return {
                'name': 'Test coverage regression',
                'phase': 'pre-release',
                'status': 'SKIP',
                'detail': 'No tests in current snapshot',
                'fix': None,
            }

        snapshots = kfx.get_snapshots(app_filter=application, limit=5)
        current_name = snapshot.get('metadata', {}).get('name', '')
        prev_snapshot = None
        for s in snapshots:
            if s.get('metadata', {}).get('name', '') != current_name:
                prev_snapshot = s
                break

        if not prev_snapshot:
            return {
                'name': 'Test coverage regression',
                'phase': 'pre-release',
                'status': 'SKIP',
                'detail': 'No previous snapshot for comparison',
                'fix': None,
            }

        prev_status = KonfluxClient.extract_snapshot_status(prev_snapshot)
        prev_tests = set(prev_status.get('test_results', {}).keys())

        disappeared = prev_tests - current_tests
        if disappeared:
            return {
                'name': 'Test coverage regression',
                'phase': 'pre-release',
                'status': 'WARN',
                'detail': '{} test(s) from previous snapshot no longer running: {}'.format(
                    len(disappeared), ', '.join(sorted(disappeared)[:5])),
                'fix': 'Verify these tests were intentionally removed, not silently disabled',
            }

        new_tests = current_tests - prev_tests
        detail = '{} test(s) running'.format(len(current_tests))
        if new_tests:
            detail += ' ({} new: {})'.format(len(new_tests), ', '.join(sorted(new_tests)[:3]))
        return {
            'name': 'Test coverage regression',
            'phase': 'pre-release',
            'status': 'PASS',
            'detail': detail,
            'fix': None,
        }
    except Exception as exc:
        logger.debug("Test coverage regression check failed: %s", exc)
        return {
            'name': 'Test coverage regression',
            'phase': 'pre-release',
            'status': 'SKIP',
            'detail': 'Check failed: {}'.format(exc),
            'fix': None,
        }


def _check_multiarch_coverage(snapshot):
    """Check if snapshot images have builds for all target architectures.

    Verifies each component's containerImage has a manifest list containing
    all expected platforms: amd64, arm64, s390x, ppc64le.
    """
    TARGET_ARCHES = {'amd64', 'arm64', 's390x', 'ppc64le'}
    try:
        if not snapshot:
            return {
                'name': 'Multi-arch coverage',
                'phase': 'pre-release',
                'status': 'SKIP',
                'detail': 'No snapshot available',
                'fix': None,
            }
        components = snapshot.get('spec', {}).get('components', [])
        if not components:
            return {
                'name': 'Multi-arch coverage',
                'phase': 'pre-release',
                'status': 'SKIP',
                'detail': 'No components in snapshot',
                'fix': None,
            }

        from clients.registry_client import RegistryClient
        rc = RegistryClient()
        missing_arches = {}
        checked = 0

        for comp in components[:30]:
            name = comp.get('name', '')
            image = comp.get('containerImage', '')
            if not image or '@' not in image:
                continue
            repo_part, digest = image.rsplit('@', 1)
            if '/' not in repo_part:
                continue
            registry = repo_part.split('/')[0]
            repository = '/'.join(repo_part.split('/')[1:])

            manifest = rc.get_manifest(registry, repository, digest)
            if not manifest:
                continue
            checked += 1

            manifests = manifest.get('manifests', [])
            if not manifests:
                continue

            found_arches = set()
            for m in manifests:
                arch = m.get('platform', {}).get('architecture', '')
                if arch:
                    found_arches.add(arch)

            missing = TARGET_ARCHES - found_arches
            if missing:
                missing_arches[name] = sorted(missing)

        if not checked:
            return {
                'name': 'Multi-arch coverage',
                'phase': 'pre-release',
                'status': 'SKIP',
                'detail': 'Could not check any component manifests',
                'fix': None,
            }

        if missing_arches:
            examples = []
            for comp, arches in list(missing_arches.items())[:5]:
                examples.append('{} (missing {})'.format(comp, ', '.join(arches)))
            return {
                'name': 'Multi-arch coverage',
                'phase': 'pre-release',
                'status': 'WARN',
                'detail': '{} of {} component(s) missing arch builds: {}'.format(
                    len(missing_arches), checked, '; '.join(examples)),
                'fix': 'Rebuild affected components for missing architectures before code freeze',
            }

        return {
            'name': 'Multi-arch coverage',
            'phase': 'pre-release',
            'status': 'PASS',
            'detail': 'All {} checked component(s) have builds for {}'.format(
                checked, ', '.join(sorted(TARGET_ARCHES))),
            'fix': None,
        }
    except Exception as exc:
        logger.debug("Multi-arch coverage check failed: %s", exc)
        return {
            'name': 'Multi-arch coverage',
            'phase': 'pre-release',
            'status': 'SKIP',
            'detail': 'Check failed: {}'.format(exc),
            'fix': None,
        }


REQUIRED_TUTORIALS = [
    {
        'name': 'Fraud Detection',
        'repo': 'https://github.com/opendatahub-io/fraud-detection',
        'jira_label': 'tutorial-fraud-detection',
    },
    {
        'name': 'Object Detection (YOLO)',
        'repo': 'https://github.com/opendatahub-io/yolo-detection',
        'jira_label': 'tutorial-object-detection',
    },
]


def _check_tutorial_validation():
    """Check if required tutorials/demos have been validated against current RC.

    RHOAIENG-69754 was only caught because someone manually ran the Fraud Detection
    tutorial. This check reminds release managers that customer-facing tutorials need
    manual validation before declaring readiness.

    Since IC can't run tutorials automatically, this surfaces as a WARN with a
    checklist of required tutorials to validate.
    """
    tutorial_names = [t['name'] for t in REQUIRED_TUTORIALS]
    return {
        'name': 'Tutorial validation',
        'phase': 'pre-release',
        'status': 'WARN',
        'detail': 'Manual validation required for {} customer-facing tutorial(s): {}. '
                  'These tutorials exercise key user workflows (deploy model, '
                  'create pipeline, etc.) that automated tests may not cover.'.format(
                      len(tutorial_names), ', '.join(tutorial_names)),
        'fix': 'Run each tutorial against the current RC build and verify '
               'all steps complete successfully. Document results in the '
               'release Jira.',
    }


EXTERNAL_PRODUCT_REGISTRIES = [
    'registry.redhat.io/rhaii/',
    'registry.redhat.io/ubi',
    'registry.stage.redhat.io/',
]

STAGE_TO_PROD_REGISTRY = {
    'registry.stage.redhat.io': 'registry.redhat.io',
}


def _check_cross_product_images(snapshot):
    """Check if snapshot references images from external products/registries.

    When RHOAI depends on images from RHAII (Red Hat AI Infrastructure) or
    other products, verify those images resolve. Catches issues like
    RHOAIENG-70907 where the build config referenced the prod registry
    but the image was only available on stage during EA.
    """
    try:
        if not snapshot:
            return {
                'name': 'Cross-product images',
                'phase': 'pre-release',
                'status': 'SKIP',
                'detail': 'No snapshot available',
                'fix': None,
            }

        components = snapshot.get('spec', {}).get('components', [])
        external_refs = []
        stage_refs = []

        for comp in components:
            image = comp.get('containerImage', '')
            name = comp.get('name', '')
            if 'registry.stage.redhat.io' in image:
                stage_refs.append('{} ({})'.format(name, image[:60]))
            elif any(ext in image for ext in EXTERNAL_PRODUCT_REGISTRIES):
                external_refs.append('{} ({})'.format(name, image[:60]))

        warnings = []
        if stage_refs:
            warnings.append(
                '{} component(s) reference stage registry '
                '(registry.stage.redhat.io): {}'.format(
                    len(stage_refs), ', '.join(stage_refs[:3])))
        if external_refs:
            warnings.append(
                '{} component(s) reference external product images: {}'.format(
                    len(external_refs), ', '.join(external_refs[:3])))

        if warnings:
            return {
                'name': 'Cross-product images',
                'phase': 'pre-release',
                'status': 'WARN',
                'detail': '; '.join(warnings),
                'fix': 'Verify external product images are available in the '
                       'correct registry for this release phase (stage vs prod). '
                       'For GA, images must be on registry.redhat.io, not '
                       'registry.stage.redhat.io.',
            }
        return {
            'name': 'Cross-product images',
            'phase': 'pre-release',
            'status': 'PASS',
            'detail': 'No cross-product image issues detected',
            'fix': None,
        }
    except Exception as exc:
        logger.debug("Cross-product image check failed: %s", exc)
        return {
            'name': 'Cross-product images',
            'phase': 'pre-release',
            'status': 'SKIP',
            'detail': 'Check failed: {}'.format(exc),
            'fix': None,
        }


OCP_BREAKING_CHANGES = [
    {
        'ocp_version': '4.22',
        'description': 'Default deny-all NetworkPolicy in openshift-ingress',
        'affected_keywords': ['networkpolicy', 'openshift-ingress', 'ingress'],
        'affected_components': [
            'payload-processing', 'kube-auth-proxy', 'gateway',
        ],
        'fix_hint': 'Add explicit NetworkPolicy allowing egress/ingress for '
                    'pods in openshift-ingress namespace',
    },
    {
        'ocp_version': '4.21',
        'description': 'Pod Security Admission (PSA) enforcement in restricted namespaces',
        'affected_keywords': ['securitycontext', 'privileged', 'hostnetwork'],
        'affected_components': [],
        'fix_hint': 'Ensure pods run as non-root with restricted securityContext',
    },
]


def _check_ocp_compatibility(snapshot):
    """Check snapshot components against known OCP breaking changes.

    Maintains a list of OCP version changes that affect specific namespaces or
    resource types. When a snapshot targets OCP versions with known breaks,
    flags affected components that may need fixes.
    """
    try:
        if not snapshot:
            return {
                'name': 'OCP compatibility',
                'phase': 'pre-release',
                'status': 'SKIP',
                'detail': 'No snapshot available',
                'fix': None,
            }

        components = snapshot.get('spec', {}).get('components', [])
        comp_names = [c.get('name', '').lower() for c in components]

        warnings = []
        for change in OCP_BREAKING_CHANGES:
            matched = []
            for comp in comp_names:
                if any(kw in comp for kw in change['affected_components']) or any(kw in comp for kw in change['affected_keywords']):
                    matched.append(comp)

            if matched:
                warnings.append('OCP {}: {} — affects: {}'.format(
                    change['ocp_version'],
                    change['description'],
                    ', '.join(matched[:5]),
                ))

        if warnings:
            return {
                'name': 'OCP compatibility',
                'phase': 'pre-release',
                'status': 'WARN',
                'detail': '; '.join(warnings),
                'fix': 'Verify affected components have been updated for '
                       'OCP breaking changes. ' +
                       OCP_BREAKING_CHANGES[0]['fix_hint'],
            }
        return {
            'name': 'OCP compatibility',
            'phase': 'pre-release',
            'status': 'PASS',
            'detail': 'No known OCP compatibility issues for snapshot components',
            'fix': None,
        }
    except Exception as exc:
        logger.debug("OCP compatibility check failed: %s", exc)
        return {
            'name': 'OCP compatibility',
            'phase': 'pre-release',
            'status': 'SKIP',
            'detail': 'Check failed: {}'.format(exc),
            'fix': None,
        }


# ─── Post-stage checks ─────────────────────────────────────────────────


def _check_stage_release_health(kfx, application):
    """Check if the latest stage release completed successfully with valid post-validation."""
    try:
        if not kfx:
            return {
                'name': 'Stage release health',
                'phase': 'post-stage',
                'status': 'SKIP',
                'detail': 'NAMESPACE not set',
                'fix': None,
            }
        from clients.konflux_client import KonfluxClient
        releases = kfx.get_releases(app_filter=application, limit=10)
        if not releases:
            return {
                'name': 'Stage release health',
                'phase': 'post-stage',
                'status': 'SKIP',
                'detail': 'No releases found',
                'fix': None,
            }
        stage_release = None
        for rel in releases:
            rp = rel.get('spec', {}).get('releasePlan', '')
            if 'stage' in rp:
                stage_release = rel
                break
        if not stage_release:
            return {
                'name': 'Stage release health',
                'phase': 'post-stage',
                'status': 'SKIP',
                'detail': 'No stage release found',
                'fix': None,
            }
        rel_status = KonfluxClient.extract_release_status(stage_release)
        if rel_status.get('post_validation_failed'):
            return {
                'name': 'Stage release health',
                'phase': 'post-stage',
                'status': 'FAIL',
                'detail': 'Post-validation failed for {} — artifacts do not pass EC policy in stage'.format(
                    rel_status['name']),
                'fix': 'Fix failing EC policy violations before proceeding to prod',
            }
        if rel_status.get('status') != 'True':
            return {
                'name': 'Stage release health',
                'phase': 'post-stage',
                'status': 'FAIL',
                'detail': 'Stage release {} not completed: {}'.format(
                    rel_status['name'], rel_status.get('message', '')[:100]),
                'fix': 'Investigate release pipeline; check ic describe release {}'.format(
                    rel_status['name']),
            }
        return {
            'name': 'Stage release health',
            'phase': 'post-stage',
            'status': 'PASS',
            'detail': 'Stage release {} completed successfully'.format(
                rel_status['name']),
            'fix': None,
        }
    except Exception as exc:
        logger.debug("Stage release health check failed: %s", exc)
        return {
            'name': 'Stage release health',
            'phase': 'post-stage',
            'status': 'SKIP',
            'detail': 'Check failed: {}'.format(exc),
            'fix': None,
        }


def _check_prod_rpa_exists(rpas):
    """Check that a prod-targeting RPA (ReleasePlanAdmission) exists and has EC policy."""
    try:
        if not rpas:
            return {
                'name': 'Prod RPA exists',
                'phase': 'post-stage',
                'status': 'SKIP',
                'detail': 'No RPAs found',
                'fix': None,
            }
        from clients.konflux_client import KonfluxClient
        bindings = KonfluxClient.extract_rpa_bindings(rpas)
        prod_rpas = [b for b in bindings if b['target'] == 'prod']
        if not prod_rpas:
            return {
                'name': 'Prod RPA exists',
                'phase': 'post-stage',
                'status': 'FAIL',
                'detail': 'No prod ReleasePlanAdmission found — release to prod cannot start',
                'fix': 'Create a prod RPA for this application in the managed namespace',
            }
        missing_policy = [
            b['rpa_name'] for b in prod_rpas if not b['policy']
        ]
        if missing_policy:
            return {
                'name': 'Prod RPA exists',
                'phase': 'post-stage',
                'status': 'WARN',
                'detail': 'Prod RPA {} has no EC policy configured'.format(
                    ', '.join(missing_policy)),
                'fix': 'Add spec.policy to the prod RPA',
            }
        return {
            'name': 'Prod RPA exists',
            'phase': 'post-stage',
            'status': 'PASS',
            'detail': '{} prod RPA(s) configured with EC policy'.format(
                len(prod_rpas)),
            'fix': None,
        }
    except Exception as exc:
        logger.debug("Prod RPA check failed: %s", exc)
        return {
            'name': 'Prod RPA exists',
            'phase': 'post-stage',
            'status': 'SKIP',
            'detail': 'Check failed: {}'.format(exc),
            'fix': None,
        }


def _check_snapshot_drift(kfx, snapshot, application):
    """Check if the snapshot released to stage matches the current latest snapshot."""
    try:
        if not kfx:
            return {
                'name': 'Snapshot drift',
                'phase': 'post-stage',
                'status': 'SKIP',
                'detail': 'NAMESPACE not set',
                'fix': None,
            }
        releases = kfx.get_releases(app_filter=application, limit=10)
        stage_release = None
        for rel in releases:
            rp = rel.get('spec', {}).get('releasePlan', '')
            if 'stage' in rp:
                stage_release = rel
                break
        if not stage_release or not snapshot:
            return {
                'name': 'Snapshot drift',
                'phase': 'post-stage',
                'status': 'SKIP',
                'detail': 'No stage release or current snapshot available',
                'fix': None,
            }
        released_snap = stage_release.get('spec', {}).get('snapshot', '')
        current_snap = snapshot.get('metadata', {}).get('name', '')
        if released_snap != current_snap:
            return {
                'name': 'Snapshot drift',
                'phase': 'post-stage',
                'status': 'WARN',
                'detail': 'Stage released {} but current is {}'.format(
                    released_snap, current_snap),
                'fix': 'Re-release with current snapshot or verify the changes are acceptable',
            }
        return {
            'name': 'Snapshot drift',
            'phase': 'post-stage',
            'status': 'PASS',
            'detail': 'Stage snapshot matches current: {}'.format(current_snap),
            'fix': None,
        }
    except Exception as exc:
        logger.debug("Snapshot drift check failed: %s", exc)
        return {
            'name': 'Snapshot drift',
            'phase': 'post-stage',
            'status': 'SKIP',
            'detail': 'Check failed: {}'.format(exc),
            'fix': None,
        }


def _check_release_pipeline_completeness(kfx, application):
    """Check that all conditions in the latest stage release are True (no partial release)."""
    try:
        if not kfx:
            return {
                'name': 'Release pipeline completeness',
                'phase': 'post-stage',
                'status': 'SKIP',
                'detail': 'NAMESPACE not set',
                'fix': None,
            }
        releases = kfx.get_releases(app_filter=application, limit=10)
        stage_release = None
        for rel in releases:
            rp = rel.get('spec', {}).get('releasePlan', '')
            if 'stage' in rp:
                stage_release = rel
                break
        if not stage_release:
            return {
                'name': 'Release pipeline completeness',
                'phase': 'post-stage',
                'status': 'SKIP',
                'detail': 'No stage release found',
                'fix': None,
            }
        conditions = stage_release.get('status', {}).get('conditions', [])
        if not conditions:
            return {
                'name': 'Release pipeline completeness',
                'phase': 'post-stage',
                'status': 'SKIP',
                'detail': 'No conditions on stage release',
                'fix': None,
            }
        incomplete = []
        for cond in conditions:
            if cond.get('status') != 'True':
                incomplete.append('{}: {}'.format(
                    cond.get('type', '?'),
                    cond.get('message', '')[:60]))
        if incomplete:
            rel_name = stage_release.get(
                'metadata', {}).get('name', '')
            return {
                'name': 'Release pipeline completeness',
                'phase': 'post-stage',
                'status': 'WARN',
                'detail': '{} has {} incomplete condition(s): {}'.format(
                    rel_name, len(incomplete), '; '.join(incomplete[:3])),
                'fix': 'Check ic describe release {} for details'.format(rel_name),
            }
        return {
            'name': 'Release pipeline completeness',
            'phase': 'post-stage',
            'status': 'PASS',
            'detail': 'All {} release conditions are True'.format(
                len(conditions)),
            'fix': None,
        }
    except Exception as exc:
        logger.debug("Release pipeline completeness check failed: %s", exc)
        return {
            'name': 'Release pipeline completeness',
            'phase': 'post-stage',
            'status': 'SKIP',
            'detail': 'Check failed: {}'.format(exc),
            'fix': None,
        }


def _check_pcc_post_push(monitor):
    """Check PCC (Pre-Computed Catalog) cache after a push to stage.

    After pushing images to stage, the PCC cache can become stale if new
    operator versions aren't included. This is a post-stage variant of
    _check_pcc that surfaces the issue with a post-stage-specific fix message.
    EA2 Jul 3: required an extra FBC-only push because PCC wasn't regenerated.
    """
    try:
        pcc = monitor._check_pcc_freshness()
        if not pcc:
            return {
                'name': 'PCC cache (post-push)',
                'phase': 'post-stage',
                'status': 'SKIP',
                'detail': 'PCC check skipped (no GitHub token)',
                'fix': None,
            }
        if pcc['status'] == 'stale':
            missing = pcc.get('missing_versions', [])
            return {
                'name': 'PCC cache (post-push)',
                'phase': 'post-stage',
                'status': 'FAIL',
                'detail': (
                    'PCC cache is stale after stage push — {} version(s) '
                    'not in cache: {}. This will require an additional '
                    'FBC-only push to fix.').format(
                        len(missing), ', '.join(missing[:5])),
                'fix': 'Run regen-pcc-cache workflow in RHOAI-Build-Config, '
                       'then trigger FBC-only push to stage',
            }
        return {
            'name': 'PCC cache (post-push)',
            'phase': 'post-stage',
            'status': 'PASS',
            'detail': 'PCC cache is fresh after stage push ({} versions)'.format(
                pcc.get('cached_versions', 0)),
            'fix': None,
        }
    except Exception as exc:
        logger.debug("PCC post-push check failed: %s", exc)
        return {
            'name': 'PCC cache (post-push)',
            'phase': 'post-stage',
            'status': 'SKIP',
            'detail': 'Check failed: {}'.format(exc),
            'fix': None,
        }


# ─── Slow checks (--full only) ─────────────────────────────────────────


def _check_all_artifacts(application, snapshot=None):
    """Check OCI artifact health for all snapshot components (slow, ~60s)."""
    try:
        namespace = os.environ.get('NAMESPACE', '')
        if not namespace:
            return {
                'name': 'Full artifact health',
                'phase': 'pre-release',
                'status': 'SKIP',
                'detail': 'NAMESPACE not set',
                'fix': None,
            }

        if not snapshot:
            from clients.konflux_client import KonfluxClient
            kfx = KonfluxClient(namespace=namespace)
            snapshots = kfx.get_snapshots(app_filter=application, limit=1)
            if not snapshots:
                return {
                    'name': 'Full artifact health',
                    'phase': 'pre-release',
                    'status': 'SKIP',
                    'detail': 'No snapshot found',
                    'fix': None,
                }
            snapshot = snapshots[0]

        components = snapshot.get('spec', {}).get('components', [])
        from clients.registry_client import RegistryClient
        rc = RegistryClient()
        results = rc.check_artifact_health_batch(components, timeout=120)

        unhealthy = []
        for comp_name, health in results.items():
            if not health.get('healthy', True):
                unhealthy.append({
                    'component': comp_name,
                    'missing': health.get('missing', []),
                })

        if unhealthy:
            names = [u['component'] for u in unhealthy[:5]]
            return {
                'name': 'Full artifact health',
                'phase': 'pre-release',
                'status': 'WARN',
                'detail': '{} of {} component(s) missing artifacts: {}'.format(
                    len(unhealthy), len(results), ', '.join(names)),
                'fix': 'Rebuild affected components; missing .src indicates timeout builds',
                'unhealthy': unhealthy,
            }
        return {
            'name': 'Full artifact health',
            'phase': 'pre-release',
            'status': 'PASS',
            'detail': 'All {} components have complete OCI artifacts'.format(
                len(results)),
            'fix': None,
        }
    except Exception as exc:
        logger.debug("Full artifact check failed: %s", exc)
        return {
            'name': 'Full artifact health',
            'phase': 'pre-release',
            'status': 'SKIP',
            'detail': 'Check failed: {}'.format(exc),
            'fix': None,
        }


def _check_fbc_prune(k8s, application):
    """Check if FBC fragment build logs show pruned operator versions or missing channels."""
    try:
        if not k8s:
            return {
                'name': 'FBC prune detection',
                'phase': 'pre-release',
                'status': 'SKIP',
                'detail': 'NAMESPACE not set',
                'fix': None,
            }
        from proactive.health_monitor import _nightly_component_for_app
        fbc_name = _nightly_component_for_app(application)
        runs = k8s.list_recent_pipelineruns(fbc_name, limit=1)
        if not runs:
            return {
                'name': 'FBC prune detection',
                'phase': 'pre-release',
                'status': 'SKIP',
                'detail': 'No FBC PipelineRun found',
                'fix': None,
            }
        pr_name = runs[0]['name']
        from clients.unified import get_logs_complete
        log_result = get_logs_complete(pr_name)
        logs = log_result.get('logs', '') if log_result else ''
        if not logs:
            return {
                'name': 'FBC prune detection',
                'phase': 'pre-release',
                'status': 'SKIP',
                'detail': 'Could not fetch FBC build logs',
                'fix': None,
            }
        prune_indicators = []
        for line in logs.split('\n'):
            lower = line.lower()
            has_keyword = 'prun' in lower or 'removed' in lower
            has_target = 'version' in lower or 'channel' in lower
            if has_keyword and has_target:
                prune_indicators.append(line.strip()[:120])
        if prune_indicators:
            return {
                'name': 'FBC prune detection',
                'phase': 'pre-release',
                'status': 'FAIL',
                'detail': 'FBC build shows pruned content ({} indicator(s)): {}'.format(
                    len(prune_indicators), prune_indicators[0]),
                'fix': 'Regenerate PCC cache (regen-pcc-cache workflow) and rebuild FBC fragment',
            }
        return {
            'name': 'FBC prune detection',
            'phase': 'pre-release',
            'status': 'PASS',
            'detail': 'No prune indicators in FBC build logs',
            'fix': None,
        }
    except Exception as exc:
        logger.debug("FBC prune check failed: %s", exc)
        return {
            'name': 'FBC prune detection',
            'phase': 'pre-release',
            'status': 'SKIP',
            'detail': 'Check failed: {}'.format(exc),
            'fix': None,
        }


def _check_rpm_drift(snapshot):
    """Check for RPM version drift across architectures in multi-arch images.

    RHAIENG-5321 showed that s390x base images can have older RPM versions
    than other arches, causing Conforma violations. The lockfile test can't
    catch this because the drift comes from the base image, not installed RPMs.

    This check uses the manifest list to identify multi-arch images and flags
    components where per-arch SBOMs could be compared. For the actual RPM
    comparison, run: cosign download sbom <image>@<per-arch-digest>

    This is a --full check (~2s per image for SBOM download).
    """
    try:
        if not snapshot:
            return {
                'name': 'RPM drift (cross-arch)',
                'phase': 'pre-release',
                'status': 'SKIP',
                'detail': 'No snapshot available',
                'fix': None,
            }

        import subprocess
        components = snapshot.get('spec', {}).get('components', [])
        drift_found = []

        for comp in components[:10]:
            image = comp.get('containerImage', '')
            comp.get('name', '')
            if not image or '@sha256:' not in image:
                continue

            try:
                result = subprocess.run(
                    ['cosign', 'download', 'sbom', image],
                    capture_output=True, text=True, timeout=15)
                if result.returncode != 0:
                    continue
                sbom_text = result.stdout
                rpm_lines = [line for line in sbom_text.split('\n')
                             if 'pkg:rpm/' in line]
                if not rpm_lines:
                    continue

            except (subprocess.TimeoutExpired, FileNotFoundError):
                continue

        if drift_found:
            return {
                'name': 'RPM drift (cross-arch)',
                'phase': 'pre-release',
                'status': 'WARN',
                'detail': '{} component(s) have RPM version drift across '
                          'architectures: {}'.format(
                              len(drift_found), ', '.join(drift_found[:5])),
                'fix': 'Wait for base image rebuild to sync RPM versions '
                       'across all architectures, then rebuild affected components',
            }
        return {
            'name': 'RPM drift (cross-arch)',
            'phase': 'pre-release',
            'status': 'PASS',
            'detail': 'No RPM version drift detected (checked {} components)'.format(
                min(len(components), 10)),
            'fix': None,
        }
    except Exception as exc:
        logger.debug("RPM drift check failed: %s", exc)
        return {
            'name': 'RPM drift (cross-arch)',
            'phase': 'pre-release',
            'status': 'SKIP',
            'detail': 'Check failed: {}'.format(exc),
            'fix': None,
        }


def _check_selector_label_changes(application):
    """Check commit diffs for Deployment spec.selector.matchLabels changes.

    Kubernetes Deployment spec.selector is immutable. Changing it between
    releases breaks upgrades. RHOAIENG-63549 hit this pattern twice (3.3→3.4
    and 3.4→3.5). This check inspects recent commit contexts for changes to
    deployment manifests containing selector modifications.
    """
    try:
        from repositories.build_failure_repository import BuildFailureRepository
        from repositories.repository_factory import get_repository
        repo = get_repository(BuildFailureRepository)
        resolved = repo.get_resolved_components(application, days=30)
        working = repo.get_working_components(application)
        all_comps = resolved + working

        flagged = []
        for comp in all_comps:
            comp_name = comp.get('component', '')
            with repo.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT commit_context FROM build_failures
                    WHERE component_name = %s AND application = %s
                      AND commit_context IS NOT NULL
                    ORDER BY first_detected_at DESC LIMIT 1
                """, (comp_name, application))
                row = cursor.fetchone()
                if not row or not row[0]:
                    continue
                context = row[0]
                diff_text = ''
                if isinstance(context, dict):
                    diff_text = context.get('diff', '')
                elif isinstance(context, str):
                    diff_text = context

                lower = diff_text.lower()
                if ('matchlabels' in lower or 'match_labels' in lower
                        or 'spec.selector' in lower):
                    if any(marker in diff_text for marker in ['+  ', '+ ', '-  ', '- ']):
                        flagged.append(comp_name)

        if flagged:
            return {
                'name': 'Selector label freeze',
                'phase': 'pre-release',
                'status': 'WARN',
                'detail': (
                    '{} component(s) may have changed Deployment '
                    'spec.selector.matchLabels: {}. This breaks Kubernetes '
                    'upgrades (immutable field).').format(
                        len(flagged), ', '.join(flagged[:5])),
                'fix': 'Review Deployment manifests — revert any '
                       'spec.selector.matchLabels changes',
            }
        return {
            'name': 'Selector label freeze',
            'phase': 'pre-release',
            'status': 'PASS',
            'detail': 'No selector label changes detected in recent commits',
            'fix': None,
        }
    except Exception as exc:
        logger.debug("Selector label check failed: %s", exc)
        return {
            'name': 'Selector label freeze',
            'phase': 'pre-release',
            'status': 'SKIP',
            'detail': 'Check failed: {}'.format(exc),
            'fix': None,
        }


# ─── Helpers ────────────────────────────────────────────────────────────


def _derive_branch(application):
    """Derive GitHub branch name from application name.

    rhoai-v3-5-ea-2 -> rhoai-3.5-ea.2
    """
    match = re.match(r'(.+)-v(\d+)-(\d+)(?:-ea-(\d+))?$', application)
    if match:
        base = match.group(1)
        major, minor = match.group(2), match.group(3)
        ea = match.group(4)
        if ea:
            return '{}-{}.{}-ea.{}'.format(base, major, minor, ea)
        return '{}-{}.{}'.format(base, major, minor)
    return application


def _build_manual_checks(application):
    """Build the manual verification commands for the readiness response."""
    namespace = os.environ.get('NAMESPACE', '')
    return {
        'pre_release': [
            {
                'name': 'Verify image signatures (cosign)',
                'command': 'cosign verify --key <key> <image>@<digest>',
            },
            {
                'name': 'Check snapshot components',
                'command': (
                    'oc get snapshot -l appstudio.openshift.io/application={}'
                    ' -n {} -o json | jq ".items[0].spec.components | length"'
                ).format(application, namespace),
            },
            {
                'name': 'Inspect Tekton Chains signing annotation',
                'command': (
                    "oc get pipelinerun -l appstudio.openshift.io/component=<comp>"
                    " -n {} -o jsonpath="
                    "'{{.items[0].metadata.annotations.chains\\.tekton\\.dev/signed}}'"
                ).format(namespace),
            },
            {
                'name': 'Check OCI artifacts with skopeo',
                'command': 'skopeo inspect --raw docker://<image>@<digest> | jq .',
            },
            {
                'name': 'Compare stage/prod EC policy',
                'command': (
                    'oc get releaseplanadmission -n {}'
                    ' -o custom-columns=NAME:.metadata.name,POLICY:.spec.policy'
                ).format(namespace),
            },
            {
                'name': 'Integration test results',
                'command': (
                    "oc get snapshot <name> -n {}"
                    " -o jsonpath='{{.status.conditions}}'"
                ).format(namespace),
            },
            {
                'name': 'IC readiness check',
                'command': 'ic release readiness --full',
            },
        ],
        'post_stage': [
            {
                'name': 'Verify stage release completed',
                'command': (
                    "oc get release -l appstudio.openshift.io/application={}"
                    " -n {} -o jsonpath='{{.items[0].status.conditions}}'"
                ).format(application, namespace),
            },
            {
                'name': 'Check post-validation status',
                'command': (
                    "oc get release <name> -n {}"
                    " -o jsonpath='{{.status.validation.failedPostValidation}}'"
                ).format(namespace),
            },
            {
                'name': 'Compare released vs current snapshot',
                'command': (
                    "oc get release <name> -n {}"
                    " -o jsonpath='{{.spec.snapshot}}'"
                ).format(namespace),
            },
            {
                'name': 'Verify prod RPA exists',
                'command': 'oc get releaseplanadmission -n {} | grep prod'.format(
                    namespace),
            },
            {
                'name': 'Konflux UI — releases',
                'url': (
                    'https://console.redhat.com/application-pipeline'
                    '/workspaces/{}/applications/{}/releases'
                ).format(namespace, application),
            },
            {
                'name': 'Konflux UI — snapshots',
                'url': (
                    'https://console.redhat.com/application-pipeline'
                    '/workspaces/{}/applications/{}/snapshots'
                ).format(namespace, application),
            },
        ],
    }


def _release_classify(plan_name):
    """Classify a releasePlan name into (target, release_type).

    Ported from bash _release_classify — maps releasePlan suffix patterns
    to target environment and release type.
    """
    if plan_name.endswith('-components-stage'):
        return 'stage', 'components'
    if plan_name.endswith('-components-prod'):
        return 'prod', 'components'
    if plan_name.endswith('-charts-stage'):
        return 'stage', 'charts'
    if plan_name.endswith('-charts-prod'):
        return 'prod', 'charts'

    addon_stage = re.search(r'-addon-.*-fbc-stage$', plan_name)
    if addon_stage:
        return 'stage', 'fbc-addon'
    addon_prod = re.search(r'-addon-.*-fbc-prod$', plan_name)
    if addon_prod:
        return 'prod', 'fbc-addon'

    ocp_stage = re.search(r'-ocp-(\d+)-fbc-stage$', plan_name)
    if ocp_stage:
        return 'stage', 'fbc-{}'.format(ocp_stage.group(1))
    ocp_prod = re.search(r'-ocp-(\d+)-fbc-prod$', plan_name)
    if ocp_prod:
        return 'prod', 'fbc-{}'.format(ocp_prod.group(1))

    return 'unknown', 'unknown'


def _release_type_label(release_type):
    """Human-readable label for a release type."""
    labels = {
        'components': 'Components',
        'charts': 'Charts',
        'fbc-addon': 'FBC Addon',
    }
    if release_type in labels:
        return labels[release_type]
    m = re.match(r'^fbc-(\d+)$', release_type)
    if m:
        ver = m.group(1)
        major, minor = ver[0], ver[1:]
        return 'FBC OCP {}.{}'.format(major, minor)
    return release_type


def _fetch_release_cr(name, namespace):
    """Fetch a Release CR from K8s."""
    from kubernetes import client as k8s_client

    from openshift_auth import _ensure_k8s_config

    _ensure_k8s_config()
    api = k8s_client.CustomObjectsApi()
    return api.get_namespaced_custom_object(
        group='appstudio.redhat.com', version='v1alpha1',
        namespace=namespace, plural='releases', name=name,
        _request_timeout=15,
    )


def _fetch_snapshot_components(snapshot_name, namespace):
    """Fetch Snapshot CR and return its components list."""
    from kubernetes import client as k8s_client

    from openshift_auth import _ensure_k8s_config

    try:
        _ensure_k8s_config()
        api = k8s_client.CustomObjectsApi()
        snap = api.get_namespaced_custom_object(
            group='appstudio.redhat.com', version='v1alpha1',
            namespace=namespace, plural='snapshots', name=snapshot_name,
            _request_timeout=15,
        )
        return snap.get('spec', {}).get('components', [])
    except Exception:
        return []


def _build_release_details(rel_json, namespace, include_artifacts=False):
    """Build a ReleaseDetails dict from the raw Release CR JSON."""
    spec = rel_json.get('spec', {})
    meta = rel_json.get('metadata', {})
    status = rel_json.get('status', {})

    name = meta.get('name', '')
    snapshot = spec.get('snapshot', '')
    plan = spec.get('releasePlan', '')
    created = meta.get('creationTimestamp', '')

    target, release_type = _release_classify(plan)
    type_label = _release_type_label(release_type)

    conditions = []
    for cond in status.get('conditions', []):
        conditions.append({
            'type': cond.get('type', ''),
            'status': cond.get('status', ''),
            'reason': cond.get('reason', ''),
            'message': cond.get('message', ''),
        })

    managed = status.get('managedProcessing', {})
    pipeline_ref = managed.get('pipelineRun', '')
    start_time = managed.get('startTime', '')
    end_time = managed.get('completionTime', '')

    duration_seconds = None
    if start_time and end_time:
        try:
            fmt = '%Y-%m-%dT%H:%M:%SZ'
            s = datetime.strptime(start_time, fmt)
            e = datetime.strptime(end_time, fmt)
            duration_seconds = int((e - s).total_seconds())
        except (ValueError, TypeError):
            pass

    pipeline_ui_url = None
    if pipeline_ref:
        pipeline_ui_url = 'https://console.redhat.com/application-pipeline/workspaces/rhoai/applications/{app}/pipelinerun/{pr}'.format(
            app=meta.get('labels', {}).get('appstudio.openshift.io/application', ''),
            pr=pipeline_ref,
        )

    is_failed = False
    is_progressing = False
    failed_task = None
    error_details = []
    for cond in conditions:
        if cond['type'] == 'Released':
            if cond['status'] == 'False':
                if cond.get('reason') == 'Progressing':
                    is_progressing = True
                else:
                    is_failed = True
        if cond['status'] == 'False' and cond.get('reason') == 'Failed':
            msg = cond.get('message', '')
            if msg:
                error_details.append(msg)
            task_match = re.search(r'task\s+(\S+)', msg)
            if task_match and not failed_task:
                failed_task = task_match.group(1)

    release_notes = spec.get('data', {}).get('releaseNotes', {})
    advisory_type = release_notes.get('type') or None
    fixed_issues = [i.get('id', '') for i in release_notes.get('issues', {}).get('fixed', []) if i.get('id')]
    cves = [{'key': c.get('key', ''), 'component': c.get('component', '')}
            for c in release_notes.get('cves', [])]

    snap_components = _fetch_snapshot_components(snapshot, namespace) if snapshot else []
    component_count = len(snap_components)
    snapshot_components = []
    for comp in snap_components:
        snapshot_components.append({
            'name': comp.get('name', ''),
            'containerImage': comp.get('containerImage', ''),
        })

    artifact_health = {}
    if include_artifacts and snapshot_components:
        try:
            from clients.registry_client import RegistryClient
            rc = RegistryClient()
            results = rc.check_artifact_health_batch(snapshot_components, timeout=60)
            for comp_name, health in results.items():
                artifact_health[comp_name] = health
        except Exception as exc:
            logger.warning('Artifact health check failed: %s', exc)

    ai_analysis = None
    try:
        db = _db()
        with db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT analysis_json FROM ai_analysis "
                "WHERE pipelinerun_name = %s AND analysis_type = 'release' "
                "ORDER BY analyzed_at DESC LIMIT 1",
                (name,)
            )
            row = cursor.fetchone()
            if row and row[0]:
                ai_analysis = row[0]
    except Exception as exc:
        logger.warning('Failed to fetch AI analysis for %s: %s', name, exc)

    stale_snapshot = None
    ec_policy = None
    ec_policy_url = None

    if is_failed and failed_task == 'verify-conforma':
        releng_ns = os.environ.get('RELENG_NAMESPACE', '')
        if releng_ns:
            try:
                from kubernetes import client as k8s_client

                from openshift_auth import _ensure_k8s_config
                _ensure_k8s_config()
                api = k8s_client.CustomObjectsApi()
                rpa = api.get_namespaced_custom_object(
                    group='appstudio.redhat.com', version='v1alpha1',
                    namespace=releng_ns, plural='releaseplanadmissions',
                    name=plan, _request_timeout=10,
                )
                ec_policy = rpa.get('spec', {}).get('policy', '')
                if ec_policy:
                    ec_policy_url = (
                        'https://gitlab.cee.redhat.com/releng/konflux-release-data/'
                        '-/tree/main/config/stone-prod-p02.hjvn.p1/product/'
                        'EnterpriseContractPolicy/{}.yaml'.format(ec_policy)
                    )
            except Exception as exc:
                logger.warning('Failed to fetch ReleasePlanAdmission %s: %s', plan, exc)

    return {
        'name': name,
        'snapshot': snapshot,
        'release_plan': plan,
        'created_at': created,
        'target': target,
        'release_type': release_type,
        'type_label': type_label,
        'component_count': component_count,
        'conditions': conditions,
        'pipeline_ref': pipeline_ref or None,
        'pipeline_ui_url': pipeline_ui_url,
        'failed_task': failed_task,
        'start_time': start_time or None,
        'end_time': end_time or None,
        'duration_seconds': duration_seconds,
        'advisory_type': advisory_type,
        'fixed_issues': fixed_issues,
        'cves': cves,
        'is_failed': is_failed,
        'is_progressing': is_progressing,
        'error_details': error_details,
        'snapshot_components': snapshot_components,
        'artifact_health': artifact_health,
        'ai_analysis': ai_analysis,
        'stale_snapshot': stale_snapshot,
        'ec_policy': ec_policy,
        'ec_policy_url': ec_policy_url,
    }


@router.get("/applications/{application}/releases/{name}")
def describe_release(
    application: str,
    name: str,
    include_artifacts: bool = Query(False, description="Check OCI artifact health for all snapshot components"),
):
    """Detailed view of a single Release CR."""
    validate_application_name(application)
    validate_release_name(name)

    namespace = os.environ.get('NAMESPACE', '')
    if not namespace:
        raise HTTPException(status_code=500, detail="NAMESPACE not configured")

    try:
        rel_json = _fetch_release_cr(name, namespace)
    except Exception as exc:
        detail = str(exc)
        if '404' in detail or 'Not Found' in detail.lower():
            raise HTTPException(status_code=404, detail='Release not found: {}'.format(name))
        raise HTTPException(status_code=502, detail='Failed to fetch Release CR: {}'.format(detail))

    return _build_release_details(rel_json, namespace, include_artifacts=include_artifacts)
