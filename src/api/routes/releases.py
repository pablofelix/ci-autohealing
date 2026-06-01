"""Release readiness and freeze calendar API routes."""

from datetime import date
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from repositories.connection import DatabaseConnection
from repositories.build_failure_repository import BuildFailureRepository
from repositories.conforma_repository import ConformaRepository
from repositories.repository_factory import get_repository
from config import CollectorConfig


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


@router.get("/applications/{application}/readiness")
def get_readiness(application: str) -> Dict[str, Any]:
    build_repo = get_repository(BuildFailureRepository)
    conforma_repo = get_repository(ConformaRepository)

    failing = build_repo.find_failing_component_names(application) or set()
    fail_count = len(failing)

    unresolved_conforma = conforma_repo.find_unresolved_component_names(application)
    conforma_count = len(unresolved_conforma)

    freeze = get_active_freeze()

    blockers = []
    risks = []

    if conforma_count > 0:
        blockers.append(f"{conforma_count} component(s) with unexcepted conforma violations")
    if freeze:
        blockers.append(f"Pipeline frozen until {freeze['end_date']} ({freeze['reason']})")
    if fail_count > 0:
        risks.append(f"{fail_count} component(s) with failing builds")

    if blockers:
        verdict = "NOT_READY"
    elif risks:
        verdict = "AT_RISK"
    else:
        verdict = "READY"

    schedule = get_schedule(application)

    return {
        'application': application,
        'verdict': verdict,
        'build_failures': fail_count,
        'conforma_violations': conforma_count,
        'failing_components': sorted(failing),
        'conforma_components': sorted(unresolved_conforma),
        'freeze': freeze,
        'blockers': blockers,
        'risks': risks,
        'schedule': schedule,
    }
