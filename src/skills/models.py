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
    risk_level: str = 'medium'  # 'low', 'medium', 'high'
    ic_metadata: Optional[IcMetadata] = None

    def to_dict(self) -> dict:
        d = {
            'name': self.name,
            'description': self.description,
            'allowed_tools': self.allowed_tools,
            'user_invocable': self.user_invocable,
            'risk_level': self.risk_level,
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
            risk_level=data.get('risk_level', data.get('risk-level', 'medium')),
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
    branch: Optional[str] = None

    def to_dict(self) -> dict:
        d = {
            'name': self.name,
            'url': self.url,
            'commit': self.commit,
            'added_at': self.added_at,
            'local_path': self.local_path,
        }
        if self.branch:
            d['branch'] = self.branch
        return d

    @classmethod
    def from_dict(cls, data: dict) -> 'SourceEntry':
        return cls(
            name=data['name'],
            url=data['url'],
            commit=data['commit'],
            added_at=data['added_at'],
            local_path=data['local_path'],
            branch=data.get('branch'),
        )


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


@dataclass
class ExecutionResult:
    """Result of a skill execution."""

    skill_name: str
    status: str  # 'success', 'failed', 'dry_run', 'cancelled', 'prereq_failed'
    exit_code: int = 0
    stdout: str = ''
    stderr: str = ''
    duration_seconds: float = 0.0
    risk_level: str = 'medium'
    started_at: str = ''
    steps_executed: int = 0
    steps_total: int = 0
    triggered_by: str = 'cli'
    component_name: Optional[str] = None
    application: Optional[str] = None
    triage_item_id: Optional[int] = None
    dry_run_steps: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            'skill_name': self.skill_name,
            'status': self.status,
            'exit_code': self.exit_code,
            'stdout': self.stdout,
            'stderr': self.stderr,
            'duration_seconds': self.duration_seconds,
            'risk_level': self.risk_level,
            'started_at': self.started_at,
            'steps_executed': self.steps_executed,
            'steps_total': self.steps_total,
            'triggered_by': self.triggered_by,
            'component_name': self.component_name,
            'application': self.application,
            'triage_item_id': self.triage_item_id,
            'dry_run_steps': self.dry_run_steps,
        }
