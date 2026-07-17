"""Slack draft message templates for triage communication.

Pure functions — no I/O. Templates follow team conventions:
- Polite tone, no accusations ("seems to be", "might")
- No emojis
- Address people by name ("Hi {name},")
- End with a question or request
"""


def format_draft(message_type, component, contact, build_url=None,
                 root_cause=None, failed_step=None, days_to_freeze=None,
                 days_failing=None, recommended_files=None, **kwargs):
    """Generate a Slack draft message for triage.

    Returns draft message text (does NOT send).
    """
    templates = {
        'resolved': _resolved,
        'new_failure': _new_failure,
        'followup': _followup,
        'escalation': _escalation,
    }
    formatter = templates.get(message_type, _generic)
    return formatter(
        component=component, contact=contact, build_url=build_url,
        root_cause=root_cause, failed_step=failed_step,
        days_to_freeze=days_to_freeze, days_failing=days_failing,
        recommended_files=recommended_files, **kwargs,
    )


def _resolved(component, contact, build_url=None, **kwargs):
    parts = ['Hi {},'.format(contact),
             '{} build succeeded.'.format(component)]
    if build_url:
        parts.append('Build: {}'.format(build_url))
    return ' '.join(parts)


def _new_failure(component, contact, build_url=None, root_cause=None,
                 failed_step=None, recommended_files=None, **kwargs):
    parts = ['Hi {},'.format(contact),
             '{} seems to be failing'.format(component)]
    if failed_step:
        parts[-1] += ' at {}.'.format(failed_step)
    else:
        parts[-1] += '.'
    if root_cause:
        parts.append(root_cause + '.')
    if build_url:
        parts.append('Build: {}'.format(build_url))
    if recommended_files:
        files = (', '.join(recommended_files)
                 if isinstance(recommended_files, list)
                 else recommended_files)
        parts.append('Files that might need updating: {}'.format(files))
    parts.append('Could you take a look when you get a chance?')
    return ' '.join(parts)


def _followup(component, contact, root_cause=None,
              days_to_freeze=None, **kwargs):
    parts = ['Hi {},'.format(contact),
             'checking in on {}.'.format(component)]
    if root_cause:
        parts.append('Any progress on {}?'.format(root_cause))
    if days_to_freeze is not None:
        parts.append('Code freeze is {} days away.'.format(days_to_freeze))
    return ' '.join(parts)


def _escalation(component, contact, root_cause=None,
                days_failing=None, **kwargs):
    parts = ['Hi {},'.format(contact)]
    if days_failing:
        parts.append(
            '{} has been failing for {} days without resolution.'.format(
                component, days_failing))
    else:
        parts.append('{} is still failing.'.format(component))
    if root_cause:
        parts.append(root_cause + '.')
    parts.append('Could someone take a look at this?')
    return ' '.join(parts)


def _generic(component, contact, **kwargs):
    return ('Hi {}, regarding {} — could you take a look '
            'when you get a chance?'.format(contact, component))
