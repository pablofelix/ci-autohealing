"""Skill registry — persistent JSON-based storage for sources and skills."""

import json
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional

from skills.models import SkillEntry, SkillMetadata, SourceEntry

_DEFAULT_DIR = os.path.expanduser('~/.ic')


def _registry_path() -> str:
    base = os.environ.get('IC_SKILLS_DIR', _DEFAULT_DIR)
    return os.path.join(base, 'skills.json')


class SkillRegistry:
    """Load, save, and query the skills.json registry."""

    def __init__(self, path: Optional[str] = None):
        self.path = path or _registry_path()
        self.sources: Dict[str, SourceEntry] = {}
        self.skills: Dict[str, SkillEntry] = {}
        self._load()

    def _load(self):
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return
        for s in data.get('sources', []):
            entry = SourceEntry.from_dict(s)
            self.sources[entry.name] = entry
        for s in data.get('skills', []):
            entry = SkillEntry.from_dict(s)
            self.skills[entry.qualified_name] = entry

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        data = {
            'sources': [s.to_dict() for s in self.sources.values()],
            'skills': [s.to_dict() for s in self.skills.values()],
        }
        with open(self.path, 'w') as f:
            json.dump(data, f, indent=2)

    def add_source(self, name: str, url: str, commit: str, local_path: str,
                   branch: Optional[str] = None) -> SourceEntry:
        entry = SourceEntry(
            name=name,
            url=url,
            commit=commit,
            added_at=datetime.now(timezone.utc).isoformat(),
            local_path=local_path,
            branch=branch,
        )
        self.sources[name] = entry
        return entry

    def remove_source(self, name: str) -> int:
        """Remove a source and all its skills. Returns count of removed skills."""
        self.sources.pop(name, None)
        to_remove = [k for k, v in self.skills.items() if v.source == name]
        for k in to_remove:
            del self.skills[k]
        return len(to_remove)

    def add_skill(self, name: str, source: str, path: str, metadata: SkillMetadata,
                  initial_tags: Optional[List[str]] = None) -> SkillEntry:
        tags = list(initial_tags) if initial_tags else []
        if source and source not in tags:
            tags.insert(0, source)
        if metadata.category and metadata.category not in tags:
            tags.append(metadata.category)

        entry = SkillEntry(
            name=name,
            source=source,
            path=path,
            status='active',
            metadata=metadata,
            tags=tags,
        )
        self.skills[entry.qualified_name] = entry
        return entry

    def remove_skill(self, qualified_name: str) -> bool:
        return self.skills.pop(qualified_name, None) is not None

    def get_skill(self, name: str) -> Optional[SkillEntry]:
        """Look up by qualified name first, then by short name (error if ambiguous)."""
        if name in self.skills:
            return self.skills[name]
        matches = [s for s in self.skills.values() if s.name == name]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise KeyError(
                'Ambiguous skill name "{}". Use qualified name: {}'.format(
                    name, ', '.join(m.qualified_name for m in matches)))
        return None

    def list_skills(self, tag: Optional[str] = None, source: Optional[str] = None,
                    status: Optional[str] = None) -> List[SkillEntry]:
        result = list(self.skills.values())
        if tag:
            result = [s for s in result if tag in s.tags]
        if source:
            result = [s for s in result if s.source == source]
        if status:
            result = [s for s in result if s.status == status]
        return sorted(result, key=lambda s: (s.source, s.name))

    def add_tag(self, qualified_name: str, tag: str) -> bool:
        skill = self.get_skill(qualified_name)
        if not skill:
            return False
        if tag not in skill.tags:
            skill.tags.append(tag)
        return True

    def remove_tag(self, qualified_name: str, tag: str) -> bool:
        skill = self.get_skill(qualified_name)
        if not skill:
            return False
        if tag in skill.tags:
            skill.tags.remove(tag)
            return True
        return False

    def list_tags(self) -> Dict[str, int]:
        """Return all tags with usage counts."""
        counts: Dict[str, int] = {}
        for skill in self.skills.values():
            for tag in skill.tags:
                counts[tag] = counts.get(tag, 0) + 1
        return dict(sorted(counts.items(), key=lambda x: (-x[1], x[0])))

    def list_sources(self) -> List[SourceEntry]:
        return sorted(self.sources.values(), key=lambda s: s.name)

    def update_source_commit(self, name: str, commit: str):
        if name in self.sources:
            self.sources[name].commit = commit
