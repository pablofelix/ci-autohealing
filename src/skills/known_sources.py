"""Catalog of well-known skill sources (convenience shortcuts)."""

KNOWN_SOURCES = {
    'aiops-infra': {
        'url': 'https://github.com/opendatahub-io/aiops-infra',
        'description': 'RHOAI/ODH component onboarding and conforma fix skills',
        'skill_paths': ['.claude/skills'],
    },
    'ai-helpers': {
        'url': 'https://github.com/opendatahub-io/ai-helpers',
        'description': 'Utility skills — CVE scanning, RPM inspection, code review',
        'skill_paths': ['helpers/skills'],
    },
}


def resolve_source(name_or_url: str) -> tuple:
    """Resolve a source name or URL to (name, url).

    If name_or_url is a known source shorthand, return its URL.
    If it looks like a URL, derive a name from it.
    """
    if name_or_url in KNOWN_SOURCES:
        return name_or_url, KNOWN_SOURCES[name_or_url]['url']

    if '/' in name_or_url and ('github.com' in name_or_url or 'gitlab' in name_or_url
                                or name_or_url.startswith('http')):
        name = name_or_url.rstrip('/').rsplit('/', 1)[-1]
        name = name.replace('.git', '')
        return name, name_or_url

    raise ValueError(
        'Unknown source: "{}". Use a git URL or one of: {}'.format(
            name_or_url, ', '.join(sorted(KNOWN_SOURCES.keys()))))
