"""Static security analysis for skill scripts."""

import os
import re
from dataclasses import dataclass, field
from typing import List, Optional

from skills.models import SkillMetadata


@dataclass
class Finding:
    severity: str  # 'critical' | 'warning'
    check: str
    message: str
    file: str
    line: int  # 1-based, 0 if N/A


@dataclass
class ValidationResult:
    skill_name: str
    findings: List[Finding] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not any(f.severity == 'critical' for f in self.findings)

    @property
    def critical_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == 'critical')

    @property
    def warning_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == 'warning')


SCANNABLE_EXTENSIONS = {'.sh', '.py', '.yaml', '.yml', '.md', '.bash', '.zsh'}
SKIP_DIRS = {'.git', '__pycache__', 'node_modules', '.venv', 'venv'}

SECRET_PATTERNS = [
    (r'sk-ant-[a-zA-Z0-9_-]{20,}', 'Anthropic API key'),
    (r'ghp_[a-zA-Z0-9]{36,}', 'GitHub personal access token'),
    (r'glpat-[a-zA-Z0-9_-]{20,}', 'GitLab personal access token'),
    (r'xoxb-[0-9]{10,}-[a-zA-Z0-9]+', 'Slack bot token'),
    (r'xoxp-[0-9]{10,}-[a-zA-Z0-9]+', 'Slack user token'),
    (r'AKIA[0-9A-Z]{16}', 'AWS access key ID'),
    (r'(?:password|passwd|pwd)\s*[=:]\s*["\'][^"\']{8,}["\']', 'Hardcoded password'),
    (r'(?:secret|token|api_key)\s*[=:]\s*["\'][a-zA-Z0-9+/=_-]{20,}["\']',
     'Hardcoded secret/token'),
]

_SYS_DIRS = r'(bin|boot|etc|home|lib|opt|root|sbin|sys|usr|var)'
DESTRUCTIVE_PATTERNS = [
    # rm -rf / or rm -fr /
    (r'rm\s+-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*\s+/\s*$', 'Recursive force-delete of root'),
    (r'rm\s+-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*\s+/' + _SYS_DIRS + r'\b',
     'Recursive force-delete of system directory'),
    (r'rm\s+-[a-zA-Z]*f[a-zA-Z]*r[a-zA-Z]*\s+/\s*$', 'Recursive force-delete of root'),
    (r'rm\s+-[a-zA-Z]*f[a-zA-Z]*r[a-zA-Z]*\s+/' + _SYS_DIRS + r'\b',
     'Recursive force-delete of system directory'),
    # rm -r -f / (separated flags)
    (r'rm\s+(?=.*-[a-zA-Z]*r)(?=.*-[a-zA-Z]*f).*\s+/\s*$',
     'Recursive force-delete of root'),
    (r'rm\s+(?=.*-[a-zA-Z]*r)(?=.*-[a-zA-Z]*f).*\s+/' + _SYS_DIRS + r'\b',
     'Recursive force-delete of system directory'),
    # rm --recursive --force /
    (r'rm\s+.*--recursive.*--force.*\s+/\s*$', 'Recursive force-delete of root'),
    (r'rm\s+.*--force.*--recursive.*\s+/\s*$', 'Recursive force-delete of root'),
    (r'dd\s+if=.*of=/dev/', 'Raw disk write with dd'),
    (r'mkfs\b', 'Filesystem format command'),
    (r'DROP\s+(TABLE|DATABASE)\b', 'SQL DROP statement'),
    (r'format\s+[A-Z]:', 'Windows format command'),
    (r'>\s*/dev/sda', 'Direct write to block device'),
]

EXFILTRATION_PATTERNS = [
    (r'curl\b.*\$\{?\w*(TOKEN|SECRET|KEY|PASSWORD|PASS)\w*\}?',
     'curl with secret env var'),
    (r'wget\b.*\$\{?\w*(TOKEN|SECRET|KEY|PASSWORD|PASS)\w*\}?',
     'wget with secret env var'),
    (r'echo\s+\$\{?\w*(TOKEN|SECRET|KEY|PASSWORD)\w*\}?\s*\|\s*(curl|wget|nc|ncat)',
     'Piping secret to network command'),
]

UNSAFE_PATTERNS = [
    (r'eval\s+"\$', 'Dynamic evaluation with variable expansion'),
    (r'bash\s+-c\s+"\$', 'Shell -c with variable expansion'),
    (r'exec\s+"\$', 'Exec with variable expansion'),
]

COMMON_TOOLS = {
    'oc', 'kubectl', 'git', 'jq', 'yq', 'curl', 'wget', 'docker', 'podman',
    'buildah', 'skopeo', 'cosign', 'gh', 'python3', 'pip', 'make', 'cmake',
}


class SkillValidator:
    """Run static security checks on a skill directory."""

    def validate(self, skill_dir: str,
                 metadata: Optional[SkillMetadata] = None) -> ValidationResult:
        name = metadata.name if metadata else os.path.basename(skill_dir)
        result = ValidationResult(skill_name=name)

        if not os.path.isdir(skill_dir):
            result.findings.append(Finding(
                severity='critical', check='skill_format',
                message='Skill directory does not exist: {}'.format(skill_dir),
                file=name, line=0,
            ))
            return result

        result.findings.extend(self._check_skill_format(skill_dir, metadata))

        files = self._collect_files(skill_dir)
        for filepath in files:
            rel = os.path.relpath(filepath, skill_dir)
            try:
                with open(filepath) as f:
                    lines = f.readlines()
            except (OSError, UnicodeDecodeError):
                continue

            result.findings.extend(self._check_secrets(rel, lines))
            result.findings.extend(self._check_destructive_ops(rel, lines))
            result.findings.extend(self._check_exfiltration(rel, lines))
            result.findings.extend(self._check_unsafe_patterns(rel, lines))

        result.findings.extend(self._check_tools(skill_dir, files, metadata))

        return result

    def _collect_files(self, skill_dir: str) -> List[str]:
        files = []
        for root, dirs, filenames in os.walk(skill_dir, followlinks=False):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for fname in sorted(filenames):
                _, ext = os.path.splitext(fname)
                if ext.lower() in SCANNABLE_EXTENSIONS:
                    files.append(os.path.join(root, fname))
        return files

    def _check_skill_format(self, skill_dir: str,
                            metadata: Optional[SkillMetadata]) -> List[Finding]:
        findings = []
        skill_file = os.path.join(skill_dir, 'SKILL.md')
        if not os.path.isfile(skill_file):
            for sub in os.listdir(skill_dir) if os.path.isdir(skill_dir) else []:
                sub_skill = os.path.join(skill_dir, sub, 'SKILL.md')
                if os.path.isfile(sub_skill):
                    return findings
            findings.append(Finding(
                severity='critical', check='skill_format',
                message='No SKILL.md found in skill directory',
                file=os.path.basename(skill_dir), line=0,
            ))
            return findings

        if metadata and not metadata.name:
            findings.append(Finding(
                severity='critical', check='skill_format',
                message='SKILL.md missing required "name" field',
                file='SKILL.md', line=0,
            ))
        if metadata and not metadata.description:
            findings.append(Finding(
                severity='critical', check='skill_format',
                message='SKILL.md missing required "description" field',
                file='SKILL.md', line=0,
            ))
        return findings

    def _check_secrets(self, rel_path: str,
                       lines: List[str]) -> List[Finding]:
        return self._scan_patterns(
            rel_path, lines, SECRET_PATTERNS, 'hardcoded_secret', 'critical')

    def _check_destructive_ops(self, rel_path: str,
                               lines: List[str]) -> List[Finding]:
        return self._scan_patterns(
            rel_path, lines, DESTRUCTIVE_PATTERNS, 'destructive_op', 'critical',
            ignore_case=True)

    def _check_exfiltration(self, rel_path: str,
                            lines: List[str]) -> List[Finding]:
        return self._scan_patterns(
            rel_path, lines, EXFILTRATION_PATTERNS, 'exfiltration', 'critical')

    def _check_unsafe_patterns(self, rel_path: str,
                               lines: List[str]) -> List[Finding]:
        return self._scan_patterns(
            rel_path, lines, UNSAFE_PATTERNS, 'unsafe_pattern', 'warning')

    def _check_tools(self, skill_dir: str, files: List[str],
                     metadata: Optional[SkillMetadata]) -> List[Finding]:
        if not metadata or not metadata.ic_metadata:
            return []

        declared = set(metadata.ic_metadata.requires_tools)
        used = set()

        for filepath in files:
            if not filepath.endswith(('.sh', '.bash', '.zsh')):
                continue
            try:
                with open(filepath) as f:
                    content = f.read()
            except (OSError, UnicodeDecodeError):
                continue
            for tool in COMMON_TOOLS:
                if re.search(r'\b{}\b'.format(re.escape(tool)), content):
                    used.add(tool)

        undeclared = used - declared - {'bash', 'sh'}
        findings = []
        for tool in sorted(undeclared):
            findings.append(Finding(
                severity='warning', check='undeclared_tool',
                message='Uses "{}" but not declared in requires-tools'.format(tool),
                file='(shell scripts)', line=0,
            ))
        return findings

    @staticmethod
    def _scan_patterns(rel_path: str, lines: List[str],
                       patterns: list, check_name: str,
                       severity: str, ignore_case: bool = False) -> List[Finding]:
        flags = re.IGNORECASE if ignore_case else 0
        findings = []
        for i, line in enumerate(lines, 1):
            for pattern, desc in patterns:
                if re.search(pattern, line, flags):
                    findings.append(Finding(
                        severity=severity, check=check_name,
                        message=desc, file=rel_path, line=i,
                    ))
        return findings


def check_prerequisites(metadata: SkillMetadata) -> dict:
    """Check if a skill's required tools and env vars are available.

    Returns dict with 'tools' and 'env' status dicts.
    """
    import shutil

    result = {'tools': {}, 'env': {}, 'status': 'ok'}

    if metadata.ic_metadata:
        for tool in metadata.ic_metadata.requires_tools:
            found = shutil.which(tool) is not None
            result['tools'][tool] = found
            if not found:
                result['status'] = 'fail'

        for var in metadata.ic_metadata.requires_env:
            found = var in os.environ
            result['env'][var] = found
            if not found and result['status'] != 'fail':
                result['status'] = 'warn'

    return result


_WRITE_TOOLS = {'git', 'gh', 'oc', 'kubectl', 'podman', 'docker', 'buildah'}
_READ_TOOLS = {'jq', 'yq', 'grep', 'curl', 'wget', 'python3', 'cat', 'find'}
_DESTRUCTIVE_TOOLS = {'rm', 'mkfs', 'dd'}


def classify_risk(metadata, validation_result=None):
    """Classify skill risk level based on tools, env, and security findings.

    Returns 'low', 'medium', or 'high'.
    """
    if validation_result and validation_result.critical_count > 0:
        return 'high'

    tools = set()
    if metadata.ic_metadata:
        tools = set(metadata.ic_metadata.requires_tools)

    if tools & _DESTRUCTIVE_TOOLS:
        return 'high'

    needs_secrets = False
    if metadata.ic_metadata:
        env_vars = set(metadata.ic_metadata.requires_env)
        secret_vars = {v for v in env_vars
                       if any(k in v.upper() for k in ('TOKEN', 'SECRET', 'KEY', 'PASSWORD'))}
        if secret_vars:
            needs_secrets = True

    if tools & _WRITE_TOOLS or needs_secrets:
        return 'medium'

    if tools and tools <= _READ_TOOLS:
        return 'low'

    return 'medium'
