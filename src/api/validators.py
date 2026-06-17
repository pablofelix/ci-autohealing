"""Input validation for API and CLI."""

import re

_APP_PATTERN = re.compile(r'^[a-z0-9][a-z0-9-]*$')
_COMPONENT_PATTERN = re.compile(r'^[a-z0-9][a-z0-9-]*$')
_JIRA_PATTERN = re.compile(r'^[A-Z][A-Z0-9]+-\d+$')
_SKILL_PATTERN = re.compile(r'^[a-zA-Z0-9_-]+(/[a-zA-Z0-9_-]+)?$')


def validate_application_name(name):
    if not name or not name.strip():
        from api.errors import validation_error
        validation_error('Application name cannot be empty',
                         suggestion="Run 'ic get apps' to list available applications")
    name = name.strip()
    if not _APP_PATTERN.match(name):
        from api.errors import validation_error
        validation_error(
            "Invalid application name: '{}' — must be lowercase alphanumeric with dashes".format(name),
            suggestion="Example: rhoai-v3-5-ea-2")
    return name


def validate_component_name(name):
    if not name or not name.strip():
        from api.errors import validation_error
        validation_error('Component name cannot be empty',
                         suggestion="Run 'ic get alerts' to see current components")
    name = name.strip()
    if not _COMPONENT_PATTERN.match(name):
        from api.errors import validation_error
        validation_error(
            "Invalid component name: '{}' — must be lowercase alphanumeric with dashes".format(name),
            suggestion="Example: odh-operator-v3-5-ea-2")
    return name


def validate_jira_key(key):
    if not key or not key.strip():
        from api.errors import validation_error
        validation_error('Jira key cannot be empty',
                         suggestion="Example: RHOAIENG-12345")
    key = key.strip().upper()
    if not _JIRA_PATTERN.match(key):
        from api.errors import validation_error
        validation_error(
            "Invalid Jira key: '{}' — must be PROJECT-NUMBER format".format(key),
            suggestion="Example: RHOAIENG-12345")
    return key


def validate_skill_name(name):
    if not name or not name.strip():
        from api.errors import validation_error
        validation_error('Skill name cannot be empty',
                         suggestion="Run 'ic skills list' to see available skills")
    name = name.strip()
    if not _SKILL_PATTERN.match(name):
        from api.errors import validation_error
        validation_error(
            "Invalid skill name: '{}' — must be alphanumeric with dashes/underscores".format(name),
            suggestion="Example: validate-component-onboarding-jira")
    return name
