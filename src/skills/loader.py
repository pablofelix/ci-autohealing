"""Clone repos and discover skills from SKILL.md files."""

import hashlib
import os
import re
import subprocess
from typing import List, Optional, Tuple

from skills.models import SkillMetadata

SKILL_SCAN_DIRS = ['.claude/skills', 'helpers/skills', 'skills']


def _cache_dir() -> str:
    base = os.environ.get('IC_SKILLS_DIR', os.path.expanduser('~/.ic'))
    return os.path.join(base, 'cache')


def _cache_path(name: str, url: str, branch: Optional[str] = None) -> str:
    cache_key = '{}@{}'.format(url, branch) if branch else url
    url_hash = hashlib.sha256(cache_key.encode()).hexdigest()[:12]
    return os.path.join(_cache_dir(), '{}-{}'.format(name, url_hash))


def clone_source(url: str, name: str, branch: Optional[str] = None) -> Tuple[str, str]:
    """Clone a git repo (shallow) into the cache directory.

    Returns (local_path, commit_sha).
    """
    dest = _cache_path(name, url, branch)
    os.makedirs(_cache_dir(), exist_ok=True)

    if os.path.isdir(dest):
        result = subprocess.run(
            ['git', '-C', dest, 'pull', '--ff-only'],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            remote_ref = 'origin/{}'.format(branch) if branch else 'origin/HEAD'
            subprocess.run(
                ['git', '-C', dest, 'fetch', '--depth', '1', 'origin'],
                capture_output=True, text=True, timeout=60,
            )
            subprocess.run(
                ['git', '-C', dest, 'reset', '--hard', remote_ref],
                capture_output=True, text=True, timeout=60,
            )
    else:
        cmd = ['git', 'clone', '--depth', '1']
        if branch:
            cmd += ['--branch', branch]
        cmd += [url, dest]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError('git clone failed: {}'.format(result.stderr.strip()))

    commit = subprocess.run(
        ['git', '-C', dest, 'rev-parse', 'HEAD'],
        capture_output=True, text=True,
    )
    sha = commit.stdout.strip()[:12]
    return dest, sha


def use_local_source(path: str) -> Tuple[str, str]:
    """Use a local directory as a skill source.

    Returns (local_path, commit_sha_or_'local').
    """
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isdir(path):
        raise FileNotFoundError('Not a directory: {}'.format(path))

    commit = subprocess.run(
        ['git', '-C', path, 'rev-parse', 'HEAD'],
        capture_output=True, text=True,
    )
    sha = commit.stdout.strip()[:12] if commit.returncode == 0 else 'local'
    return path, sha


def discover_skills(root: str) -> List[Tuple[str, SkillMetadata]]:
    """Scan a directory for SKILL.md files and return parsed metadata.

    Returns list of (skill_dir_path, SkillMetadata).
    """
    found = []
    for scan_dir in SKILL_SCAN_DIRS:
        base = os.path.join(root, scan_dir)
        if not os.path.isdir(base):
            continue
        for entry in sorted(os.listdir(base)):
            skill_dir = os.path.join(base, entry)
            skill_file = os.path.join(skill_dir, 'SKILL.md')
            if os.path.isfile(skill_file):
                meta = parse_skill_md(skill_file)
                if meta:
                    found.append((skill_dir, meta))
    return found


def parse_skill_md(path: str) -> Optional[SkillMetadata]:
    """Parse YAML frontmatter from a SKILL.md file."""
    try:
        with open(path) as f:
            content = f.read()
    except OSError:
        return None

    match = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
    if not match:
        return None

    frontmatter = _parse_yaml_simple(match.group(1))
    if not frontmatter.get('name'):
        return None

    return SkillMetadata.from_dict(frontmatter)


def _parse_yaml_simple(text: str) -> dict:
    """Minimal YAML parser for flat frontmatter (avoids PyYAML dependency)."""
    result = {}
    current_key = None
    current_list = None

    for line in text.split('\n'):
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue

        if stripped.startswith('- ') and current_key and current_list is not None:
            current_list.append(stripped[2:].strip())
            result[current_key] = current_list
            continue

        m = re.match(r'^([\w-]+)\s*:\s*(.*)', stripped)
        if m:
            key, value = m.group(1), m.group(2).strip()
            current_key = key
            if not value:
                current_list = []
                result[key] = current_list
                continue
            current_list = None
            if (value.startswith('"') and value.endswith('"')) or \
               (value.startswith("'") and value.endswith("'")):
                result[key] = value[1:-1]
            elif value.lower() == 'true':
                result[key] = True
            elif value.lower() == 'false':
                result[key] = False
            elif value.startswith('[') and value.endswith(']'):
                items = [item.strip().strip('"\'') for item in value[1:-1].split(',') if item.strip()]
                result[key] = items
            else:
                result[key] = value
        else:
            current_list = None

    return result
