"""IC CLI entry point — Click-based command tree."""

import json as json_mod
import os
import subprocess
import sys

import click

from cli import config as cfg


@click.group(invoke_without_command=True)
@click.option('--json', 'output_json', is_flag=True, help='Output as JSON')
@click.pass_context
def cli(ctx, output_json):
    """IC — kubectl-style interface for CI Auto-Healing."""
    ctx.ensure_object(dict)
    ctx.obj['json'] = output_json
    if ctx.invoked_subcommand is None:
        _bash_fallback(['help'])


@cli.command('help')
def help_cmd():
    """Show full usage information."""
    _bash_fallback(['help'])


# --- get group ---

@cli.group()
@click.option('--json', 'output_json', is_flag=True, help='Output as JSON')
@click.pass_context
def get(ctx, output_json):
    """List resources (components, alerts, conforma, ...)."""
    ctx.ensure_object(dict)
    ctx.obj['json'] = ctx.obj.get('json') or output_json


@get.command('components')
@click.option('-r', '--refresh', is_flag=True, help='Refresh from cluster')
@click.option('--json', 'output_json', is_flag=True, hidden=True)
@click.pass_context
def get_components(ctx, refresh, output_json):
    """List currently failing components."""
    args = ['get', 'components']
    if refresh:
        args.append('--refresh')
    if output_json or ctx.obj.get('json'):
        args.append('--json')
    _bash_fallback(args)


@get.command('component')
@click.argument('name')
@click.option('--json', 'output_json', is_flag=True, hidden=True)
@click.pass_context
def get_component(ctx, name, output_json):
    """Get component summary."""
    is_json = output_json or ctx.obj.get('json')
    from cli.db import get_repo, require_db
    from repositories.build_failure_repository import BuildFailureRepository
    if not require_db():
        return
    summary = get_repo(BuildFailureRepository).get_component_summary(name)
    if not summary:
        if is_json:
            print(json_mod.dumps({"error": "Component not found", "component": name}))
        else:
            from cli.formatting import red
            print(red('Error: Component not found: {}'.format(name)))
        raise SystemExit(1)
    if is_json:
        print(json_mod.dumps({"component": name, **summary}, default=str))
        return
    from cli.formatting import bold, cyan
    print(bold('Component: ') + cyan(name))
    print()
    print(bold('Summary:'))
    print('  Total failures: {}'.format(summary['total']))
    print('  With logs: {}'.format(summary['with_logs']))
    print('  First seen: {}'.format(summary['first_seen']))
    print('  Last seen: {}'.format(summary['last_seen']))
    print()
    print(cyan('For full details with logs:') + ' ic describe component {}'.format(name))
    print()


@get.command('alerts')
@click.option('--group', is_flag=True, help='Group by root cause')
@click.option('--all', 'show_all', is_flag=True, help='All applications')
@click.option('--date', help='Alerts on specific date')
@click.option('--from', 'from_date', help='Start date for range')
@click.option('--to', 'to_date', help='End date for range')
@click.option('--json', 'output_json', is_flag=True, hidden=True)
@click.pass_context
def get_alerts(ctx, group, show_all, date, from_date, to_date, output_json):
    """Unified view: build failures + Conforma violations."""
    args = ['get', 'alerts']
    if group:
        args.append('--group')
    if show_all:
        args.append('--all')
    if date:
        args.extend(['--date', date])
    if from_date:
        args.extend(['--from', from_date])
    if to_date:
        args.extend(['--to', to_date])
    if output_json or ctx.obj.get('json'):
        args.append('--json')
    _bash_fallback(args)


@get.command('conforma')
@click.argument('filter', required=False, default='')
@click.option('--json', 'output_json', is_flag=True, hidden=True)
@click.pass_context
def get_conforma(ctx, filter, output_json):
    """Conforma test failures."""
    args = ['get', 'conforma']
    if filter:
        args.append(filter)
    if output_json or ctx.obj.get('json'):
        args.append('--json')
    _bash_fallback(args)


@get.command('exceptions')
@click.option('--json', 'output_json', is_flag=True, hidden=True)
@click.pass_context
def get_exceptions(ctx, output_json):
    """Policy exceptions expiring/expired."""
    args = ['get', 'exceptions']
    if output_json or ctx.obj.get('json'):
        args.append('--json')
    _bash_fallback(args)


@get.command('bindings')
@click.option('--json', 'output_json', is_flag=True, hidden=True)
@click.pass_context
def get_bindings(ctx, output_json):
    """RPA -> EC policy mappings."""
    args = ['get', 'bindings']
    if output_json or ctx.obj.get('json'):
        args.append('--json')
    _bash_fallback(args)


@get.command('policy-gap')
@click.option('--json', 'output_json', is_flag=True, hidden=True)
@click.pass_context
def get_policy_gap(ctx, output_json):
    """Stage vs prod EC policy gap (release risk)."""
    args = ['get', 'policy-gap']
    if output_json or ctx.obj.get('json'):
        args.append('--json')
    _bash_fallback(args)


@get.command('pipelineruns')
@click.option('--limit', type=int, help='Limit results')
@click.option('--json', 'output_json', is_flag=True, hidden=True)
@click.pass_context
def get_pipelineruns(ctx, limit, output_json):
    """List PipelineRun failures."""
    args = ['get', 'pipelineruns']
    if limit:
        args.extend(['--limit', str(limit)])
    if output_json or ctx.obj.get('json'):
        args.append('--json')
    _bash_fallback(args)


@get.command('pipelinerun')
@click.argument('name')
@click.option('--json', 'output_json', is_flag=True, hidden=True)
@click.pass_context
def get_pipelinerun(ctx, name, output_json):
    """Get PipelineRun details."""
    args = ['get', 'pipelinerun', name]
    if output_json or ctx.obj.get('json'):
        args.append('--json')
    _bash_fallback(args)


@get.command('apps')
@click.option('--json', 'output_json', is_flag=True, hidden=True)
@click.pass_context
def get_apps(ctx, output_json):
    """List available RHOAI applications."""
    is_json = output_json or ctx.obj.get('json')
    from cli.db import get_repo, require_db
    from repositories.build_failure_repository import BuildFailureRepository
    if not require_db():
        return
    build_repo = get_repo(BuildFailureRepository)
    apps = []
    try:
        from openshift_auth import _ensure_k8s_config
        from kubernetes import client as k8s
        _ensure_k8s_config()
        api = k8s.CustomObjectsApi()
        result = api.list_namespaced_custom_object(
            group='appstudio.redhat.com', version='v1alpha1',
            namespace=cfg.NAMESPACE, plural='applications',
        )
        app_prefix = cfg.APPLICATION_NAME.rsplit('-v', 1)[0] if cfg.APPLICATION_NAME else ''
        apps = sorted(
            [item['metadata']['name'] for item in result.get('items', [])
             if app_prefix and app_prefix in item['metadata']['name']],
        )
    except Exception:
        if not is_json:
            from cli.formatting import yellow
            print(yellow('Warning: Cluster unreachable — showing applications from database'))
            print()
        apps = [a['application'] for a in build_repo.get_applications()]
    if not apps:
        if is_json:
            print(json_mod.dumps({"applications": [], "current": cfg.APPLICATION_NAME}))
        else:
            from cli.formatting import yellow
            print(yellow('No applications found'))
        return
    app_counts = {a['application']: a['count'] for a in build_repo.get_applications()}
    current = cfg.APPLICATION_NAME
    if is_json:
        result = {
            "applications": [
                {"name": app, "records": app_counts.get(app, 0), "current": app == current}
                for app in apps
            ],
            "current": current,
        }
        print(json_mod.dumps(result, default=str))
        return
    from cli.formatting import bold, cyan, green, section_header
    section_header('Available RHOAI Applications')
    print()
    for app in apps:
        db_count = app_counts.get(app, 0)
        if app == current:
            print('  {} {} — {} records'.format(green('✓'), bold(app) + ' ' + cyan('(current)'), db_count))
        else:
            print('    {} — {} records'.format(app, db_count))
    print()
    print('Total: {} applications'.format(len(apps)))
    print()
    print(cyan('Switch:') + ' ic config set-app <app-name>')
    print()


@get.command('releases')
@click.option('--stage', is_flag=True)
@click.option('--prod', is_flag=True)
@click.option('--limit', type=int)
@click.option('--json', 'output_json', is_flag=True, hidden=True)
@click.argument('name', required=False)
@click.pass_context
def get_releases(ctx, stage, prod, limit, output_json, name):
    """List Release CRs."""
    if name:
        args = ['get', 'releases', name]
    else:
        args = ['get', 'releases']
    if stage:
        args.append('--stage')
    if prod:
        args.append('--prod')
    if limit:
        args.extend(['--limit', str(limit)])
    if output_json or ctx.obj.get('json'):
        args.append('--json')
    _bash_fallback(args)


@get.command('jira')
@click.argument('component', required=False, default='')
@click.option('--json', 'output_json', is_flag=True, hidden=True)
@click.pass_context
def get_jira(ctx, component, output_json):
    """Show Jira ticket links."""
    args = ['get', 'jira']
    if component:
        args.append(component)
    if output_json or ctx.obj.get('json'):
        args.append('--json')
    _bash_fallback(args)


@get.command('fixes')
@click.option('--all', 'show_all', is_flag=True)
@click.option('--json', 'output_json', is_flag=True, hidden=True)
@click.argument('component', required=False, default='')
@click.pass_context
def get_fixes(ctx, show_all, output_json, component):
    """List fix attempts."""
    args = ['get', 'fixes']
    if component:
        args.append(component)
    if show_all:
        args.append('--all')
    if output_json or ctx.obj.get('json'):
        args.append('--json')
    _bash_fallback(args)


@get.command('vulnerabilities')
@click.option('--component', help='Filter to a single component')
@click.option('--severity', type=click.Choice(['critical', 'high']),
              help='Show only this severity and above')
@click.option('--json', 'output_json', is_flag=True, hidden=True)
@click.pass_context
def get_vulnerabilities(ctx, component, severity, output_json):
    """CVE vulnerability summary from SARIF scans."""
    is_json = output_json or ctx.obj.get('json')
    try:
        from clients.konflux_client import KonfluxClient
        from clients.registry_client import RegistryClient
        kc = KonfluxClient(namespace=cfg.NAMESPACE)
        snapshots = kc.get_snapshots(app_filter=cfg.APPLICATION_NAME, limit=1)
        if not snapshots:
            if is_json:
                print(json_mod.dumps({"error": "No snapshots found", "application": cfg.APPLICATION_NAME}))
            else:
                from cli.formatting import yellow
                print(yellow('No snapshots found for {}'.format(cfg.APPLICATION_NAME)))
            return
        snap = snapshots[0]
        snap_name = snap.get('metadata', {}).get('name', 'unknown')
        components = snap.get('spec', {}).get('components', [])
        if not is_json:
            from cli.formatting import section_header, cyan
            section_header('Vulnerability Summary — {}'.format(cfg.APPLICATION_NAME))
            print()
            print('Snapshot: {}'.format(cyan(snap_name)))
            print('Components: {}'.format(len(components)))
        print()

        rc = RegistryClient()
        totals = {'critical': 0, 'high': 0, 'medium': 0, 'low': 0}
        rows = []
        severity_map = {'error': 'critical', 'warning': 'high', 'note': 'medium'}

        if component:
            components = [c for c in components if component in c.get('name', '')]
        batch = rc.fetch_sarif_batch(components, timeout=60)

        for name, results in batch.items():
            if not results:
                continue

            counts = {'critical': 0, 'high': 0, 'medium': 0, 'low': 0}
            top_cves = []
            for r in results:
                sev = severity_map.get(r.get('level', ''), 'low')
                counts[sev] += 1
                if len(top_cves) < 5:
                    rule_id = r.get('ruleId', '')
                    if rule_id and rule_id not in top_cves:
                        top_cves.append(rule_id)

            for sev in totals:
                totals[sev] += counts[sev]
            rows.append({'name': name, 'counts': counts, 'total': sum(counts.values()),
                         'top_cves': top_cves})

        if severity:
            min_levels = {'critical': ['critical'], 'high': ['critical', 'high']}
            levels = min_levels[severity]
            rows = [r for r in rows if any(r['counts'].get(lv, 0) > 0 for lv in levels)]

        rows.sort(key=lambda r: r['counts']['critical'] * 1000 + r['counts']['high'], reverse=True)

        if ctx.obj.get('json'):
            result = {
                "application": cfg.APPLICATION_NAME,
                "snapshot": snap_name,
                "totals": totals,
                "components": [
                    {"name": r['name'], **r['counts'], "total": r['total'],
                     "top_findings": r['top_cves']}
                    for r in rows
                ],
            }
            print(json_mod.dumps(result, default=str))
            return

        from cli.formatting import bold, red, yellow, dim
        if not rows:
            print(yellow('No vulnerabilities found.'))
            return

        fmt = '  {:<50s} {:>5s} {:>5s} {:>5s} {:>5s} {:>6s}'
        print(fmt.format('COMPONENT', 'CRIT', 'HIGH', 'MED', 'LOW', 'TOTAL'))
        print('  ' + '-' * 80)
        for r in rows:
            c = r['counts']
            crit_str = str(c['critical']) if c['critical'] else '.'
            high_str = str(c['high']) if c['high'] else '.'
            med_str = str(c['medium']) if c['medium'] else '.'
            low_str = str(c['low']) if c['low'] else '.'
            crit_str = red(crit_str) if c['critical'] > 0 else crit_str
            high_str = yellow(high_str) if c['high'] > 0 else high_str
            print(fmt.format(r['name'][:50], crit_str, high_str, med_str, low_str, str(r['total'])))

        print()
        print(bold('Totals:') + ' {} critical, {} high, {} medium, {} low'.format(
            red(str(totals['critical'])) if totals['critical'] else '0',
            yellow(str(totals['high'])) if totals['high'] else '0',
            totals['medium'], totals['low']))

        all_cves = []
        for r in rows:
            for cve in r['top_cves']:
                if cve not in [c[0] for c in all_cves]:
                    sev = 'critical' if r['counts']['critical'] > 0 else 'high'
                    all_cves.append((cve, sev, r['name']))
        if all_cves:
            print()
            print(bold('Top findings:'))
            for cve, sev, comp_name in all_cves[:10]:
                sev_str = red(sev) if sev == 'critical' else yellow(sev)
                print('  {} ({}) — {}'.format(cve, sev_str, comp_name))
        print()
        print(dim('Note: Severity is mapped from SARIF levels (error/warning/note). Includes SAST'))
        print(dim('findings (ShellCheck, Snyk Code) — not only CVEs. Filter with --severity critical.'))
    except ImportError:
        print('Error: Required clients not available (registry_client, konflux_client)',
              file=sys.stderr)
        raise SystemExit(1)
    except Exception as e:
        print('Error: {}'.format(e), file=sys.stderr)
        raise SystemExit(1)


@get.command('cves', hidden=True)
@click.option('--component', help='Filter to a single component')
@click.option('--severity', type=click.Choice(['critical', 'high']))
@click.pass_context
def get_cves(ctx, component, severity):
    """Alias for vulnerabilities."""
    ctx.invoke(get_vulnerabilities, component=component, severity=severity)


# --- describe group ---

@cli.group()
@click.option('--json', 'output_json', is_flag=True, help='Output as JSON')
@click.pass_context
def describe(ctx, output_json):
    """Show detailed resource information."""
    ctx.ensure_object(dict)
    ctx.obj['json'] = ctx.obj.get('json') or output_json


@describe.command('component')
@click.argument('name')
@click.option('--log', is_flag=True, help='Show complete logs')
@click.option('--json', 'output_json', is_flag=True, hidden=True)
@click.pass_context
def describe_component(ctx, name, log, output_json):
    """Full component details + logs."""
    args = ['describe', 'component', name]
    if log:
        args.append('--log')
    if output_json or ctx.obj.get('json'):
        args.append('--json')
    _bash_fallback(args)


@describe.command('conforma')
@click.argument('name')
@click.option('--json', 'output_json', is_flag=True, hidden=True)
@click.pass_context
def describe_conforma(ctx, name, output_json):
    """Conforma violation details."""
    args = ['describe', 'conforma', name]
    if output_json or ctx.obj.get('json'):
        args.append('--json')
    _bash_fallback(args)


@describe.command('pipelinerun')
@click.argument('name')
@click.option('--json', 'output_json', is_flag=True, hidden=True)
@click.pass_context
def describe_pipelinerun(ctx, name, output_json):
    """Full PipelineRun details + logs."""
    args = ['describe', 'pipelinerun', name]
    if output_json or ctx.obj.get('json'):
        args.append('--json')
    _bash_fallback(args)


@describe.command('jira')
@click.argument('key')
@click.option('--json', 'output_json', is_flag=True, hidden=True)
@click.pass_context
def describe_jira(ctx, key, output_json):
    """Show Jira ticket details."""
    args = ['describe', 'jira', key]
    if output_json or ctx.obj.get('json'):
        args.append('--json')
    _bash_fallback(args)


@describe.command('release')
@click.argument('name')
@click.option('--logs', is_flag=True)
@click.option('--json', 'output_json', is_flag=True, hidden=True)
@click.pass_context
def describe_release(ctx, name, logs, output_json):
    """Release CR details."""
    args = ['describe', 'release', name]
    if logs:
        args.append('--logs')
    if output_json or ctx.obj.get('json'):
        args.append('--json')
    _bash_fallback(args)


# --- config group ---

@cli.group(invoke_without_command=True)
@click.pass_context
def config(ctx):
    """Show or modify configuration."""
    if ctx.invoked_subcommand is None:
        from cli.db import check_db, get_repo
        from cli.formatting import bold, cyan, section_header
        section_header('Current Configuration')
        print()
        print(bold('Environment:'))
        print('  Namespace: {}'.format(cyan(cfg.NAMESPACE)))
        print('  Application: {}'.format(cyan(cfg.APPLICATION_NAME)))
        print('  Config: {}/.env'.format(cfg.PROJECT_DIR))
        print()
        if check_db():
            from repositories.build_failure_repository import BuildFailureRepository
            s = get_repo(BuildFailureRepository).get_overview_stats()
            print(bold('Database:'))
            print('  Failures: {}'.format(s['total']))
            print('  Components: {}'.format(s['components']))
            print()
        print(cyan('Commands:'))
        print('  ic config set-app <app>  # Change application')
        print('  ic get apps              # List applications')
        print()


@config.command('set-app')
@click.argument('app_name')
@click.option('--force', '-f', is_flag=True, help='Set even if not in DB')
def config_set_app(app_name, force):
    """Set active application."""
    from cli.db import check_db, get_repo
    from cli.formatting import cyan, green, red
    if check_db():
        from repositories.build_failure_repository import BuildFailureRepository
        s = get_repo(BuildFailureRepository).get_overview_stats(app_name)
        if s['total'] == 0 and not force:
            print(red("Error: Application '{}' not found in database".format(app_name)))
            print()
            _bash_fallback(['get', 'apps'])
            return
    env_file = os.path.join(str(cfg.PROJECT_DIR), '.env')
    lines = []
    found = False
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                if line.startswith('APPLICATION_NAME='):
                    lines.append('APPLICATION_NAME={}\n'.format(app_name))
                    found = True
                else:
                    lines.append(line)
    if not found:
        lines.append('APPLICATION_NAME={}\n'.format(app_name))
    with open(env_file, 'w') as f:
        f.writelines(lines)
    cfg.APPLICATION_NAME = app_name
    os.environ['APPLICATION_NAME'] = app_name
    print(green('✓ Application set to: ') + cyan(app_name))
    if check_db():
        from cli.db import _get_db_connection
        with _get_db_connection().connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM sync_status WHERE application = %s", (app_name,))
            has_cache = cursor.fetchone()[0]
        if not has_cache:
            print('  No cached data yet. Run {} to sync.'.format(cyan('ic get components -r')))
        else:
            print('  Cached sync data available. Run {} to view.'.format(cyan('ic get components')))
    print()


# --- config watch subgroup ---

@config.group('watch', invoke_without_command=True)
@click.pass_context
def config_watch(ctx):
    """Manage watched applications and watcher toggles."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(config_watch_list)


@config_watch.command('list')
def config_watch_list():
    """Show watched applications and watcher status."""
    from cli.formatting import bold, cyan, green, red, dim
    from config import ALL_WATCHERS

    apps = os.environ.get('WATCH_APPLICATIONS', '').split()
    if not apps:
        fallback = os.environ.get('APPLICATION_NAME', '')
        apps = [fallback] if fallback else []

    disabled = set(os.environ.get('WATCH_DISABLE', '').split())

    print()
    print(bold('Watch Daemon Configuration'))
    print()
    print(bold('  Applications:'))
    if apps:
        for app in apps:
            print('    {}'.format(cyan(app)))
    else:
        print('    {}'.format(dim('(none — set WATCH_APPLICATIONS or APPLICATION_NAME)')))

    print()
    print(bold('  Watchers:'))
    print('    {:<16s} {:<10s} {}'.format('Name', 'Status', 'Type'))
    print('    {}'.format('-' * 44))
    for w in ALL_WATCHERS:
        if w in disabled:
            status = red('disabled')
        else:
            status = green('enabled')
        if w == 'jira':
            wtype = 'Jira API poll'
        elif w == 'conforma':
            wtype = 'K8s watch (not yet implemented)'
        else:
            wtype = 'K8s watch'
        print('    {:<16s} {:<19s} {}'.format(w, status, dim(wtype)))

    print()
    print(dim('  Commands:'))
    print(dim('    ic config watch add <app>       Add application'))
    print(dim('    ic config watch remove <app>    Remove application'))
    print(dim('    ic config watch enable <name>   Enable a watcher'))
    print(dim('    ic config watch disable <name>  Disable a watcher'))
    print()


@config_watch.command('add')
@click.argument('app_name')
@click.option('--force', '-f', is_flag=True, help='Add even if not in DB')
def config_watch_add(app_name, force):
    """Add application to watch list."""
    from cli.db import check_db, get_repo
    from cli.formatting import cyan, green, red, yellow

    if check_db() and not force:
        from repositories.build_failure_repository import BuildFailureRepository
        s = get_repo(BuildFailureRepository).get_overview_stats(app_name)
        if s['total'] == 0:
            print(red("Error: Application '{}' not found in database".format(app_name)))
            print('  Use {} to add anyway.'.format(cyan('--force')))
            raise SystemExit(1)

    current = os.environ.get('WATCH_APPLICATIONS', '').split()
    if app_name in current:
        print(yellow('Already watching: ') + cyan(app_name))
        return

    current.append(app_name)
    _update_env_var('WATCH_APPLICATIONS', ' '.join(current))
    print(green('✓ Added to watch list: ') + cyan(app_name))


@config_watch.command('remove')
@click.argument('app_name')
def config_watch_remove(app_name):
    """Remove application from watch list."""
    from cli.formatting import cyan, green, red

    current = os.environ.get('WATCH_APPLICATIONS', '').split()
    if app_name not in current:
        print(red("Not in watch list: ") + cyan(app_name))
        raise SystemExit(1)

    current.remove(app_name)
    _update_env_var('WATCH_APPLICATIONS', ' '.join(current))
    print(green('✓ Removed from watch list: ') + cyan(app_name))


@config_watch.command('enable')
@click.argument('watcher_name')
def config_watch_enable(watcher_name):
    """Enable a watcher (builds, tests, conforma, jira, components)."""
    from cli.formatting import cyan, green, red
    from config import ALL_WATCHERS

    if watcher_name not in ALL_WATCHERS:
        print(red("Unknown watcher: ") + cyan(watcher_name))
        print('  Available: {}'.format(', '.join(ALL_WATCHERS)))
        raise SystemExit(1)

    disabled = set(os.environ.get('WATCH_DISABLE', '').split())
    if watcher_name not in disabled:
        print(green('Already enabled: ') + cyan(watcher_name))
        return

    disabled.discard(watcher_name)
    _update_env_var('WATCH_DISABLE', ' '.join(sorted(disabled)))
    print(green('✓ Enabled watcher: ') + cyan(watcher_name))


@config_watch.command('disable')
@click.argument('watcher_name')
def config_watch_disable(watcher_name):
    """Disable a watcher (builds, tests, conforma, jira, components)."""
    from cli.formatting import cyan, green, red, yellow
    from config import ALL_WATCHERS

    if watcher_name not in ALL_WATCHERS:
        print(red("Unknown watcher: ") + cyan(watcher_name))
        print('  Available: {}'.format(', '.join(ALL_WATCHERS)))
        raise SystemExit(1)

    disabled = set(os.environ.get('WATCH_DISABLE', '').split())
    if watcher_name in disabled:
        print(yellow('Already disabled: ') + cyan(watcher_name))
        return

    disabled.add(watcher_name)
    _update_env_var('WATCH_DISABLE', ' '.join(sorted(disabled)))
    print(green('✓ Disabled watcher: ') + cyan(watcher_name))


def _update_env_var(var_name, value):
    """Update a variable in .env file and current environment."""
    import tempfile
    env_file = os.path.join(str(cfg.PROJECT_DIR), '.env')
    lines = []
    found = False
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                if line.startswith('{}='.format(var_name)):
                    lines.append('{}={}\n'.format(var_name, value))
                    found = True
                else:
                    lines.append(line)
    if not found:
        lines.append('{}={}\n'.format(var_name, value))
    env_dir = os.path.dirname(env_file)
    os.makedirs(env_dir, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=env_dir, suffix='.tmp', text=True,
    )
    try:
        with os.fdopen(tmp_fd, 'w') as f:
            f.writelines(lines)
        os.replace(tmp_path, env_file)
    except Exception:
        try:
            os.close(tmp_fd)
        except OSError:
            pass
        os.unlink(tmp_path)
        raise
    os.environ[var_name] = value


# --- stats group ---

@cli.group(invoke_without_command=True)
@click.pass_context
def stats(ctx):
    """Statistics."""
    if ctx.invoked_subcommand is None:
        from cli.db import get_repo, print_table, require_db
        from cli.formatting import section_header
        from repositories.build_failure_repository import BuildFailureRepository
        if not require_db():
            return
        s = get_repo(BuildFailureRepository).get_overview_stats()
        section_header('Statistics')
        print()
        print_table(
            ['Total Failures', 'Components', 'With Logs'],
            [(s['total'], s['components'], s['with_logs'])],
        )
        print()


@stats.command('daily')
def stats_daily():
    """Failures by day."""
    from cli.db import get_repo, print_table, require_db
    from cli.formatting import section_header
    from repositories.build_failure_repository import BuildFailureRepository
    if not require_db():
        return
    rows = get_repo(BuildFailureRepository).get_daily_stats(cfg.APPLICATION_NAME)
    section_header('Failures by Day')
    print()
    print_table(['Date', 'Failures'], [(r['date'], r['count']) for r in rows])
    print()


@stats.command('resolved')
def stats_resolved():
    """Resolved failures statistics."""
    from cli.db import get_repo, print_table, require_db
    from cli.formatting import section_header
    from repositories.build_failure_repository import BuildFailureRepository
    if not require_db():
        return
    s = get_repo(BuildFailureRepository).get_resolved_stats(cfg.APPLICATION_NAME)
    section_header('Resolved Failures Statistics')
    print()
    print_table(
        ['Total Records', 'Resolved', 'Unresolved', 'Successes'],
        [(s['total'], s['resolved'], s['unresolved'], s['successes'])],
    )
    print()


@stats.command('components')
def stats_components():
    """Failures by component."""
    _bash_fallback(['get', 'components'])


# --- ai group ---

@cli.group(invoke_without_command=True)
@click.pass_context
def ai(ctx):
    """AI analysis commands."""
    if ctx.invoked_subcommand is None:
        _bash_fallback(['ai'])


@ai.command('analyze')
@click.argument('args', nargs=-1)
def ai_analyze(args):
    """Analyze a failure with AI."""
    _bash_fallback(['ai', 'analyze'] + list(args))


@ai.command('batch')
@click.argument('args', nargs=-1)
def ai_batch(args):
    """Batch AI analysis."""
    _bash_fallback(['ai', 'batch'] + list(args))


@ai.command('status')
def ai_status():
    """Show AI analysis summary."""
    from cli.db import get_repo, print_table, require_db
    from cli.formatting import bold, cyan, green, section_header
    from repositories.ai_analysis_repository import AIAnalysisRepository
    if not require_db():
        return
    app = cfg.APPLICATION_NAME
    ai_repo = get_repo(AIAnalysisRepository)
    status = ai_repo.get_extended_status(app)
    section_header('AI Analysis Status')
    print()

    b = status['build']
    print(bold('Build Failures:'))
    print('  Pending:        {} awaiting analysis'.format(b['pending']))
    if b['no_logs'] > 0:
        print('  No logs yet:    {} (waiting for commit context collector)'.format(b['no_logs']))
    if b['skipped'] > 0:
        print('  Skipped:        {} (max retries or no-logs timeout)'.format(b['skipped']))
    print('  Analyzed:       {} (last 30 days)'.format(b['analyzed']))
    if b['analyzed'] > 0:
        bh = b['analyzed'] - b['low_confidence']
        print('    High conf (>=70%): {}'.format(bh))
        if b['low_confidence'] > 0:
            print('    Low conf  (<70%):  {}  <- needs human review'.format(b['low_confidence']))
        bpct = b['auto_fixable'] * 100 // max(b['analyzed'], 1)
        print('  Auto-fixable:   {} ({}%)'.format(b['auto_fixable'], bpct))

    print()
    c = status['conforma']
    print(bold('Conforma Violations:'))
    print('  Pending:        {} awaiting analysis'.format(c['pending']))
    if c['skipped'] > 0:
        print('  Skipped:        {} (max retries)'.format(c['skipped']))
    print('  Analyzed:       {} (last 30 days)'.format(c['analyzed']))
    if c['analyzed'] > 0:
        ch = c['analyzed'] - c['low_confidence']
        print('    High conf (>=70%): {}'.format(ch))
        if c['low_confidence'] > 0:
            print('    Low conf  (<70%):  {}  <- needs human review'.format(c['low_confidence']))
        cpct = c['auto_fixable'] * 100 // max(c['analyzed'], 1)
        print('  Auto-fixable:   {} ({}%)'.format(c['auto_fixable'], cpct))

    print()
    print(bold('Total cost:') + '   ${} (last 30 days)'.format(status['total_cost']))
    print()

    recent = ai_repo.get_recent_analyses(app)
    if recent:
        print_table(
            ['#', 'Component', 'Type', 'Category', 'Confidence', 'Fixable'],
            [(i + 1, r['component'], r['type'], r['category'],
              '{}%'.format(r['confidence']), '✓' if r['auto_fixable'] else '✗')
             for i, r in enumerate(recent)],
        )
        print()

    total_pending = b['pending'] + c['pending']
    if total_pending > 0:
        print(cyan("Run 'ic ai analyze --all' to analyze pending failures and violations"))
    else:
        print(green('✓ No pending failures or violations to analyze'))
    print()


@ai.command('stats')
@click.option('--fixes', is_flag=True)
def ai_stats(fixes):
    """Detailed AI statistics."""
    if fixes:
        _bash_fallback(['ai', 'stats', '--fixes'])
        return
    from cli.db import get_repo, print_table, require_db
    from cli.formatting import bold, section_header
    from repositories.ai_analysis_repository import AIAnalysisRepository
    if not require_db():
        return
    app = cfg.APPLICATION_NAME
    data = get_repo(AIAnalysisRepository).get_category_stats(app)
    section_header('AI Analysis Statistics')
    print()
    print(bold('Analyses by Category (last 30 days):'))
    print_table(
        ['Type', 'Category', 'Count', 'Avg Confidence', 'Auto-fixable'],
        [(r['type'], r['category'], r['count'],
          '{}%'.format(r['avg_confidence']) if r['avg_confidence'] else '-',
          r['auto_fixable'])
         for r in data['by_category']],
    )
    print()
    print(bold('Analyses by Date (last 7 days):'))
    print_table(
        ['Date', 'Analyzed', 'Auto-fixable', 'Cost USD'],
        [(r['date'], r['analyzed'], r['auto_fixable'], r['cost'])
         for r in data['by_date']],
    )
    print()
    print(bold('Token Usage (last 30 days):'))
    print('  Total tokens: {}'.format(data['total_tokens']))
    print('  Avg per analysis: {} tokens'.format(data['avg_tokens']))
    print()


# --- Top-level commands ---

@cli.command()
def triage():
    """Currently failing components overview."""
    from cli.db import get_repo, print_table, require_db
    from cli.formatting import bold, cyan, green, red, section_header
    from repositories.build_failure_repository import BuildFailureRepository
    if not require_db():
        return
    app = cfg.APPLICATION_NAME
    data = get_repo(BuildFailureRepository).get_triage_summary(app)
    section_header('Triage Dashboard')
    print()
    print(bold('Overview:'))
    print('  Total build records: {}'.format(data['total']))
    print('  Currently failing: {}'.format(red(str(data['failing']))))
    print('  Currently working: {}'.format(green(str(data['working']))))
    print()
    print(bold(red('Currently Failing Components:')))
    print_table(
        ['Component', 'Last Failure', 'Has Logs'],
        [(c['component'], c['last_failure'],
          'Yes' if c['has_logs'] else 'No')
         for c in data['failing_components']],
    )
    print()
    print(cyan('Next Steps:'))
    print('  1. Pick a component from above')
    print('  2. Run: {} for full details'.format(bold('ic describe component <name>')))
    print('  3. Run: {} for complete logs'.format(bold('ic describe component <name> --log')))
    print('  4. Use: {} to see components that are currently working'.format(bold('ic working')))
    print()


@cli.command()
def resolved():
    """Resolved components."""
    from cli.db import get_repo, print_table, require_db
    from cli.formatting import cyan, section_header
    from repositories.build_failure_repository import BuildFailureRepository
    if not require_db():
        return
    rows = get_repo(BuildFailureRepository).get_resolved_components(cfg.APPLICATION_NAME)
    section_header('Resolved Components')
    print()
    print_table(
        ['Component', 'Resolved At', 'Failures Fixed'],
        [(r['component'], r['resolved_at'], r['count']) for r in rows],
    )
    print()
    print(cyan('Tip:') + " Use 'ic history <component>' to see full history")
    print()


@cli.command()
def working():
    """Components currently working."""
    from cli.db import get_repo, print_table, require_db
    from cli.formatting import bold, cyan, green, section_header
    from repositories.build_failure_repository import BuildFailureRepository
    if not require_db():
        return
    rows = get_repo(BuildFailureRepository).get_working_components(cfg.APPLICATION_NAME)
    section_header('Currently Working Components')
    print()
    print(bold(green('Components with last build = Succeeded:')))
    print_table(
        ['Component', 'Commit', 'Message', 'Last Build'],
        [(r['component'], r['commit'], r['message'], r['date']) for r in rows],
    )
    print()
    print(cyan('Links:'))
    print('  Use: {} to see full build history'.format(bold('ic history <component>')))
    print('  Use: {} to see currently failing components'.format(bold('ic triage')))
    print()


@cli.command()
@click.argument('component', required=False)
def history(component):
    """Build history for a component."""
    if not component:
        from cli.formatting import red
        print(red('Error: Component name required'))
        print('Usage: ic history <component-name>')
        raise SystemExit(1)
    from cli.db import get_repo, print_table, require_db
    from cli.formatting import bold, cyan, green, red, section_header
    from repositories.build_failure_repository import BuildFailureRepository
    if not require_db():
        return
    app = cfg.APPLICATION_NAME
    data = get_repo(BuildFailureRepository).get_component_history(component, app)
    section_header('History: {}'.format(component))
    print()
    s = data['summary']
    print_table(
        ['Total Builds', 'Failures', 'Successes', 'Resolved'],
        [(s['total'], s['failures'], s['successes'], s['resolved'])],
    )
    print()
    print(bold('Recent Builds (Last 20):'))
    print()
    print_table(
        ['PipelineRun', 'Status', 'Resolved', 'Commit', 'Date'],
        [(b['pipelinerun'], b['status'],
          '✓' if b['resolved'] else '', b['commit'], b['date'])
         for b in data['builds']],
    )
    print()
    last_status = data['last_status']
    if last_status == 'Failed':
        print(red('✗ Currently failing') + ' (last build: Failed)')
        print('  Run: {} to investigate'.format(cyan('ic why {}'.format(component))))
    elif last_status == 'Succeeded':
        print(green('✓ Currently working') + ' (last build: Succeeded)')
    else:
        print('? Status unknown (last build: {})'.format(last_status))
    print()


@cli.command()
def dashboard():
    """Full dashboard view."""
    from datetime import datetime
    from cli.db import get_repo, require_db
    from cli.formatting import bold, cyan, green, red, section_header, yellow
    from repositories.ai_analysis_repository import AIAnalysisRepository
    from repositories.build_failure_repository import BuildFailureRepository
    from repositories.error_pattern_repository import ErrorPatternRepository
    from repositories.resolution_attempt_repository import ResolutionAttemptRepository
    if not require_db():
        return
    app = cfg.APPLICATION_NAME
    build_repo = get_repo(BuildFailureRepository)
    ai_repo = get_repo(AIAnalysisRepository)
    pattern_repo = get_repo(ErrorPatternRepository)
    fix_repo = get_repo(ResolutionAttemptRepository)

    section_header('CI Auto-Healing Dashboard')
    print('  Application: {}'.format(cyan(app)))
    print('  Date: {}'.format(datetime.now().strftime('%Y-%m-%d %H:%M')))
    print()

    print(bold('Analysis Queue'))
    bq = build_repo.get_analysis_queue(app)
    cq = ai_repo.get_conforma_queue(app)
    print('  Build:    {} analyzed  {} pending'.format(green(str(bq['analyzed'])), yellow(str(bq['pending']))))
    print('  Conforma: {} analyzed  {} pending'.format(green(str(cq['analyzed'])), yellow(str(cq['pending']))))
    print()

    print(bold('Enrichment Coverage'))
    ec = build_repo.get_enrichment_coverage(app)
    print('  Enriched: {} ({}%)  Failed: {}'.format(
        green('{}/{}'.format(ec['enriched'], ec['total'])), ec['pct'], red(str(ec['failed']))))
    print()

    print(bold('Pattern Library'))
    pl = pattern_repo.get_library_summary()
    print('  Patterns: {} total ({} with confidence data)'.format(pl['total'], pl['with_confidence']))
    print('  Avg confidence: {}  Total occurrences: {}'.format(cyan('{}%'.format(pl['avg_confidence'])), pl['total_occurrences']))
    print()

    print(bold('Fix Outcomes (last 30 days)'))
    fo = fix_repo.get_outcome_summary()
    print('  Total: {}  {} Success: {}  {} Failed: {}  Pending: {}'.format(
        fo['total'], '', green(str(fo['successful'])), '', red(str(fo['failed'])), fo['pending']))
    print('  Success rate: {}'.format(cyan('{}%'.format(fo['rate']))))
    print()

    print(bold('API Costs (last 30 days)'))
    cs = ai_repo.get_cost_summary()
    print('  Analyses: {}  Tokens: {}  Cost: {}'.format(cs['analyses'], cs['tokens'], cyan('${}'.format(cs['cost_usd']))))
    print()


@cli.command()
@click.argument('args', nargs=-1)
def fix(args):
    """Triage a failure: choose PR / Jira / Slack."""
    _bash_fallback(['fix'] + list(args))


@cli.command('export')
@click.argument('args', nargs=-1)
def export_cmd(args):
    """Export analysis to ticket format."""
    _bash_fallback(['export'] + list(args))


# --- conforma group ---

@cli.group(invoke_without_command=True)
@click.pass_context
def conforma(ctx):
    """Conforma (Enterprise Contract) commands."""
    if ctx.invoked_subcommand is None:
        _bash_fallback(['conforma'])


@conforma.command('report')
@click.argument('args', nargs=-1)
def conforma_report(args):
    """Daily Conforma violations report."""
    _bash_fallback(['conforma', 'report'] + list(args))


@conforma.command('categories')
@click.argument('args', nargs=-1)
def conforma_categories(args):
    """Violation categories across components."""
    _bash_fallback(['conforma', 'categories'] + list(args))


@conforma.command('csv')
@click.argument('args', nargs=-1)
def conforma_csv(args):
    """Export Conforma data as CSV."""
    _bash_fallback(['conforma', 'csv'] + list(args))


@conforma.command('snapshot')
@click.argument('args', nargs=-1)
def conforma_snapshot(args):
    """Conforma snapshot analysis."""
    _bash_fallback(['conforma', 'snapshot'] + list(args))


@conforma.command('scenarios')
@click.argument('args', nargs=-1)
def conforma_scenarios(args):
    """List ITS scenarios."""
    _bash_fallback(['conforma', 'scenarios'] + list(args))


# --- release group ---

@cli.group(invoke_without_command=True)
@click.pass_context
def release(ctx):
    """Release commands."""
    if ctx.invoked_subcommand is None:
        _bash_fallback(['release'])


@release.command('status')
@click.argument('args', nargs=-1)
def release_status(args):
    """Release process checklist."""
    _bash_fallback(['release', 'status'] + list(args))


@release.command('diff')
@click.argument('args', nargs=-1)
def release_diff(args):
    """Compare snapshots between releases."""
    _bash_fallback(['release', 'diff'] + list(args))


@release.command('verify')
@click.argument('args', nargs=-1)
def release_verify(args):
    """Check image availability in Pyxis."""
    _bash_fallback(['release', 'verify'] + list(args))


# --- report group ---

@cli.group()
def report():
    """Reports (build, conforma)."""


@report.command('build')
@click.argument('args', nargs=-1)
def report_build(args):
    """Daily build failures standup table."""
    _bash_fallback(['report', 'build'] + list(args))


@report.command('conforma')
@click.argument('args', nargs=-1)
def report_conforma(args):
    """Daily Conforma violations report."""
    _bash_fallback(['conforma', 'report'] + list(args))


# --- health group ---

@cli.group(invoke_without_command=True)
@click.pass_context
def health(ctx):
    """Component health."""
    if ctx.invoked_subcommand is None:
        from cli.db import require_db
        from cli.formatting import section_header
        from proactive.health_monitor import HealthMonitor
        if not require_db():
            return
        section_header('Component Health')
        print()
        try:
            from cli.db import _get_db_connection
            summary = HealthMonitor(_get_db_connection()).get_component_health_summary()
            if not summary:
                print('  No component health data. Run data collection first.')
                return
            by_status = {}
            for s in summary:
                st = s.get('health_status') or 'unknown'
                by_status[st] = by_status.get(st, 0) + 1
            print('  Total components: {}'.format(len(summary)))
            for st in ['critical', 'warning', 'healthy', 'unknown']:
                if st in by_status:
                    print('  {:10s}: {}'.format(st, by_status[st]))
            print()
            unhealthy = [s for s in summary if (s.get('health_status') or '') in ('critical', 'warning')
                         or (s.get('consecutive_failures') or 0) >= 2]
            if unhealthy:
                print('  Unhealthy components:')
                fmt = '  {:<55s} {:>5s} {:>10s} {:>5s} {:>8s}'
                print(fmt.format('COMPONENT', 'SCORE', 'STATUS', 'FAILS', '7D RATE'))
                print('  ' + '-' * 87)
                for s in unhealthy:
                    score = str(s['health_score']) if s['health_score'] is not None else 'N/A'
                    status = s.get('health_status') or 'unknown'
                    fails = str(s.get('consecutive_failures') or 0)
                    rate = '{:.0f}%'.format(s['success_rate_last_7d']) if s.get('success_rate_last_7d') is not None else 'N/A'
                    print(fmt.format(s['component_name'][:55], score, status, fails, rate))
            else:
                print('  All components healthy.')
        except Exception as e:
            print('  Error: {}'.format(e))


@health.command('warnings')
def health_warnings():
    """Health warnings."""
    from cli.db import require_db
    from cli.formatting import section_header
    if not require_db():
        return
    section_header('Proactive Warnings')
    print()
    try:
        from cli.db import _get_db_connection
        from proactive.health_monitor import HealthMonitor
        warnings_list = HealthMonitor(_get_db_connection()).run_checks()
        if not warnings_list:
            print('  No warnings — all clear.')
            return
        by_type = {}
        for w in warnings_list:
            by_type.setdefault(w.signal_type, []).append(w)
        for signal_type, group in by_type.items():
            label = signal_type.replace('_', ' ').title()
            print('  {} ({}):'.format(label, len(group)))
            for w in group:
                icon = '!!' if w.severity == 'critical' else ' >'
                print('    {} {}'.format(icon, w.message))
            print()
        crit = sum(1 for w in warnings_list if w.severity == 'critical')
        warn = sum(1 for w in warnings_list if w.severity == 'warning')
        print('  Summary: {} critical, {} warning'.format(crit, warn))
    except Exception as e:
        print('  Error: {}'.format(e))


# --- jira group ---

@cli.group(invoke_without_command=True)
@click.pass_context
def jira(ctx):
    """Jira ticket commands."""
    if ctx.invoked_subcommand is None:
        _bash_fallback(['jira'])


@jira.command('create')
@click.argument('args', nargs=-1)
def jira_create(args):
    """Create Jira ticket."""
    _bash_fallback(['jira', 'create'] + list(args))


@jira.command('inbox')
@click.argument('args', nargs=-1)
def jira_inbox(args):
    """Check Jira inbox for new comments."""
    _bash_fallback(['jira', 'inbox'] + list(args))


@jira.command('link')
@click.argument('args', nargs=-1)
def jira_link(args):
    """Link Jira ticket to component."""
    _bash_fallback(['jira', 'link'] + list(args))


@jira.command('status')
def jira_status():
    """Show Jira ticket links."""
    _bash_fallback(['jira', 'status'])


# --- patterns group ---

@cli.group(invoke_without_command=True)
@click.pass_context
def patterns(ctx):
    """Error pattern commands."""
    if ctx.invoked_subcommand is None:
        _bash_fallback(['patterns'])


@patterns.command('learn')
def patterns_learn():
    """Learn patterns from analysis."""
    from cli.db import get_repo, require_db
    from cli.formatting import bold
    from repositories.error_pattern_repository import ErrorPatternRepository
    if not require_db():
        return
    print(bold('Discovering new patterns from repeated failures...'))
    try:
        created = get_repo(ErrorPatternRepository).discover_new_patterns()
        if created:
            print('Created {} new pattern(s):'.format(len(created)))
            for p in created:
                print('  - {} (category: {})'.format(p['pattern_name'], p['failure_category']))
        else:
            print('No new patterns to create (need 3+ occurrences of same category)')
    except Exception as e:
        print('Error: {}'.format(e))


@patterns.command('stale')
def patterns_stale():
    """Show stale patterns."""
    from cli.db import get_repo, require_db
    from cli.formatting import bold
    from repositories.error_pattern_repository import ErrorPatternRepository
    if not require_db():
        return
    print(bold('Stale patterns (unused 90+ days or low accuracy):'))
    try:
        stale = get_repo(ErrorPatternRepository).get_stale_patterns()
        if stale:
            for p in stale:
                conf = '{:.2f}'.format(p['avg_confidence']) if p['avg_confidence'] else 'N/A'
                print('  {:30s}  conf={}  last_used={}'.format(
                    p['pattern_name'], conf, p['last_used_at'] or 'never'))
        else:
            print('  No stale patterns found')
    except Exception as e:
        print('  Error: {}'.format(e))


@patterns.command('shared')
def patterns_shared():
    """Shared patterns across components."""
    _bash_fallback(['patterns', 'shared'])


@patterns.command('list')
def patterns_list():
    """List all patterns."""
    from cli.db import get_repo, print_table, require_db
    from cli.formatting import section_header
    from repositories.error_pattern_repository import ErrorPatternRepository
    if not require_db():
        return
    repo = get_repo(ErrorPatternRepository)
    all_patterns = repo.get_all()
    section_header('Error Pattern Library')
    print()
    print_table(
        ['Type', 'Category', 'Seen', 'Avg Conf', 'Doc', 'Last Seen', 'Pattern'],
        [(p['failure_type'], p['failure_category'], p['occurrence_count'],
          '{}%'.format(int(p['avg_confidence'] * 100)) if p.get('avg_confidence') is not None else '  —  ',
          'yes' if p.get('doc_context') else 'no ',
          str(p['last_seen_at'])[:10] if p.get('last_seen_at') else '—',
          (p.get('pattern_name') or '')[:40])
         for p in all_patterns],
    )
    print()
    summary = repo.get_library_summary()
    print("  {} patterns total. Run 'ic patterns show <pattern_name>' for details.".format(summary['total']))
    print()


@patterns.command('show')
@click.argument('name')
def patterns_show(name):
    """Show pattern details."""
    from cli.db import get_repo, require_db
    from cli.formatting import bold, section_header
    from repositories.error_pattern_repository import ErrorPatternRepository
    if not require_db():
        return
    p = get_repo(ErrorPatternRepository).get_by_name_or_category(name)
    if not p:
        print('Pattern not found: {}'.format(name))
        print("Run 'ic patterns list' to see all patterns.")
        raise SystemExit(1)
    section_header('Pattern: {}'.format(p['pattern_name']))
    print()
    print('  Type:          {}'.format(p['failure_type']))
    print('  Category:      {}'.format(p['failure_category']))
    print('  Created by:    {}'.format(p['created_by']))
    print('  Occurrences:   {}'.format(p.get('occurrence_count') or 0))
    if p.get('avg_confidence') is not None:
        print('  Avg confidence: {}%'.format(int(p['avg_confidence'] * 100)))
    print('  First seen:    {}'.format(p.get('first_seen_at') or '—'))
    print('  Last seen:     {}'.format(p.get('last_seen_at') or '—'))
    print()
    if p.get('description'):
        print(bold('Description:'))
        print('  {}'.format(p['description'][:200]))
        print()
    if p.get('typical_fix'):
        print(bold('Typical Fix:'))
        print('  {}'.format(p['typical_fix'][:200]))
        print()
    if p.get('doc_url'):
        print('  Doc URL:       {}'.format(p['doc_url']))
    if p.get('doc_context'):
        print()
        print(bold('Documentation Excerpt:'))
        lines = p['doc_context'].split('\n')[:20]
        for line in lines:
            print('  {}'.format(line[:80]))
        if len(p['doc_context'].split('\n')) > 20:
            print('  ... (truncated)')
    print()


# --- skills group ---

@cli.group(invoke_without_command=True)
@click.option('--json', 'output_json', is_flag=True, help='Output as JSON')
@click.pass_context
def skills(ctx, output_json):
    """Skill registry commands."""
    ctx.ensure_object(dict)
    ctx.obj['json'] = ctx.obj.get('json') or output_json
    if ctx.invoked_subcommand is None:
        ctx.invoke(skills_list)


@skills.command('list')
@click.option('--tag', 'filter_tag', multiple=True, help='Filter by tag (repeatable, AND logic)')
@click.option('--source', 'filter_source', help='Filter by source name')
@click.option('--json', 'output_json', is_flag=True, hidden=True)
@click.pass_context
def skills_list(ctx, filter_tag, filter_source, output_json):
    """List registered skills."""
    from skills.db_registry import get_registry
    from cli.formatting import dim, section_header
    registry = get_registry()
    result = registry.list_skills(source=filter_source)
    if filter_tag:
        for tag in filter_tag:
            result = [s for s in result if tag in s.tags]

    if output_json or (ctx.obj or {}).get('json'):
        print(json_mod.dumps([s.to_dict() for s in result], indent=2))
        return

    section_header('Registered Skills')
    print()
    if not result:
        print('  No skills registered. Run: ic skills add <git-url-or-source>')
        print()
        return
    name_w = max(len(s.name) for s in result)
    src_w = max(len(s.source) for s in result)
    for s in result:
        tags_str = ', '.join(s.tags) if s.tags else dim('none')
        print('  {:<{nw}}  {:<{sw}}  {:<8}  {}'.format(
            s.name, s.source, s.status, tags_str, nw=name_w, sw=src_w))
    print()
    print('  {} skill(s) total'.format(len(result)))
    print()


@skills.command('add')
@click.argument('source_name_or_url')
def skills_add(source_name_or_url):
    """Add skills from a git repo or known source."""
    from skills.known_sources import resolve_source
    from skills.loader import clone_source, discover_skills, use_local_source
    from skills.db_registry import get_registry
    from cli.formatting import bold, green

    registry = get_registry()
    expanded = os.path.expanduser(source_name_or_url)

    if os.path.isdir(expanded):
        local_path, commit = use_local_source(expanded)
        name = os.path.basename(os.path.abspath(expanded))
        url = 'file://{}'.format(os.path.abspath(expanded))
    else:
        name, url = resolve_source(source_name_or_url)
        print('Cloning {}...'.format(url))
        local_path, commit = clone_source(url, name)

    registry.add_source(name, url, commit, local_path)

    found = discover_skills(local_path)
    if not found:
        print('No SKILL.md files found in {}'.format(local_path))
        registry.save()
        return

    for skill_dir, meta in found:
        registry.add_skill(meta.name, name, skill_dir, meta)

    registry.save()
    print(green('Added {} skill(s) from {}'.format(len(found), bold(name))))
    for _, meta in found:
        print('  - {}: {}'.format(meta.name, meta.description[:60]))


@skills.command('remove')
@click.argument('name')
@click.option('--source', is_flag=True, help='Remove an entire source and all its skills')
def skills_remove(name, source):
    """Remove a skill or source."""
    from skills.db_registry import get_registry
    from cli.formatting import green
    registry = get_registry()

    if source:
        count = registry.remove_source(name)
        registry.save()
        print(green('Removed source "{}" and {} skill(s)'.format(name, count)))
    else:
        try:
            skill = registry.get_skill(name)
        except KeyError as e:
            print(str(e).strip('"\''))
            raise SystemExit(1)
        if not skill:
            print('Skill not found: {}'.format(name))
            raise SystemExit(1)
        registry.remove_skill(skill.qualified_name)
        registry.save()
        print(green('Removed skill: {}'.format(skill.qualified_name)))


@skills.command('update')
@click.argument('source_name', required=False)
def skills_update(source_name):
    """Update skills from source (git pull + re-scan)."""
    from skills.loader import clone_source, discover_skills
    from skills.db_registry import get_registry
    from cli.formatting import bold, green
    registry = get_registry()
    if source_name:
        if source_name not in registry.sources:
            print('Source not found: {}'.format(source_name))
            raise SystemExit(1)
        sources = [registry.sources[source_name]]
    else:
        sources = list(registry.sources.values())
    if not sources:
        print('No sources registered.')
        return
    for src in sources:
        print('Updating {}...'.format(bold(src.name)))
        local_path, commit = clone_source(src.url, src.name)
        found = discover_skills(local_path)
        found_names = set()
        for skill_dir, meta in found:
            qname = '{}/{}'.format(src.name, meta.name)
            found_names.add(qname)
            existing = registry.get_skill(qname)
            tags = existing.tags if existing else None
            registry.add_skill(meta.name, src.name, skill_dir, meta, initial_tags=tags)
        orphaned = [k for k, v in registry.skills.items()
                    if v.source == src.name and k not in found_names]
        for k in orphaned:
            registry.remove_skill(k)
        registry.update_source_commit(src.name, commit)
        removed_msg = ' ({} removed)'.format(len(orphaned)) if orphaned else ''
        print(green('  {} skill(s) from {} (commit {}){}').format(
            len(found), src.name, commit, removed_msg))
    registry.save()


@skills.command('info')
@click.argument('name')
def skills_info(name):
    """Show full details for a skill."""
    from skills.db_registry import get_registry
    from cli.formatting import bold, section_header
    registry = get_registry()
    try:
        skill = registry.get_skill(name)
    except KeyError as e:
        print(str(e).strip('"\''))
        raise SystemExit(1)
    if not skill:
        print('Skill not found: {}'.format(name))
        raise SystemExit(1)
    section_header('Skill: {}'.format(skill.qualified_name))
    print()
    print('  Name:          {}'.format(skill.name))
    print('  Source:         {}'.format(skill.source))
    print('  Status:        {}'.format(skill.status))
    print('  Path:          {}'.format(skill.path))
    print('  Tags:          {}'.format(', '.join(skill.tags) if skill.tags else '—'))
    print()
    print(bold('Metadata:'))
    print('  Description:   {}'.format(skill.metadata.description[:100]))
    print('  Allowed tools: {}'.format(skill.metadata.allowed_tools))
    print('  User-invocable: {}'.format(skill.metadata.user_invocable))
    if skill.metadata.ic_metadata:
        ic = skill.metadata.ic_metadata
        if ic.requires_tools:
            print('  Requires tools: {}'.format(', '.join(ic.requires_tools)))
        if ic.requires_env:
            print('  Requires env:  {}'.format(', '.join(ic.requires_env)))
    print()
    source = registry.sources.get(skill.source)
    if source:
        print(bold('Source:'))
        print('  URL:    {}'.format(source.url))
        print('  Commit: {}'.format(source.commit))
        print('  Added:  {}'.format(source.added_at[:10]))
        print()


@skills.command('sources')
def skills_sources():
    """List known and registered sources."""
    from skills.known_sources import KNOWN_SOURCES
    from skills.db_registry import get_registry
    from cli.formatting import bold, dim, green, section_header
    registry = get_registry()

    section_header('Registered Sources')
    print()
    if registry.sources:
        for src in registry.list_sources():
            skill_count = len([s for s in registry.skills.values() if s.source == src.name])
            print('  {:<20}  {} skill(s)  commit {}  {}'.format(
                bold(src.name), skill_count, src.commit[:8], dim(src.url)))
    else:
        print('  None registered yet.')
    print()

    section_header('Known Sources (shortcuts)')
    print()
    for name, info in sorted(KNOWN_SOURCES.items()):
        registered = green('✓') if name in registry.sources else dim('—')
        print('  {} {:<20}  {}'.format(registered, name, info['description']))
    print()
    print("  Add with: ic skills add <name>   (e.g., ic skills add aiops-infra)")
    print()


@skills.group('tag')
def skills_tag():
    """Manage skill tags."""


@skills_tag.command('add')
@click.argument('skill_name')
@click.argument('tag')
def skills_tag_add(skill_name, tag):
    """Add a tag to a skill."""
    from skills.db_registry import get_registry
    from cli.formatting import green
    registry = get_registry()
    try:
        if registry.add_tag(skill_name, tag):
            registry.save()
            print(green('Added tag "{}" to {}'.format(tag, skill_name)))
        else:
            print('Skill not found: {}'.format(skill_name))
            raise SystemExit(1)
    except KeyError as e:
        print(str(e).strip('"\''))
        raise SystemExit(1)


@skills_tag.command('remove')
@click.argument('skill_name')
@click.argument('tag')
def skills_tag_remove(skill_name, tag):
    """Remove a tag from a skill."""
    from skills.db_registry import get_registry
    from cli.formatting import green
    registry = get_registry()
    try:
        if registry.remove_tag(skill_name, tag):
            registry.save()
            print(green('Removed tag "{}" from {}'.format(tag, skill_name)))
        else:
            print('Skill not found or tag not present: {}'.format(skill_name))
            raise SystemExit(1)
    except KeyError as e:
        print(str(e).strip('"\''))
        raise SystemExit(1)


@skills.command('tags')
def skills_tags_list():
    """List all tags with usage counts."""
    from skills.db_registry import get_registry
    from cli.formatting import section_header
    registry = get_registry()
    tags = registry.list_tags()
    section_header('Skill Tags')
    print()
    if not tags:
        print('  No tags in use.')
    else:
        for tag, count in tags.items():
            print('  {:<25}  {} skill(s)'.format(tag, count))
    print()


# --- components group ---

@cli.group()
def components():
    """Component commands."""


@components.command('health')
def components_health():
    """All components with build/conforma status."""
    from cli.db import require_db, sql_table
    from cli.formatting import section_header
    if not require_db():
        return
    section_header('Component Health')
    print()
    sql_table("""
        SELECT
            component_name as "Component",
            application as "App",
            current_status as "Status",
            health_score as "Score",
            health_status as "Health",
            consecutive_failures as "Consec. Fails",
            success_rate_last_7d as "7d Rate",
            total_failures_last_7d as "7d Fails"
        FROM component_health
        ORDER BY health_score ASC NULLS FIRST;
    """)
    print()


# --- db group ---

@cli.group()
def db():
    """Database commands."""


@db.command('status')
def db_status():
    """Check database connection."""
    from cli.db import require_db, sql
    if require_db():
        from cli.formatting import green
        version = sql('SELECT version();')
        print(green('Database connected'))
        if version:
            print('  {}'.format(version[:60]))


@db.command('query')
@click.argument('sql_query')
def db_query(sql_query):
    """Execute custom SQL query."""
    from cli.db import require_db, sql_table
    if not require_db():
        return
    sql_table(sql_query)


# --- Number shortcut ---

@cli.command('n', hidden=True)
@click.argument('number', type=int)
@click.argument('args', nargs=-1)
def number_shortcut(number, args):
    """Describe Nth failing component."""
    _bash_fallback([str(number)] + list(args))


# --- Backward compat aliases ---

@cli.command('build', hidden=True)
@click.argument('args', nargs=-1)
def build_compat(args):
    _bash_fallback(['build'] + list(args))


@cli.command('component', hidden=True)
@click.argument('args', nargs=-1)
def component_compat(args):
    _bash_fallback(['component'] + list(args))


@cli.command('scenarios', hidden=True)
@click.argument('args', nargs=-1)
def scenarios_compat(args):
    _bash_fallback(['conforma', 'scenarios'] + list(args))


@cli.command('alerts', hidden=True)
@click.argument('args', nargs=-1)
def alerts_compat(args):
    _bash_fallback(['alerts'] + list(args))


@cli.command('apps', hidden=True)
def apps_compat():
    _bash_fallback(['get', 'apps'])


@cli.command('versions', hidden=True)
def versions_compat():
    _bash_fallback(['get', 'apps'])


# --- Bash fallback ---

_IC_BASH = os.path.join(str(cfg.PROJECT_DIR), 'ic')


def _bash_fallback(args):
    """Delegate to the bash ic script for commands not yet migrated."""
    env = os.environ.copy()
    env['IC_PYTHON_FALLBACK'] = '1'
    try:
        result = subprocess.run(
            [_IC_BASH] + list(args),
            env=env,
        )
        sys.exit(result.returncode)
    except FileNotFoundError:
        click.echo('Error: bash ic script not found at {}'.format(_IC_BASH), err=True)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)


def main():
    """Entry point — handle number shortcuts before Click parsing."""
    args = sys.argv[1:]

    if args and args[0].isdigit():
        _bash_fallback(args)
        return

    # Alias: ic pr -> ic get pipelinerun
    if args and args[0] == 'pr':
        args = ['get', 'pipelinerun'] + args[1:]
        sys.argv = [sys.argv[0]] + args

    # Alias: ic its -> ic conforma scenarios
    if args and args[0] == 'its':
        args = ['conforma', 'scenarios'] + args[1:]
        sys.argv = [sys.argv[0]] + args

    try:
        cli(standalone_mode=False)
    except click.exceptions.UsageError:
        _bash_fallback(args)
