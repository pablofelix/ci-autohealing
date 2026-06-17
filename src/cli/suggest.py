"""Fuzzy matching and suggestions for CLI input.

When a user types a wrong component or app name, suggest the closest match.
Uses simple substring and edit-distance matching — no external deps.
"""


def _edit_distance(a, b):
    """Levenshtein distance between two strings."""
    if len(a) < len(b):
        return _edit_distance(b, a)
    if len(b) == 0:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            cost = 0 if ca == cb else 1
            curr.append(min(curr[j] + 1, prev[j + 1] + 1, prev[j] + cost))
        prev = curr
    return prev[len(b)]


def suggest_match(input_name, candidates, max_suggestions=3, max_distance=5):
    """Find closest matches from a list of candidates.

    Returns list of (candidate, distance) tuples, sorted by distance.
    Checks substring match first, then edit distance.
    """
    if not candidates:
        return []

    input_lower = input_name.lower()
    matches = []

    for c in candidates:
        c_lower = c.lower()
        if input_lower in c_lower or c_lower in input_lower:
            matches.append((c, 0))
        else:
            dist = _edit_distance(input_lower, c_lower)
            if dist <= max_distance:
                matches.append((c, dist))

    matches.sort(key=lambda x: x[1])
    return matches[:max_suggestions]


def format_suggestion(input_name, candidates, resource_type='component'):
    """Format a 'did you mean?' suggestion string.

    Returns None if no good suggestions found.
    """
    suggestions = suggest_match(input_name, candidates)
    if not suggestions:
        return None

    if len(suggestions) == 1:
        return "Did you mean '{}'?".format(suggestions[0][0])

    names = [s[0] for s in suggestions]
    return "Did you mean one of: {}?".format(', '.join(names))
