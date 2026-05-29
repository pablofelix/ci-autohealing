"""Database-backed skill registry for cluster deployments.

Uses PostgreSQL skill_sources + skills tables. Falls back to JSON file
registry when DB is unavailable (local dev without database).
"""

import json
from datetime import datetime, timezone
from typing import Dict, List, Optional

from skills.models import SkillEntry, SkillMetadata, SourceEntry


class DatabaseSkillRegistry:
    """Skill registry backed by PostgreSQL."""

    def __init__(self, db):
        self.db = db

    def save(self):
        pass

    def add_source(self, name: str, url: str, commit: str, local_path: str) -> SourceEntry:
        now = datetime.now(timezone.utc).isoformat()
        with self.db.connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO skill_sources (name, url, commit_sha, local_path, added_at, updated_at)
                VALUES (%s, %s, %s, %s, NOW(), NOW())
                ON CONFLICT (name) DO UPDATE
                SET url = EXCLUDED.url, commit_sha = EXCLUDED.commit_sha,
                    local_path = EXCLUDED.local_path, updated_at = NOW()
            """, (name, url, commit, local_path))
            conn.commit()
        return SourceEntry(name=name, url=url, commit=commit,
                           added_at=now, local_path=local_path)

    def remove_source(self, name: str) -> int:
        with self.db.connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM skills WHERE source = %s", (name,))
            count = cur.fetchone()[0]
            cur.execute("DELETE FROM skill_sources WHERE name = %s", (name,))
            conn.commit()
        return count

    def add_skill(self, name: str, source: str, path: str, metadata: SkillMetadata,
                  initial_tags: Optional[List[str]] = None) -> SkillEntry:
        tags = list(initial_tags) if initial_tags else []
        if source and source not in tags:
            tags.insert(0, source)
        if metadata.category and metadata.category not in tags:
            tags.append(metadata.category)

        qname = '{}/{}'.format(source, name)
        meta_json = json.dumps(metadata.to_dict())

        with self.db.connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO skills (qualified_name, name, source, path, status, tags, metadata)
                VALUES (%s, %s, %s, %s, 'active', %s, %s)
                ON CONFLICT (qualified_name) DO UPDATE
                SET path = EXCLUDED.path, tags = EXCLUDED.tags,
                    metadata = EXCLUDED.metadata, updated_at = NOW()
            """, (qname, name, source, path, tags, meta_json))
            conn.commit()

        return SkillEntry(name=name, source=source, path=path,
                          status='active', metadata=metadata, tags=tags)

    def remove_skill(self, qualified_name: str) -> bool:
        with self.db.connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM skills WHERE qualified_name = %s", (qualified_name,))
            conn.commit()
            return cur.rowcount > 0

    def get_skill(self, name: str) -> Optional[SkillEntry]:
        with self.db.connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT qualified_name, name, source, path, status, tags, metadata
                FROM skills WHERE qualified_name = %s
            """, (name,))
            row = cur.fetchone()
            if row:
                return self._row_to_entry(row)

            cur.execute("""
                SELECT qualified_name, name, source, path, status, tags, metadata
                FROM skills WHERE name = %s
            """, (name,))
            rows = cur.fetchall()
            if len(rows) == 1:
                return self._row_to_entry(rows[0])
            if len(rows) > 1:
                names = ', '.join(r[0] for r in rows)
                raise KeyError(
                    'Ambiguous skill name "{}". Use qualified name: {}'.format(name, names))
        return None

    def list_skills(self, tag: Optional[str] = None, source: Optional[str] = None,
                    status: Optional[str] = None) -> List[SkillEntry]:
        conditions = []
        params = []
        if tag:
            conditions.append("tags @> %s")
            params.append([tag])
        if source:
            conditions.append("source = %s")
            params.append(source)
        if status:
            conditions.append("status = %s")
            params.append(status)

        where = ' AND '.join(conditions) if conditions else '1=1'
        query = """
            SELECT qualified_name, name, source, path, status, tags, metadata
            FROM skills WHERE {} ORDER BY source, name
        """.format(where)

        with self.db.connection() as conn:
            cur = conn.cursor()
            cur.execute(query, params)
            return [self._row_to_entry(r) for r in cur.fetchall()]

    def add_tag(self, name: str, tag: str) -> bool:
        skill = self.get_skill(name)
        if not skill:
            return False
        with self.db.connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                UPDATE skills SET tags = array_append(tags, %s), updated_at = NOW()
                WHERE qualified_name = %s AND NOT (%s = ANY(tags))
            """, (tag, skill.qualified_name, tag))
            conn.commit()
        return True

    def remove_tag(self, name: str, tag: str) -> bool:
        skill = self.get_skill(name)
        if not skill:
            return False
        with self.db.connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                UPDATE skills SET tags = array_remove(tags, %s), updated_at = NOW()
                WHERE qualified_name = %s
            """, (tag, skill.qualified_name))
            conn.commit()
            return cur.rowcount > 0

    def list_tags(self) -> Dict[str, int]:
        with self.db.connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT tag, COUNT(*) as cnt
                FROM skills, UNNEST(tags) AS tag
                GROUP BY tag ORDER BY cnt DESC, tag
            """)
            return {r[0]: r[1] for r in cur.fetchall()}

    def list_sources(self) -> List[SourceEntry]:
        with self.db.connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT name, url, commit_sha, local_path, added_at
                FROM skill_sources ORDER BY name
            """)
            return [SourceEntry(
                name=r[0], url=r[1], commit=r[2] or '',
                local_path=r[3] or '', added_at=str(r[4]),
            ) for r in cur.fetchall()]

    def update_source_commit(self, name: str, commit: str):
        with self.db.connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                UPDATE skill_sources SET commit_sha = %s, updated_at = NOW()
                WHERE name = %s
            """, (commit, name))
            conn.commit()

    @property
    def sources(self) -> Dict[str, SourceEntry]:
        return {s.name: s for s in self.list_sources()}

    @property
    def skills(self) -> Dict[str, SkillEntry]:
        return {s.qualified_name: s for s in self.list_skills()}

    @staticmethod
    def _row_to_entry(row) -> SkillEntry:
        meta_data = row[6] if isinstance(row[6], dict) else json.loads(row[6] or '{}')
        return SkillEntry(
            name=row[1],
            source=row[2],
            path=row[3] or '',
            status=row[4] or 'active',
            metadata=SkillMetadata.from_dict(meta_data),
            tags=list(row[5] or []),
        )


def get_registry():
    """Return DB registry if database is available, JSON file registry otherwise."""
    try:
        from cli.db import check_db
        if check_db():
            from cli.db import _get_db_connection
            return DatabaseSkillRegistry(_get_db_connection())
    except Exception:
        pass
    from skills.registry import SkillRegistry
    return SkillRegistry()
