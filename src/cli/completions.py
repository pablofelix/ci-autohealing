"""Shell completion helpers for Click commands.

Provides dynamic completion for component names, application names,
and other values from the API or local DB.
"""

import click


class ComponentComplete(click.ParamType):
    """Click parameter type with component name completion."""
    name = 'component'

    def shell_complete(self, ctx, param, incomplete):
        try:
            from cli.mode import has_api
            if has_api():
                from cli.data import get_alerts
                alerts = get_alerts()
                comps = [f.get('component', '') for f in
                         alerts.get('build_failures', []) + alerts.get('conforma_violations', [])]
            else:
                from cli.db import check_db, get_repo
                if check_db():
                    from repositories.build_failure_repository import BuildFailureRepository
                    repo = get_repo(BuildFailureRepository)
                    triage = repo.get_triage_summary('')
                    comps = [c['component'] for c in triage.get('failing_components', [])]
                else:
                    comps = []
            return [
                click.shell_completion.CompletionItem(c)
                for c in comps if c.startswith(incomplete)
            ]
        except Exception:
            return []
