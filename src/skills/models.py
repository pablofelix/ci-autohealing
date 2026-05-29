"""Data models for the ic skill registry."""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class IcMetadata:
    """Optional ic-specific metadata from SKILL.md frontmatter."""

    requires_tools: List[str] = field(default_factory=list)
    requires_env: List[str] = field(default_factory=list)
    sandbox: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {'requires_tools': self.requires_tools, 'requires_env': self.requires_env}
        if self.sandbox:
            d['sandbox'] = self.sandbox
        return d

    @classmethod
    def from_dict(cls, data: dict) -> 'IcMetadata':
        return cls(
            requires_tools=data.get('requires_tools', []),
            requires_env=data.get('requires_env', []),
            sandbox=data.get('sandbox', {}),
        )


@dataclass
class SkillMetadata:
    """Parsed SKILL.md frontmatter."""

    name: str
    description: str
    allowed_tools: str = 'Bash'
    user_invocable: bool = False
    category: str = ''
    ic_metadata: Optional[IcMetadata] = None

    def to_dict(self) -> dict:
        d = {
            'name': self.name,
            'description': self.description,
            'allowed_tools': self.allowed_tools,
            'user_invocable': self.user_invocable,
        }
        if self.category:
            d['category'] = self.category
        if self.ic_metadata:
            d['ic_metadata'] = self.ic_metadata.to_dict()
        return d

    @classmethod
    def from_dict(cls, data: dict) -> 'SkillMetadata':
        ic_meta = None
        if 'ic_metadata' in data:
            ic_meta = IcMetadata.from_dict(data['ic_metadata'])
        return cls(
            name=data['name'],
            description=data.get('description', ''),
            allowed_tools=data.get('allowed_tools', data.get('allowed-tools', 'Bash')),
            user_invocable=data.get('user_invocable', data.get('user-invocable', False)),
            category=data.get('category', ''),
            ic_metadata=ic_meta,
        )


@dataclass
class SourceEntry:
    """A registered skill source (git repo or local path)."""

    name: str
    url: str
    commit: str
    added_at: str
    local_path: str

    def to_dict(self) -> dict:
        return {
            'name': self.name,
            'url': self.url,
            'commit': self.commit,
            'added_at': self.added_at,
            'local_path': self.local_path,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'SourceEntry':
        return cls(**data)


@dataclass
class SkillEntry:
    """A registered skill with its metadata and user-assigned tags."""

    name: str
    source: str
    path: str
    status: str  # 'active' | 'disabled'
    metadata: SkillMetadata
    tags: List[str] = field(default_factory=list)

    @property
    def qualified_name(self) -> str:
        return '{}/{}'.format(self.source, self.name)

    def to_dict(self) -> dict:
        return {
            'name': self.name,
            'source': self.source,
            'path': self.path,
            'status': self.status,
            'metadata': self.metadata.to_dict(),
            'tags': self.tags,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'SkillEntry':
        return cls(
            name=data['name'],
            source=data['source'],
            path=data['path'],
            status=data.get('status', 'active'),
            metadata=SkillMetadata.from_dict(data.get('metadata', {'name': data['name'], 'description': ''})),
            tags=data.get('tags', []),
        )
