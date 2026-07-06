"""Catalog of well-known skill sources (convenience shortcuts)."""

KNOWN_SOURCES = {
    'aiops-infra': {
        'url': 'https://github.com/opendatahub-io/aiops-infra',
        'description': 'RHOAI/ODH component onboarding and conforma fix skills',
        'skill_paths': ['.claude/skills'],
    },
    'aiops-infra/conforma': {
        'url': 'https://github.com/opendatahub-io/aiops-infra',
        'description': 'Conforma policy analysis, exceptions, and remediation skills',
        'branch': 'skill/conforma',
        'skill_paths': ['.claude/skills', 'skills'],
    },
    'ai-helpers': {
        'url': 'https://github.com/opendatahub-io/ai-helpers',
        'description': 'Utility skills — CVE scanning, RPM inspection, code review',
        'skill_paths': ['helpers/skills'],
    },
}


def resolve_source(name_or_url: str, branch_override: str = None) -> tuple:
    """Resolve a source name or URL to (name, url, branch).

    Supports:
    - Known source names: 'aiops-infra' -> (name, url, None)
    - Known source with branch: 'aiops-infra/conforma' -> (name, url, 'skill/conforma')
    - URL with @branch: 'https://github.com/org/repo@branch' -> (derived_name, url, branch)
    - Full URL: 'https://github.com/org/repo' -> (derived_name, url, None)

    branch_override takes precedence over @branch syntax and catalog defaults.
    """
    if name_or_url in KNOWN_SOURCES:
        entry = KNOWN_SOURCES[name_or_url]
        branch = branch_override or entry.get('branch')
        return name_or_url, entry['url'], branch

    url = name_or_url
    branch = None

    if _looks_like_url(url):
        url, branch = _split_url_branch(url)

    if branch_override:
        branch = branch_override

    if _looks_like_url(url):
        repo_name = url.rstrip('/').rsplit('/', 1)[-1].replace('.git', '')
        name = '{}@{}'.format(repo_name, branch) if branch else repo_name
        return name, url, branch

    raise ValueError(
        'Unknown source: "{}". Use a git URL or one of: {}'.format(
            name_or_url, ', '.join(sorted(KNOWN_SOURCES.keys()))))


def _looks_like_url(s: str) -> bool:
    return '/' in s and ('github.com' in s or 'gitlab' in s or s.startswith('http'))


def _split_url_branch(url: str) -> tuple:
    """Split 'https://host/org/repo@branch' into (url, branch).

    Only splits on @ that appears after the host/path portion
    (not in userinfo like git@github.com).
    """
    if '://' not in url:
        return url, None
    after_scheme = url.split('://', 1)[1]
    if '@' in after_scheme:
        scheme_and_path = url.rsplit('@', 1)
        return scheme_and_path[0], scheme_and_path[1]
    return url, None
