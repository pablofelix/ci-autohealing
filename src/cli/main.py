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
    import json as json_mod
    from cli.mode import ensure_cluster
    is_json = output_json or ctx.obj.get('json')
    if ensure_cluster():
        from cli.data import get_failures
        from cli.formatting import bold, green, red, section_header
        data = get_failures()
        if is_json:
            print(json_mod.dumps(data, indent=2, default=str))
            return
        section_header('Failing Components: {}'.format(cfg.APPLICATION_NAME))
        print()
        items = data if isinstance(data, list) else []
        if not items:
            print(green('  No failing components'))
        for f in items:
            comp = f.get('component_name', f.get('component', '?'))
            err = f.get('error_type') or ''
            print('  {} {}'.format(red('✗'), '{} — {}'.format(bold(comp), err) if err else bold(comp)))
        print()
        return
    args = ['get', 'components']
    if refresh:
        args.append('--refresh')
    if is_json:
        args.append('--json')
    _bash_fallback(args)


@get.command('component')
@click.argument('name')
@click.option('--json', 'output_json', is_flag=True, hidden=True)
@click.pass_context
def get_component(ctx, name, output_json):
    """Get component summary."""
    is_json = output_json or ctx.obj.get('json')
    from cli.data import get_component_summary
    summary = get_component_summary(name)
    if not summary:
        if is_json:
            print(json_mod.dumps({"error": "Component not found", "component": name}))
        else:
            from cli.formatting import cyan, red
            print(red('Error: Component not found: {}'.format(name)))
            from cli.suggest import format_suggestion
            from cli.data import get_alerts
            try:
                alerts = get_alerts()
                all_comps = [f.get('component', '') for f in
                             alerts.get('build_failures', []) + alerts.get('conforma_violations', [])]
                hint = format_suggestion(name, all_comps)
                if hint:
                    print('  {}'.format(hint))
            except Exception:
                pass
            print('  Run {} to see current components'.format(cyan('ic get alerts')))
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


def _format_since(raw):
    """Format a timestamp as '30 Jun 08:04' for display."""
    from datetime import datetime as dt
    s = str(raw or '')
    for fmt in ('%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S'):
        try:
            d = dt.strptime(s[:26], fmt)
            return d.strftime('%d %b %H:%M')
        except (ValueError, IndexError):
            continue
    if len(s) >= 10:
        try:
            d = dt.strptime(s[:10], '%Y-%m-%d')
            return d.strftime('%d %b')
        except ValueError:
            pass
    return s[:12] if s else ''


@get.command('alerts')
@click.option('--group', is_flag=True, help='Group by root cause')
@click.option('--all', 'show_all', is_flag=True, help='All applications')
@click.option('--date', help='Alerts on specific date')
@click.option('--from', 'from_date', help='Start date for range')
@click.option('--to', 'to_date', help='End date for range')
@click.option('--stage', is_flag=True, help='Show only stage-policy violations')
@click.option('--prod', is_flag=True, help='Show only prod-policy violations')
@click.option('--json', 'output_json', is_flag=True, hidden=True)
@click.pass_context
def get_alerts(ctx, group, show_all, date, from_date, to_date, stage, prod, output_json):
    """Unified view: build failures + Conforma violations."""
    import json as json_mod
    from cli.mode import ensure_cluster
    is_json = output_json or ctx.obj.get('json')

    if ensure_cluster():
        from cli.data import get_alerts as _get_alerts
        from cli.formatting import bold, dim, green, red, section_header, yellow
        data = _get_alerts()
        if is_json:
            print(json_mod.dumps(data, indent=2, default=str))
            return

        builds = data.get('build_failures', [])
        conforma = data.get('conforma_violations', [])
        nightlies = data.get('nightly_warnings', [])

        if stage or prod:
            from utils.conforma_utils import policy_env as _policy_env, extract_policy_from_scenario as _extract_policy
            filtered = []
            for v in conforma:
                env = _policy_env(_extract_policy(v.get('scenario', '')))
                if stage and env == 'stage':
                    filtered.append(v)
                elif prod and env == 'prod':
                    filtered.append(v)
                elif stage and prod:
                    filtered.append(v)
            conforma = filtered
        schedule = data.get('release_schedule')
        last_sync = data.get('last_sync', '')

        from cli.data import _triage_info_map_safe
        triage_info = _triage_info_map_safe(cfg.APPLICATION_NAME)
        if triage_info:
            for v in conforma:
                comp = v.get('component', '')
                info = triage_info.get(comp)
                if info and not v.get('jira_key') and info.get('jira_key'):
                    v['jira_key'] = info['jira_key']

        section_header('Alerts — {}'.format(cfg.APPLICATION_NAME))
        print()

        if last_sync:
            sync_str = str(last_sync)[:19]
            print('Last sync: {}'.format(sync_str))
            print()

        if schedule:
            parts = []
            for key, label in [('code_freeze', 'Code Freeze'), ('initial_rc', 'RC'), ('release_date', 'GA')]:
                days = schedule.get('{}_days'.format(key))
                val = schedule.get(key)
                if val and days is not None:
                    from datetime import datetime as dt
                    try:
                        d = dt.strptime(val[:10], '%Y-%m-%d')
                        date_str = d.strftime('%b %d')
                    except Exception:
                        date_str = val[:10]
                    color = red if days <= 3 else yellow if days <= 7 else green
                    parts.append('{}: {} ({})'.format(label, date_str, color('{}d'.format(days))))
            if parts:
                print(bold('Release:  ') + '  |  '.join(parts))
                print()

        print(bold('Build Failures ({})'.format(len(builds))) + ':')
        if builds:
            print('  {:<4} {:<45} {:<20} {:>12}  {}'.format(
                '#', 'Component', 'Error/Cause', 'Since', 'JIRA'))
            print('  {}  {} {} {}  {}'.format(
                '---', '-' * 45, '-' * 20, '-' * 12, '--------'))
            for i, f in enumerate(builds, 1):
                comp = f.get('component', '?')
                if len(comp) > 45:
                    comp = comp[:42] + '...'
                err = f.get('error_type') or f.get('possible_cause') or ''
                info = triage_info.get(f.get('component', ''))
                if not err and info:
                    err = info.get('group_label') or ''
                if len(err) > 20:
                    err = err[:17] + '...'
                first = _format_since(f.get('first_seen', ''))
                jira = ''
                if f.get('jira_key'):
                    jira = f['jira_key']
                elif info and info.get('jira_key'):
                    jira = info['jira_key']
                jira = jira or '-'
                analyzed = green(' [AI]') if f.get('has_analysis') else ''
                print('  {:<4} {:<45} {:<20} {:>12}  {}{}'.format(
                    i, comp, err, first, jira, analyzed))
        else:
            print('  {} No build failures'.format(green('✓')))
        print()

        from utils.conforma_utils import extract_policy_from_scenario
        policies_seen = set()
        for v in conforma:
            p = extract_policy_from_scenario(v.get('scenario', ''))
            if p:
                policies_seen.add(p)
        policy_note = ''
        if len(policies_seen) > 1:
            policy_note = '  ({} policies)'.format(len(policies_seen))
        print(bold('Conforma Failures ({})'.format(len(conforma))) + policy_note + ':')
        if conforma:
            print('  {:<4} {:<45} {:>6} {:>6} {:>12}  {}'.format(
                '#', 'Component', 'Viol', 'Warn', 'Since', 'JIRA'))
            print('  {}  {} {} {} {}  {}'.format(
                '---', '-' * 45, '------', '------', '-' * 12, '--------'))
            for i, v in enumerate(conforma, 1):
                comp = v.get('component', '?')
                if len(comp) > 45:
                    comp = comp[:42] + '...'
                viol = v.get('violations_count', 0) or 0
                warn = v.get('warnings_count', 0) or 0
                first = _format_since(v.get('first_seen', ''))
                jira = v.get('jira_key') or '-'
                viol_color = red if viol > 0 else green
                policy = extract_policy_from_scenario(v.get('scenario', ''))
                policy_tag = ' [{}]'.format(dim(policy)) if policy and len(policies_seen) > 1 else ''
                env_tag = v.get('exception_env_tag')
                stage_cov = v.get('exception_coverage_stage')
                prod_cov = v.get('exception_coverage_prod')
                cov_tag = ''
                if env_tag:
                    is_partial = (stage_cov == 'partially_covered' or prod_cov == 'partially_covered')
                    label = 'PARTIAL' if is_partial else 'EXC'
                    color_fn = yellow if is_partial else green
                    cov_tag = ' {}'.format(color_fn('[{}:{}]'.format(label, env_tag)))
                print('  {:<4} {:<45} {} {:>6} {:>12}  {}{}{}'.format(
                    i, comp, viol_color('{:>6}'.format(viol)), warn, first, jira, policy_tag, cov_tag))
                p_url_s = v.get('policy_url_stage')
                p_url_p = v.get('policy_url_prod')
                if p_url_s:
                    print('       {} {}'.format(dim('↳ stage'), dim(p_url_s)))
                if p_url_p:
                    print('       {} {}'.format(dim('↳ prod '), dim(p_url_p)))
                if env_tag and (stage_cov == 'partially_covered' or prod_cov == 'partially_covered'):
                    us = v.get('uncovered_rules_stage', [])
                    up = v.get('uncovered_rules_prod', [])
                    both = sorted(set(us) & set(up))
                    only_s = sorted(set(us) - set(up))
                    only_p = sorted(set(up) - set(us))
                    if both:
                        print('       {} {}'.format(red('↳ missing(S+P):'), ', '.join(both)))
                    if only_s:
                        print('       {} {}'.format(red('↳ missing(S):'), ', '.join(only_s)))
                    if only_p:
                        print('       {} {}'.format(red('↳ missing(P):'), ', '.join(only_p)))
        else:
            print('  {} No conforma violations'.format(green('✓')))
        print()

        if nightlies:
            print(bold(yellow('{} nightly warning(s):'.format(len(nightlies)))))
            for w in nightlies:
                print('  {} — {}'.format(w.get('component_name', '?'), w.get('message', '')))
            print()

        return

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
    if stage:
        args.append('--stage')
    if prod:
        args.append('--prod')
    if is_json:
        args.append('--json')
    _bash_fallback(args)


@get.command('conforma')
@click.argument('filter', required=False, default='')
@click.option('--json', 'output_json', is_flag=True, hidden=True)
@click.option('--stage', is_flag=True, help='Show stage policy violations (from reporter)')
@click.option('--nightly', is_flag=True, help='Show nightly build violations (from reporter)')
@click.pass_context
def get_conforma(ctx, filter, output_json, stage, nightly):
    """Conforma test failures."""
    import json as json_mod
    from cli.mode import ensure_cluster
    is_json = output_json or ctx.obj.get('json')
    use_reporter = stage or nightly
    if use_reporter or ensure_cluster():
        from cli.data import get_conforma_violations
        from cli.formatting import bold, dim, green, red, section_header
        reporter_env = 'stage' if stage else None
        reporter_build_type = 'nightly' if nightly else None
        data = get_conforma_violations(
            reporter_env=reporter_env, reporter_build_type=reporter_build_type)
        if isinstance(data, list):
            from cli.data import _triage_jira_map_safe
            jira_map = _triage_jira_map_safe(cfg.APPLICATION_NAME)
            if jira_map:
                for v in data:
                    if isinstance(v, dict):
                        comp = v.get('component', v.get('component_name', ''))
                        if not v.get('jira_key') and comp in jira_map:
                            v['jira_key'] = jira_map[comp]
        if is_json:
            print(json_mod.dumps(data, indent=2, default=str))
            return
        source_parts = []
        if stage:
            source_parts.append('stage')
        if nightly:
            source_parts.append('nightly')
        source_label = ' ({})'.format('+'.join(source_parts)) if source_parts else ''
        section_header('Conforma Violations: {}{}'.format(cfg.APPLICATION_NAME, source_label))
        print()
        if not data:
            print(green('  No conforma violations'))
            print()
            return
        if isinstance(data, list) and data and isinstance(data[0], str):
            for comp in data:
                print('  {}'.format(comp))
        elif isinstance(data, list):
            from utils.conforma_utils import extract_policy_from_scenario
            from cli.formatting import yellow
            cov_counts = {'fully_covered': 0, 'partially_covered': 0, 'not_covered': 0}
            for v in data:
                comp = v.get('component', v.get('component_name', '?'))
                viol = v.get('violations_count', 0) or 0
                warn = v.get('warnings_count', 0) or 0
                scenario = v.get('scenario', v.get('error_type', ''))
                jira = v.get('jira_key', '')
                policy = extract_policy_from_scenario(scenario)
                exc_cov = v.get('exception_coverage')
                env_tag = v.get('exception_env_tag')
                stage_cov = v.get('exception_coverage_stage')
                prod_cov = v.get('exception_coverage_prod')
                viol_color = red if viol > 0 else green
                jira_part = ' — {}'.format(bold(jira)) if jira else ''
                policy_part = ' [{}]'.format(dim(policy)) if policy else ''
                cov_part = ''
                if env_tag:
                    is_partial = (stage_cov == 'partially_covered' or prod_cov == 'partially_covered')
                    label = 'PARTIAL' if is_partial else 'EXC'
                    color_fn = yellow if is_partial else green
                    cov_part = ' {}'.format(color_fn('[{}:{}]'.format(label, env_tag)))
                    if is_partial:
                        cov_counts['partially_covered'] += 1
                    else:
                        cov_counts['fully_covered'] += 1
                elif exc_cov == 'fully_covered':
                    cov_part = ' {}'.format(green('[EXC]'))
                    cov_counts['fully_covered'] += 1
                elif exc_cov == 'partially_covered':
                    cov_part = ' {}'.format(yellow('[PARTIAL]'))
                    cov_counts['partially_covered'] += 1
                elif exc_cov == 'not_covered':
                    cov_counts['not_covered'] += 1
                print('  {} — {} viol, {} warn{}{}{}'.format(
                    bold(comp), viol_color(str(viol)), warn, policy_part,
                    cov_part, jira_part))
                if env_tag and (stage_cov == 'partially_covered' or prod_cov == 'partially_covered'):
                    us = v.get('uncovered_rules_stage', [])
                    up = v.get('uncovered_rules_prod', [])
                    both = sorted(set(us) & set(up))
                    only_s = sorted(set(us) - set(up))
                    only_p = sorted(set(up) - set(us))
                    if both:
                        print('    {} {}'.format(red('↳ missing(S+P):'), ', '.join(both)))
                    if only_s:
                        print('    {} {}'.format(red('↳ missing(S):'), ', '.join(only_s)))
                    if only_p:
                        print('    {} {}'.format(red('↳ missing(P):'), ', '.join(only_p)))
            if any(cov_counts.values()):
                parts = []
                if cov_counts['fully_covered']:
                    parts.append(green('{} covered'.format(cov_counts['fully_covered'])))
                if cov_counts['partially_covered']:
                    parts.append(yellow('{} partial'.format(cov_counts['partially_covered'])))
                if cov_counts['not_covered']:
                    parts.append(red('{} uncovered'.format(cov_counts['not_covered'])))
                print()
                print('  Exception coverage: {}'.format(', '.join(parts)))
        print()
        return
    args = ['get', 'conforma']
    if filter:
        args.append(filter)
    if is_json:
        args.append('--json')
    _bash_fallback(args)


@get.command('exceptions')
@click.option('--json', 'output_json', is_flag=True, hidden=True)
@click.pass_context
def get_exceptions(ctx, output_json):
    """Policy exceptions expiring/expired."""
    import json as json_mod
    from cli.mode import ensure_cluster
    is_json = output_json or ctx.obj.get('json')
    if ensure_cluster():
        from cli.data import get_policy_exceptions
        from cli.formatting import dim, green, red, section_header, yellow
        data = get_policy_exceptions()
        if is_json:
            print(json_mod.dumps(data, indent=2, default=str))
            return
        section_header('Policy Exceptions')
        print()
        exceptions = data.get('exceptions', []) if data else []
        if not exceptions:
            print('  No exceptions found')
            print()
            return
        from utils.conforma_utils import policy_env as _policy_env
        print('  {:<40} {:<6} {:<12} {}'.format('Rule', 'Env', 'Expires', 'Policy'))
        print('  {}  {} {} {}'.format('-' * 40, '-' * 6, '-' * 12, '-' * 30))
        for exc in sorted(exceptions, key=lambda e: (e.get('source_policy', ''), e.get('value', ''))):
            value = exc.get('value', '?')
            if len(value) > 40:
                value = value[:37] + '...'
            pname = exc.get('source_policy', '')
            env = _policy_env(pname)
            env_display = green('stage') if env == 'stage' else yellow('prod') if env == 'prod' else dim('-')
            days = exc.get('days_left')
            if exc.get('permanent'):
                expires = dim('permanent')
            elif days is not None:
                expires = red('{}d'.format(days)) if days <= 7 else yellow('{}d'.format(days))
            else:
                expires = dim('-')
            print('  {:<40} {:<6} {:<12} {}'.format(value, env_display, expires, dim(pname)))
        print()
        print('  Total: {} exceptions'.format(len(exceptions)))
        print()
        return
    args = ['get', 'exceptions']
    if is_json:
        args.append('--json')
    _bash_fallback(args)


@get.command('bindings')
@click.option('--json', 'output_json', is_flag=True, hidden=True)
@click.pass_context
def get_bindings(ctx, output_json):
    """RPA -> EC policy mappings."""
    import json as json_mod
    from cli.mode import ensure_cluster
    is_json = output_json or ctx.obj.get('json')
    if ensure_cluster():
        from cli.data import get_policy_bindings
        from cli.formatting import bold, section_header
        data = get_policy_bindings()
        if is_json:
            print(json_mod.dumps(data, indent=2, default=str))
            return
        section_header('RPA → EC Policy Bindings')
        print()
        bindings = data.get('bindings', []) if data else []
        if not bindings:
            print('  No bindings found')
        for b in bindings:
            print('  {} → {}'.format(bold(b.get('name', '?')[:40]), b.get('policy', '')))
        print()
        return
    args = ['get', 'bindings']
    if is_json:
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
    import json as json_mod
    from cli.mode import ensure_cluster
    is_json = output_json or ctx.obj.get('json')
    if ensure_cluster():
        from cli.data import get_failures
        from cli.formatting import bold, dim, section_header
        data = get_failures()
        if is_json:
            print(json_mod.dumps(data, indent=2, default=str))
            return
        section_header('Build Failures: {}'.format(cfg.APPLICATION_NAME))
        print()
        items = data if isinstance(data, list) else []
        for f in items[:limit or 50]:
            comp = f.get('component_name', f.get('component', '?'))
            status = f.get('status', '')
            err = f.get('error_type') or f.get('error_message', '')
            print('  {} — {} {}'.format(bold(comp), status, dim(err[:60]) if err else ''))
        print()
        return
    args = ['get', 'pipelineruns']
    if limit:
        args.extend(['--limit', str(limit)])
    if is_json:
        args.append('--json')
    _bash_fallback(args)


@get.command('pipelinerun')
@click.argument('name')
@click.option('--json', 'output_json', is_flag=True, hidden=True)
@click.pass_context
def get_pipelinerun(ctx, name, output_json):
    """Get PipelineRun details."""
    import json as json_mod
    from cli.mode import ensure_cluster
    is_json = output_json or ctx.obj.get('json')
    if ensure_cluster():
        from cli.data import get_failure_details
        data = get_failure_details(name)
        if is_json or data:
            print(json_mod.dumps(data, indent=2, default=str))
        else:
            print('PipelineRun not found: {}'.format(name))
        return
    args = ['get', 'pipelinerun', name]
    if is_json:
        args.append('--json')
    _bash_fallback(args)


@get.command('apps')
@click.option('--json', 'output_json', is_flag=True, hidden=True)
@click.pass_context
def get_apps(ctx, output_json):
    """List available RHOAI applications."""
    is_json = output_json or ctx.obj.get('json')
    from cli.data import get_applications
    from cli.mode import ensure_cluster
    if ensure_cluster():
        app_list = get_applications()
        apps = [a.get('name', a.get('application', '')) for a in app_list]
        app_counts = {a.get('name', a.get('application', '')): a.get('failure_count', a.get('count', 0)) for a in app_list}
    else:
        from cli.db import get_repo
        from repositories.build_failure_repository import BuildFailureRepository
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
        app_counts = {a['application']: a['count'] for a in build_repo.get_applications()}
    if not apps:
        if is_json:
            print(json_mod.dumps({"applications": [], "current": cfg.APPLICATION_NAME}))
        else:
            from cli.formatting import yellow
            print(yellow('No applications found'))
        return
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
    import json as json_mod
    from cli.mode import ensure_cluster
    is_json = output_json or ctx.obj.get('json')
    if ensure_cluster():
        from cli.data import get_schedule
        from cli.formatting import green, section_header, yellow
        schedule = get_schedule()
        if is_json:
            print(json_mod.dumps(schedule, indent=2, default=str))
            return
        section_header('Release Schedule: {}'.format(cfg.APPLICATION_NAME))
        print()
        if not schedule:
            print('  No release schedule data')
        else:
            for key in ('planning_freeze', 'code_freeze', 'initial_rc',
                        'release_window_start', 'release_date', 'next_release'):
                val = schedule.get(key)
                days = schedule.get('{}_days'.format(key))
                if val:
                    days_str = ' ({}d)'.format(days) if days is not None else ''
                    color = green
                    if days is not None and days <= 3:
                        color = yellow
                    print('  {:<25} {}{}'.format(key + ':', val[:10], color(days_str)))
        print()
        return
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
    if is_json:
        args.append('--json')
    _bash_fallback(args)


@get.command('jira')
@click.argument('component', required=False, default='')
@click.option('--json', 'output_json', is_flag=True, hidden=True)
@click.pass_context
def get_jira(ctx, component, output_json):
    """Show Jira ticket links."""
    import json as json_mod
    from cli.mode import ensure_cluster
    is_json = output_json or ctx.obj.get('json')
    if ensure_cluster():
        from cli.data import get_alerts
        from cli.formatting import bold, cyan, section_header
        data = get_alerts()
        all_items = data.get('build_failures', []) + data.get('conforma_violations', [])
        jira_items = [i for i in all_items if i.get('jira_key')]
        if is_json:
            print(json_mod.dumps(jira_items, indent=2, default=str))
            return
        section_header('Jira Links')
        print()
        if not jira_items:
            print('  No Jira tickets linked')
        else:
            for i in jira_items:
                print('  {} → {}'.format(bold(i['component']), cyan(i['jira_key'])))
        print()
        return
    args = ['get', 'jira']
    if component:
        args.append(component)
    if is_json:
        args.append('--json')
    _bash_fallback(args)


@get.command('fixes')
@click.option('--all', 'show_all', is_flag=True)
@click.option('--json', 'output_json', is_flag=True, hidden=True)
@click.argument('component', required=False, default='')
@click.pass_context
def get_fixes(ctx, show_all, output_json, component):
    """List fix attempts."""
    import json as json_mod
    from cli.mode import ensure_cluster
    is_json = output_json or ctx.obj.get('json')
    if ensure_cluster():
        from cli.data import get_fixes as _get_fixes
        from cli.formatting import bold, dim, green, red, section_header
        data = _get_fixes(show_all=show_all)
        if is_json:
            print(json_mod.dumps(data, indent=2, default=str))
            return
        section_header('Fix Attempts')
        print()
        items = data if isinstance(data, list) else []
        if not items:
            print('  No fix attempts')
        for f in items:
            status = f.get('status', '?')
            color = green if status == 'success' else red
            print('  {} — {} {}'.format(
                bold(f.get('component_name', '?')),
                color(status),
                dim(f.get('pr_url', '') or '')))
        print()
        return
    args = ['get', 'fixes']
    if component:
        args.append(component)
    if show_all:
        args.append('--all')
    if is_json:
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
    import json as json_mod
    from cli.mode import ensure_cluster
    is_json = output_json or ctx.obj.get('json')
    if ensure_cluster():
        from cli.data import get_failure_details
        from cli.formatting import bold, cyan, dim, green, red, section_header, yellow
        data = get_failure_details(name)
        if not data:
            print(red('Component not found: ') + cyan(name))
            raise SystemExit(1)
        if is_json:
            print(json_mod.dumps(data, indent=2, default=str))
            return

        section_header('Component: {}'.format(name))
        print()

        pr_name = data.get('pipelinerun_name', '')

        section_header('Latest PipelineRun')
        print()
        print(bold('PipelineRun:') + ' {}'.format(pr_name))
        print(bold('Status:') + '      {}'.format(red('Failed')))
        if data.get('repository_url'):
            print(bold('Repository:') + '  {}'.format(data['repository_url']))
        if data.get('branch'):
            print(bold('Branch:') + '      {}'.format(data['branch']))
        if data.get('commit_sha'):
            print(bold('Commit:') + '      {}'.format(data['commit_sha'][:8]))
        if data.get('commit_author'):
            print(bold('Author:') + '      {}'.format(data['commit_author']))
        if data.get('commit_message'):
            print(bold('Message:') + '     {}'.format(data['commit_message'][:80]))
        if data.get('commit_url'):
            print(bold('Commit URL:') + '  {}'.format(cyan(data['commit_url'])))
        print()

        if data.get('failed_step') or data.get('error_type') or data.get('error_message'):
            print(bold('Failure Info:'))
            if data.get('failed_step'):
                print('  Failed Step: {}'.format(red(data['failed_step'])))
            if data.get('error_type'):
                print('  Error Type:  {}'.format(data['error_type']))
            if data.get('error_message'):
                msg = data['error_message']
                for line in msg.split('\n')[:5]:
                    print('  {}'.format(line[:120]))
            if data.get('failed_task'):
                print('  Task:        {}'.format(data['failed_task']))
        print()

        if data.get('jira_key'):
            print(bold('JIRA:') + '        {}'.format(cyan(data['jira_key'])))
        if data.get('konflux_url'):
            print(bold('Konflux UI:') + ' {}'.format(cyan(data['konflux_url'])))
        if data.get('output_image'):
            print(bold('Image:') + '       {}'.format(data['output_image'][:80]))
        print()

        if data.get('build_logs'):
            from cli.log_filter import filter_error_context
            raw_logs = data['build_logs']
            failed = data.get('failed_step') or data.get('failed_task')

            if log:
                section_header('Build Logs (Full)')
                print()
                print(raw_logs)
                print()
            else:
                filtered, stats = filter_error_context(raw_logs, failed_step=failed)
                if stats.get('filtered'):
                    header = 'Build Logs'
                    if stats['match_count'] > 0:
                        header += ' — {} error/warning lines + context (from {} total)'.format(
                            stats['match_count'], stats['total_lines'])
                    elif stats.get('note'):
                        header += ' — {}'.format(stats['note'])
                    section_header(header)
                else:
                    section_header('Build Logs')
                print()
                print(filtered)
                print()
                if stats['total_lines'] > 80:
                    print(cyan('Tip: Add --log to see the complete build log'))
                    print()

        history = data.get('build_history', [])
        if history:
            section_header('Build History')
            print()
            for b in history:
                st = b.get('status', '?')
                color = green if st == 'Succeeded' else red
                date_str = str(b.get('date', b.get('build_completion_time', '')))[:16]
                commit = str(b.get('commit', b.get('commit_short_sha', '')))[:8]
                failed = b.get('failed_task_name', b.get('failed_task', ''))
                extra = ', {}'.format(failed) if failed and st != 'Succeeded' else ''
                print('  {}  {}  ({}{})'.format(
                    date_str,
                    color('✓ succeeded' if st == 'Succeeded' else '✗ failed'),
                    commit, extra))
            resolved = history[0].get('status') == 'Succeeded' if history else False
            print()
            if resolved:
                print(green('  Status: RESOLVED') + ' (latest build passed)')
            print()

        if data.get('ai_analyzed'):
            print(dim('[AI analysis available]'))
        else:
            print(yellow('[!] AI analysis not available'))
            print(cyan('[!] Run: ic ai analyze component {}'.format(name)))
        print()
        return
    args = ['describe', 'component', name]
    if log:
        args.append('--log')
    if is_json:
        args.append('--json')
    _bash_fallback(args)


@describe.command('conforma')
@click.argument('name')
@click.option('--json', 'output_json', is_flag=True, hidden=True)
@click.pass_context
def describe_conforma(ctx, name, output_json):
    """Conforma violation details."""
    import json as json_mod
    from cli.mode import ensure_cluster
    is_json = output_json or ctx.obj.get('json')
    if ensure_cluster():
        from cli.data import get_conforma_details
        from cli.formatting import bold, red, section_header
        data = get_conforma_details(name)
        if not data:
            print(red('Conforma violation not found: ') + name)
            raise SystemExit(1)
        if is_json:
            print(json_mod.dumps(data, indent=2, default=str))
            return
        section_header('Conforma: {}'.format(name))
        print()
        for key in ('scenario', 'violations_count', 'warnings_count', 'successes_count',
                     'pipelinerun_name', 'snapshot_name', 'repository_url',
                     'commit_sha', 'first_detected_at', 'last_updated_at',
                     'jira_key', 'ai_analyzed'):
            val = data.get(key)
            if val is not None:
                print('  {:<20} {}'.format(key + ':', val))
        summary = data.get('violation_summary')
        if summary:
            print()
            print(bold('Violation Summary:'))
            print('  {}'.format(summary[:2000]))
        print()
        return
    args = ['describe', 'conforma', name]
    if is_json:
        args.append('--json')
    _bash_fallback(args)


@describe.command('pipelinerun')
@click.argument('name')
@click.option('--json', 'output_json', is_flag=True, hidden=True)
@click.pass_context
def describe_pipelinerun(ctx, name, output_json):
    """Full PipelineRun details + logs."""
    import json as json_mod
    from cli.mode import ensure_cluster
    is_json = output_json or ctx.obj.get('json')
    if ensure_cluster():
        from cli.data import get_failure_details
        from cli.formatting import section_header
        data = get_failure_details(name)
        if is_json or not data:
            print(json_mod.dumps(data, indent=2, default=str))
            return
        section_header('PipelineRun: {}'.format(name))
        print()
        for k, v in data.items():
            if v and k not in ('build_logs', 'raw_pipelinerun_yaml', 'blob_refs'):
                print('  {:<22} {}'.format(k + ':', str(v)[:100]))
        print()
        return
    args = ['describe', 'pipelinerun', name]
    if is_json:
        args.append('--json')
    _bash_fallback(args)


@describe.command('jira')
@click.argument('key')
@click.option('--json', 'output_json', is_flag=True, hidden=True)
@click.pass_context
def describe_jira(ctx, key, output_json):
    """Show Jira ticket details."""
    import json as json_mod
    from cli.mode import ensure_cluster
    is_json = output_json or ctx.obj.get('json')
    if ensure_cluster():
        from cli.data import get_jira_issue
        from cli.formatting import bold, cyan, dim, section_header
        data = get_jira_issue(key)
        if is_json:
            print(json_mod.dumps(data, indent=2, default=str))
            return
        if not data:
            return
        section_header('Jira: {}'.format(key))
        print()
        status = data.get('status', {})
        if isinstance(status, dict):
            for k, v in status.items():
                print('  {:<20} {}'.format(k + ':', v))
        else:
            print('  Status: {}'.format(status))
        comments = data.get('comments', [])
        if comments:
            print()
            print(bold('{} comment(s):'.format(len(comments))))
            for c in comments[-5:]:
                author = c.get('author', {}).get('displayName', '?')
                body = c.get('body', '')[:100]
                print('  {} — {}'.format(cyan(author), dim(body)))
        print()
        return
    args = ['describe', 'jira', key]
    if is_json:
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
        from cli import ic_config
        from cli.formatting import bold, cyan, green, yellow, section_header
        mode = ic_config.get_mode()
        section_header('Current Configuration')
        print()
        print(bold('Mode: ') + (green('cluster') if mode == 'cluster' else cyan('local')))
        print()
        print(bold('Environment:'))
        print('  Namespace: {}'.format(cyan(cfg.NAMESPACE)))
        print('  Application: {}'.format(cyan(cfg.APPLICATION_NAME)))
        print('  Config: {}/.env'.format(cfg.PROJECT_DIR))
        print()
        if mode == 'cluster':
            api_url = ic_config.get_api_url() or ''
            has_key = bool(ic_config.get_api_key())
            print(bold('Cluster:'))
            print('  API URL: {}'.format(cyan(api_url) if api_url else yellow('not set')))
            print('  API Key: {}'.format(green('configured') if has_key else yellow('not set')))
            print()
        else:
            from cli.db import check_db, get_repo
            if check_db():
                from repositories.build_failure_repository import BuildFailureRepository
                s = get_repo(BuildFailureRepository).get_overview_stats()
                print(bold('Database:'))
                print('  Failures: {}'.format(s['total']))
                print('  Components: {}'.format(s['components']))
                print()
        print(cyan('Commands:'))
        print('  ic config set-app <app>      # Change application')
        print('  ic config use-cluster <url>  # Switch to cluster API')
        print('  ic config use-local          # Switch to local DB')
        print('  ic get apps                  # List applications')
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


@config.command('use-cluster')
@click.argument('api_url', required=False)
@click.option('--api-key', help='Bearer token for API auth')
@click.option('--no-verify-tls', is_flag=True, help='Skip TLS certificate verification')
def config_use_cluster(api_url, api_key, no_verify_tls):
    """Switch to cluster API mode."""
    from cli import ic_config
    from cli.formatting import cyan, green, yellow
    current = ic_config.load()
    if api_url:
        ic_config.set_cluster(api_url, api_key)
        if no_verify_tls:
            c = ic_config.load()
            c['cluster']['verify_tls'] = False
            ic_config._save(c)
    elif current['cluster'].get('api_url'):
        ic_config.set_mode('cluster')
    else:
        print(yellow('Usage: ic config use-cluster <api-url>'))
        return
    print(green('✓ Mode: cluster'))
    print('  API: {}'.format(cyan(ic_config.get_api_url())))
    try:
        from cli.api_client import APIClient
        url = ic_config.get_api_url()
        verify = ic_config.load().get('cluster', {}).get('verify_tls', True)
        client = APIClient(url, ic_config.get_api_key(), verify_tls=verify)
        health = client.get('/health')
        if health and health.get('status') == 'healthy':
            print(green('  Connection: healthy'))
        else:
            print(yellow('  Connection: unknown response'))
    except SystemExit:
        pass
    except Exception as e:
        print(yellow('  Connection: failed ({})'.format(e)))


@config.command('use-local')
def config_use_local():
    """Switch to local mode (API on localhost:8000)."""
    from cli import ic_config
    from cli.formatting import dim, green, yellow
    ic_config.set_mode('local')
    print(green('✓ Mode: local'))
    from cli.mode import has_api
    if has_api():
        print('  API: {}'.format(green('localhost:8000 (running)')))
    else:
        print('  API: {}'.format(yellow('localhost:8000 (not running)')))
        print('  {}'.format(dim("Start with: task serve")))


# --- watch group ---

@cli.group(invoke_without_command=True)
@click.pass_context
def watch(ctx):
    """Watch daemon commands."""
    if ctx.invoked_subcommand is None:
        click.echo("Usage: ic watch start")
        click.echo()
        click.echo("Start the event-driven watch daemon for real-time CI monitoring.")
        click.echo("Configure with: ic config watch list|add|remove|enable|disable")


@watch.command('start')
def watch_start():
    """Start the event-driven watch daemon."""
    import asyncio
    import signal

    from cli.formatting import bold, cyan, dim, green, red
    from config import CollectorConfig

    config = CollectorConfig.from_env()

    if not config.watcher or not config.watcher.applications:
        print(red('Error: No applications configured for watching.'))
        print()
        print('  Set {} in .env, or run:'.format(cyan('WATCH_APPLICATIONS')))
        print('    {}'.format(bold('ic config watch add <app>')))
        raise SystemExit(1)

    wc = config.watcher
    print(bold('Watch Daemon'))
    print()
    print('  Applications:  {}'.format(cyan(', '.join(wc.applications))))
    if wc.disabled:
        print('  Disabled:      {}'.format(', '.join(sorted(wc.disabled))))
    print('  Auto-analyze:  {}'.format(green('yes') if wc.auto_analyze else dim('no')))
    print('  Reconcile:     every {}s'.format(wc.reconcile_interval))
    print()
    print(dim('  Press Ctrl+C to stop.'))
    print()

    from watcher.daemon import WatchDaemon

    daemon = WatchDaemon(config)
    loop = asyncio.new_event_loop()

    def shutdown(sig):
        print()
        print(dim('Shutting down...'))
        loop.create_task(daemon.stop())

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown, sig)

    try:
        loop.run_until_complete(daemon.start())
    except KeyboardInterrupt:
        loop.run_until_complete(daemon.stop())
    finally:
        loop.close()
        print(dim('Daemon exited.'))


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
    from cli.mode import ensure_cluster
    from cli.formatting import bold, cyan, dim, green, red

    if ensure_cluster():
        from cli.data import get_watched_applications
        apps = get_watched_applications()
    else:
        apps = os.environ.get('WATCH_APPLICATIONS', '').split()

    if not apps:
        fallback = os.environ.get('APPLICATION_NAME', '')
        apps = [fallback] if fallback else []

    from config import ALL_WATCHERS

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
            wtype = 'Worker poll'
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
    from cli.mode import ensure_cluster
    from cli.formatting import cyan, green, yellow

    if ensure_cluster():
        from cli.data import add_watched_application
        apps = add_watched_application(app_name)
        if apps is not None:
            print(green('✓ Added to watch list: ') + cyan(app_name))
            print('  Now watching: {}'.format(', '.join(apps)))
            return

    from cli.db import check_db, get_repo
    from cli.formatting import red

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
                    quoted = '"{}"'.format(value) if ' ' in value else value
                    lines.append('{}={}\n'.format(var_name, quoted))
                    found = True
                else:
                    lines.append(line)
    if not found:
        quoted = '"{}"'.format(value) if ' ' in value else value
        lines.append('{}={}\n'.format(var_name, quoted))
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
        from cli.mode import ensure_cluster
        from cli.formatting import section_header
        if ensure_cluster():
            from cli.data import get_overview_stats
            s = get_overview_stats()
            section_header('Statistics')
            print()
            if isinstance(s, dict):
                for k, v in s.items():
                    if k not in ('application',):
                        print('  {:<25} {}'.format(k + ':', v))
            print()
            return
        from cli.db import get_repo, print_table, require_db
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
        from cli.mode import ensure_cluster
        if ensure_cluster():
            ctx.invoke(ai_status)
        else:
            _bash_fallback(['ai'])


@ai.command('analyze')
@click.argument('args', nargs=-1)
def ai_analyze(args):
    """Analyze a failure with AI.

    Works the same in local and cluster mode:
    - Reads failure data (from local DB or cluster API)
    - Runs LLM analysis using YOUR credentials (Vertex AI / Anthropic)
    - Saves result (to local DB or cluster API)
    """
    from cli.mode import is_cluster
    if len(args) >= 2 and args[0] == 'component':
        component = args[1]
        from cli.formatting import bold, cyan, green, red, yellow

        if is_cluster():
            from cli.data import require_data, get_failure_details, submit_analysis
            if not require_data():
                return
            failure = get_failure_details(component)
            if not failure:
                print(red('No failure found for: {}'.format(component)))
                from cli.suggest import format_suggestion
                from cli.data import get_alerts
                try:
                    alerts = get_alerts()
                    comps = [f.get('component', '') for f in alerts.get('build_failures', [])]
                    hint = format_suggestion(component, comps)
                    if hint:
                        print('  {}'.format(hint))
                except Exception:
                    pass
                return
        else:
            from cli.db import require_db
            if not require_db():
                return
            from repositories.build_failure_repository import BuildFailureRepository
            from cli.db import get_repo
            repo = get_repo(BuildFailureRepository)
            failure = repo.get_failure_details(component, cfg.APPLICATION_NAME)
            if not failure:
                print(red('No unresolved failures found for component: {}'.format(component)))
                return

        logs = failure.get('build_logs', '')
        error_msg = failure.get('error_message', '')

        if not logs and not error_msg:
            print(yellow('No logs or error info available for analysis'))
            return

        print(bold('Analyzing: ') + cyan(component))
        try:
            from config import CollectorConfig
            from analyzers.build_failure_analyzer import ANALYSIS_TOOL, BuildFailureAnalyzer
            config = CollectorConfig.from_env()
            if not config.llm:
                print(red('No LLM provider configured'))
                print(cyan('  Set LLM_PROVIDER + ANTHROPIC_API_KEY or ANTHROPIC_VERTEX_PROJECT_ID'))
                return

            if is_cluster():
                failure.setdefault('component_name', failure.get('component', component))
                failure.setdefault('id', 0)
                failure.setdefault('failed_task_name', failure.get('failed_task', ''))
                failure.setdefault('failed_step_name', failure.get('failed_step', ''))
                failure.setdefault('application', cfg.APPLICATION_NAME)
                failure.setdefault('namespace', cfg.NAMESPACE)
                failure.setdefault('repository_url', failure.get('repository_url', ''))
                failure.setdefault('commit_sha', failure.get('commit_sha', ''))
                failure.setdefault('commit_context', failure.get('commit_context'))

                from clients.llm_provider import create_llm_provider
                from cli.progress import Spinner
                llm = create_llm_provider(config.llm)
                analyzer = BuildFailureAnalyzer(config=config, llm=llm)
                with Spinner('Fetching context...'):
                    analyzer._ensure_context(failure)
                    analyzer._ensure_enrichment(failure)
                    analyzer._ensure_logs(failure)
                    system_prompt, user_prompt = analyzer.build_analysis_prompt(failure)
                import time as _time
                t0 = _time.time()
                with Spinner('Running AI analysis ({})...'.format(config.llm.model)):
                    response = llm.create_message(
                        system=system_prompt, user_content=user_prompt,
                        tools=[ANALYSIS_TOOL],
                    )
                analysis_duration = _time.time() - t0
                result = analyzer.parse_analysis_response(response)
                result['tokens_used'] = response.input_tokens + response.output_tokens
                result['cost_usd'] = (response.input_tokens * 0.000003) + (response.output_tokens * 0.000015)
                result['model_used'] = llm.model_name()

                print(green('  ✓ Analysis complete'))
                print()
                print(bold('Root cause:  ') + result.get('root_cause', '?'))
                print(bold('Category:    ') + result.get('failure_category', '?'))
                print(bold('Confidence:  ') + '{}%'.format(
                    int(result.get('confidence_score', 0) * 100)))
                print(bold('Fix:         ') + (result.get('recommended_fix', '?') or '?')[:100])
                print(bold('Duration:    ') + '{:.1f}s'.format(analysis_duration))
                print()

                submission = {
                    'component': component,
                    'analysis_type': 'build',
                    'model_used': result.get('model_used', config.llm.model),
                    'root_cause': result.get('root_cause', ''),
                    'failure_category': result.get('failure_category', ''),
                    'confidence_score': result.get('confidence_score', 0),
                    'recommended_fix': result.get('recommended_fix', ''),
                    'recommended_files': result.get('recommended_files', []),
                    'can_auto_fix': result.get('can_auto_fix', False),
                    'tokens_used': result.get('tokens_used', 0),
                    'cost_usd': result.get('cost_usd', 0),
                    'analysis_json': result,
                }
                upload = submit_analysis(component, submission)
                if upload and upload.get('action') == 'stored':
                    print(green('  ✓ Stored in cluster'))
            else:
                _bash_fallback(['ai', 'analyze', 'component', component])

        except Exception as e:
            print(red('Analysis failed: {}'.format(e)))
        return
    _bash_fallback(['ai', 'analyze'] + list(args))


@ai.command('batch')
@click.argument('args', nargs=-1)
def ai_batch(args):
    """Batch AI analysis — analyze all pending failures."""
    from cli.mode import is_cluster
    if is_cluster():
        from cli.data import require_data, get_alerts, get_failure_details, submit_analysis
        from cli.formatting import bold, cyan, green, red, yellow
        if not require_data():
            return
        alerts = get_alerts()
        failures = alerts.get('build_failures', [])
        pending = [f for f in failures if not f.get('has_analysis')]
        if not pending:
            print(green('No pending failures to analyze'))
            return
        print(bold('Batch analyzing {} failure(s)...'.format(len(pending))))
        print()
        try:
            from config import CollectorConfig
            from analyzers.build_failure_analyzer import ANALYSIS_TOOL, BuildFailureAnalyzer
            from clients.llm_provider import create_llm_provider
            config = CollectorConfig.from_env()
            if not config.llm:
                print(red('No LLM provider configured'))
                print(cyan('  Set LLM_PROVIDER + ANTHROPIC_VERTEX_PROJECT_ID'))
                return
            llm = create_llm_provider(config.llm)
            analyzer = BuildFailureAnalyzer(config=config, llm=llm)
        except Exception as e:
            print(red('Failed to initialize analyzer: {}'.format(e)))
            return
        success = 0
        for i, f in enumerate(pending):
            comp = f.get('component', '?')
            print('[{}/{}] {}...'.format(i + 1, len(pending), bold(comp)), end=' ', flush=True)
            try:
                failure = get_failure_details(comp)
                if not failure:
                    print(yellow('not found'))
                    continue
                failure.setdefault('component_name', comp)
                failure.setdefault('id', 0)
                failure.setdefault('failed_task_name', failure.get('failed_task', ''))
                failure.setdefault('failed_step_name', failure.get('failed_step', ''))
                failure.setdefault('application', cfg.APPLICATION_NAME)
                failure.setdefault('namespace', cfg.NAMESPACE)
                analyzer._ensure_context(failure)
                analyzer._ensure_logs(failure)
                system_prompt, user_prompt = analyzer.build_analysis_prompt(failure)
                import time as _time
                t0 = _time.time()
                response = llm.create_message(
                    system=system_prompt, user_content=user_prompt,
                    tools=[ANALYSIS_TOOL],
                )
                dur = _time.time() - t0
                result = analyzer.parse_analysis_response(response)
                result['tokens_used'] = response.input_tokens + response.output_tokens
                result['cost_usd'] = (response.input_tokens * 0.000003) + (response.output_tokens * 0.000015)
                result['model_used'] = llm.model_name()
                submission = {
                    'component': comp,
                    'analysis_type': 'build',
                    'model_used': result.get('model_used', ''),
                    'root_cause': result.get('root_cause', ''),
                    'failure_category': result.get('failure_category', ''),
                    'confidence_score': result.get('confidence_score', 0),
                    'recommended_fix': result.get('recommended_fix', ''),
                    'recommended_files': result.get('recommended_files', []),
                    'can_auto_fix': result.get('can_auto_fix', False),
                    'tokens_used': result.get('tokens_used', 0),
                    'cost_usd': result.get('cost_usd', 0),
                    'analysis_json': result,
                }
                upload = submit_analysis(comp, submission)
                if upload and upload.get('action') == 'stored':
                    print(green('✓') + ' {}% {:.0f}s'.format(
                        int(result.get('confidence_score', 0) * 100), dur))
                    success += 1
                else:
                    print(yellow('analyzed but save failed'))
            except Exception as e:
                print(red('✗ {}'.format(str(e)[:60])))
        print()
        print(bold('Done: {}/{} analyzed'.format(success, len(pending))))
        return
    _bash_fallback(['ai', 'batch'] + list(args))


@ai.command('status')
def ai_status():
    """Show AI analysis summary."""
    from cli.mode import ensure_cluster
    if ensure_cluster():
        from cli.data import get_overview_stats
        from cli.formatting import bold, cyan, green, section_header
        data = get_overview_stats()
        section_header('AI Analysis Status')
        print()
        bf = data.get('build_failures', {})
        cv = data.get('conforma_violations', {})
        print(bold('Build Failures:'))
        print('  Pending:    {}'.format(bf.get('pending', 0)))
        print('  Analyzed:   {}'.format(bf.get('analyzed', 0)))
        print('  Fixable:    {}'.format(bf.get('autofixable', 0)))
        print()
        print(bold('Conforma Violations:'))
        print('  Pending:    {}'.format(cv.get('pending', 0)))
        print('  Analyzed:   {}'.format(cv.get('analyzed', 0)))
        print()
        cost = data.get('total_cost_30d', 0)
        print(bold('Total cost:') + '   ${} (last 30 days)'.format(cost))
        print()
        return
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
    from cli.mode import ensure_cluster
    if ensure_cluster():
        from cli.api_client import get_client
        from cli.formatting import bold, section_header
        data = get_client().get('/api/v1/applications/{}/analyses/stats'.format(cfg.APPLICATION_NAME))
        if data:
            section_header('AI Analysis Statistics')
            print()
            for k, v in data.items():
                if k != 'application':
                    print('  {:<25} {}'.format(k + ':', v))
            print()
        else:
            print('No AI analysis data')
        return
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


@ai.command('quality')
@click.option('--calibration', is_flag=True, help='Show calibration analysis')
def ai_quality(calibration):
    """AI analysis quality metrics — accuracy from verdicts."""
    from cli.mode import is_cluster
    from cli.formatting import bold, cyan, green, red, section_header, yellow

    if is_cluster():
        from cli.api_client import get_client
        data = get_client().get('/api/v1/metrics/ai-quality',
                                params={'application': cfg.APPLICATION_NAME})
    else:
        from cli.db import get_repo, require_db
        from repositories.ai_analysis_repository import AIAnalysisRepository
        if not require_db():
            return
        ai_repo = get_repo(AIAnalysisRepository)
        data = ai_repo.get_quality_metrics(cfg.APPLICATION_NAME)

    if not data or not data.get('total_with_verdict'):
        print('  No verdicts recorded yet.')
        print('  Verdicts are recorded automatically when builds pass after AI analysis,')
        print('  or manually via: ic triage resolve <id> --verdict correct|partial|incorrect')
        return

    section_header('AI Quality Metrics')
    print()
    total = data['total_with_verdict']
    acc = data.get('accuracy')
    acc_str = '{}%'.format(int(acc * 100)) if acc is not None else '-'
    color = green if acc and acc >= 0.8 else yellow if acc and acc >= 0.6 else red

    print('  Accuracy:     {}'.format(color(acc_str)))
    print('  Verdicts:     {} total'.format(bold(str(total))))
    print('    Correct:    {}'.format(green(str(data['correct']))))
    print('    Partial:    {}'.format(yellow(str(data['partial']))))
    print('    Incorrect:  {}'.format(red(str(data['incorrect']))))
    if data.get('avg_confidence_correct') is not None:
        print('  Avg conf (correct):   {}%'.format(
            int(data['avg_confidence_correct'] * 100)))
    if data.get('avg_confidence_incorrect') is not None:
        print('  Avg conf (incorrect): {}%'.format(
            int(data['avg_confidence_incorrect'] * 100)))

    if data.get('by_category'):
        print()
        print('  {}'.format(bold('By Category:')))
        for cat, stats in data['by_category'].items():
            cat_acc = (stats['correct'] + stats['partial'] * 0.5) / stats['total'] if stats['total'] else 0
            print('    {:<30} {}/{} ({}%)'.format(
                cyan(cat), stats['correct'], stats['total'], int(cat_acc * 100)))

    if calibration and not is_cluster():
        _show_calibration_dashboard(ai_repo, bold, cyan, green, red, yellow, section_header)
    print()


def _show_calibration_dashboard(ai_repo, bold, cyan, green, red, yellow, section_header):
    """Display calibration analysis: predicted vs actual accuracy."""
    from analyzers.calibration import CalibrationService
    from analyzers.feature_weights import FeatureWeightService
    from analyzers.bayesian_priors import BayesianPriorService
    from cli.db import get_db

    print()
    section_header('Calibration Analysis')

    cal_service = CalibrationService(ai_repo)
    curve = cal_service.compute_calibration_curve()

    if not curve:
        print('  No calibration data yet (need verdicts with confidence scores).')
        return

    print()
    print('  {:<12} {:<10} {:<10} {}'.format(
        bold('Predicted'), bold('Actual'), bold('Samples'), bold('Status')))
    for bucket in curve:
        lo = int(bucket.bucket_min * 100)
        hi = int(bucket.bucket_max * 100)
        actual_pct = int(bucket.actual_accuracy * 100)
        diff = bucket.actual_accuracy - bucket.predicted_confidence
        if abs(diff) < 0.1:
            status = green('well-calibrated')
        elif diff > 0:
            status = cyan('underconfident')
        else:
            status = yellow('overconfident')
        print('  {:<12} {:<10} {:<10} {}'.format(
            '{}-{}%'.format(lo, hi), '{}%'.format(actual_pct),
            str(bucket.sample_size), status))

    score = cal_service.compute_calibration_score()
    if score is not None:
        color = green if score >= 0.85 else yellow if score >= 0.7 else red
        print()
        print('  Calibration Score: {} ({})'.format(
            color(str(score)),
            'excellent' if score >= 0.9 else 'good' if score >= 0.8 else
            'fair' if score >= 0.7 else 'poor'))

    try:
        db = get_db()
        weight_service = FeatureWeightService(db)
        importance = weight_service.get_feature_importance()
        if importance:
            print()
            print('  {}'.format(bold('Feature Importance (learned):')))
            for fi in importance:
                bar = int(fi['weight'] * 5)
                indicator = green('*' * bar) if fi['weight'] >= 1.0 else yellow('*' * max(1, bar))
                print('    {:<30} {:<6} {} ({} samples)'.format(
                    cyan(fi['name']), str(fi['weight']), indicator, fi['sample_size']))
    except Exception:
        pass

    try:
        prior_service = BayesianPriorService(ai_repo)
        stats = ai_repo.get_verdict_stats_by_category('build')
        if stats:
            print()
            print('  {}'.format(bold('Bayesian Priors (by category):')))
            for row in stats[:10]:
                prior = prior_service.get_prior(row['category'])
                flag = '*' if prior.is_bootstrapped else ''
                print('    {:<30} P(correct)={:<6} ({} verdicts{})'.format(
                    cyan(row['category']),
                    '{:.2f}'.format(prior.prior_correct),
                    prior.sample_size,
                    ' bootstrapped' if flag else ''))
    except Exception:
        pass


# --- Top-level commands ---

from cli.commands.triage import triage  # noqa: E402
cli.add_command(triage)


@cli.command()
@click.argument('name')
@click.option('--prs', is_flag=True, default=False, help='Also show open GitHub PRs')
@click.pass_context
def source(ctx, name, prs):
    """Show component source info: repo, branch, image, and optionally open PRs."""
    import os
    from cli.formatting import bold, cyan, dim, red, section_header, yellow
    from clients.kubernetes import KubernetesClient
    from openshift_auth import _ensure_k8s_config

    is_json = ctx.obj.get('json') if ctx.obj else False

    _ensure_k8s_config()
    kc = KubernetesClient(namespace=cfg.NAMESPACE)
    meta = kc.get_component_metadata(name)

    if not meta:
        print(red('Component not found: {}'.format(name)))
        return

    if is_json and not prs:
        print(json_mod.dumps({'component': name, **meta}, default=str, indent=2))
        return

    section_header('Component: {}'.format(name))
    print()
    print('  {}  {}'.format(bold('Repo:'), cyan(meta.get('repository_url', '-'))))
    print('  {}  {}'.format(bold('Branch:'), meta.get('branch', '-')))
    print('  {}  {}'.format(bold('Image:'), meta.get('container_image', '-')[:80]))
    last_commit = meta.get('last_built_commit', '')
    if last_commit:
        print('  {}  {}'.format(bold('Last built:'), last_commit[:12]))
    promoted = meta.get('last_promoted_image', '')
    if promoted:
        print('  {}  {}'.format(bold('Promoted:'), promoted[:80]))
    nudges = meta.get('nudges', [])
    if nudges:
        print('  {}  {}'.format(bold('Nudges:'), ', '.join(nudges[:5])))
    print()

    if not prs:
        print(dim("Tip: Use '--prs' to also show open GitHub PRs"))
        print()
        return

    repo_url = meta.get('repository_url', '')
    branch = meta.get('branch', '')
    if not repo_url:
        print(yellow('No repository URL — cannot check PRs'))
        return

    from clients.github_client import GitHubClient, parse_github_repo
    parsed = parse_github_repo(repo_url)
    if not parsed:
        print(yellow('Cannot parse GitHub URL: {}'.format(repo_url)))
        return
    owner, repo = parsed

    token = os.environ.get('GITHUB_TOKEN', '')
    gh = GitHubClient(token)
    open_prs = gh.list_pull_requests(owner, repo, base=branch, state='open', limit=10)

    if is_json:
        print(json_mod.dumps({
            'component': name, **meta,
            'open_prs': open_prs,
        }, default=str, indent=2))
        return

    if not open_prs:
        print(dim('  No open PRs against {}'.format(branch)))
    else:
        print(bold('  Open PRs against {}:'.format(branch)))
        for p in open_prs:
            print('    #{} {} ({})'.format(p['number'], p['title'][:60], cyan(p['author'])))
            print('      {}'.format(dim(p['url'])))
    print()


@cli.command()
@click.option('--days', type=int, default=None, help='Only show components resolved in the last N days')
@click.option('--since', type=str, default=None, help='Only show components resolved since date (YYYY-MM-DD)')
@click.option('--to', type=str, default=None, help='Upper bound date (YYYY-MM-DD), use with --since for a range')
@click.pass_context
def resolved(ctx, days, since, to):
    """Resolved components."""
    import re
    from cli.db import get_repo, print_table, require_db
    from cli.formatting import cyan, dim, red, section_header
    from repositories.build_failure_repository import BuildFailureRepository
    date_re = re.compile(r'^\d{4}-\d{2}-\d{2}$')
    for label, val in [('--since', since), ('--to', to)]:
        if val and not date_re.match(val):
            print(red('Error:') + ' {} must be YYYY-MM-DD, got: {}'.format(label, val))
            return
    if not require_db():
        return
    rows = get_repo(BuildFailureRepository).get_resolved_components(
        cfg.APPLICATION_NAME, days=days, since=since, to=to,
    )

    is_json = ctx.obj.get('json') if ctx.obj else False
    if is_json:
        print(json_mod.dumps({
            'application': cfg.APPLICATION_NAME,
            'days': days,
            'since': since,
            'to': to,
            'items': rows,
        }, default=str, indent=2))
        return

    title = 'Resolved Components'
    if since and to:
        title += ' — {} to {}'.format(since, to)
    elif since:
        title += ' — since {}'.format(since)
    elif days:
        title += ' — last {} day{}'.format(days, 's' if days != 1 else '')
    section_header(title)
    print()
    if not rows:
        label = ''
        if since:
            label = ' since {}'.format(since)
        elif days:
            label = ' in the last {} day{}'.format(days, 's' if days != 1 else '')
        print('  No resolved components{}.'.format(label))
        print()
        return
    headers = ['Component', 'Resolved', 'Fixed', 'Commit', 'Build']
    table_rows = []
    for r in rows:
        resolved_date = str(r['resolved_at'])[:16] if r['resolved_at'] else ''
        sha = (r.get('commit_sha') or '')[:7]
        url = r.get('resolution_url') or ''
        pipelinerun = url.rsplit('/', 1)[-1] if url and 'retrigger-resolved' not in url else ''
        table_rows.append((r['component'], resolved_date, r['count'], sha, pipelinerun))
    print_table(headers, table_rows)
    print()
    print(dim('{} resolved component(s)'.format(len(rows))))
    print()
    print(cyan('Tip:') + " Use '--json' for full URLs or 'ic history <component>' for details")
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
@click.argument('application', required=False)
@click.option('--json', 'output_json', is_flag=True, hidden=True)
@click.option('--no-cache', is_flag=True, help='Skip ref cache, query GitHub fresh')
@click.option('--no-diagnose', is_flag=True, help='Skip trigger diagnosis')
@click.pass_context
def stale(ctx, application, output_json, no_cache, no_diagnose):
    """Detect components with untriggered commits."""
    import json as json_mod
    from cli.formatting import bold, cyan, dim, green, red, section_header, yellow
    from proactive.health_monitor import HealthMonitor

    output_json = output_json or (ctx.obj.get('json') if ctx.obj else False)
    app = application or cfg.APPLICATION_NAME

    monitor = HealthMonitor(db=None)
    result = monitor.get_stale_components(
        app, use_cache=not no_cache, diagnose=not no_diagnose)

    if output_json:
        print(json_mod.dumps(result, indent=2, default=str))
        return

    if result.get('error'):
        print(red('Error: {}'.format(result['error'])))
        return

    section_header('Stale Components: {}'.format(app))
    print()

    stale_list = result.get('stale', [])
    if not stale_list:
        if result.get('warning'):
            print(yellow('Warning: ' + result['warning']))
            print(dim('  Results may be incomplete — set GITHUB_TOKEN for reliable checks'))
        else:
            print(green('All components up to date'))
        _print_stale_footer(result, dim, yellow, cyan, bold)
        return

    print(bold(yellow('{} component(s) with untriggered commits:'.format(len(stale_list)))))
    print()
    from cli.db import print_table

    has_diagnosis = any(s.get('diagnosis') for s in stale_list)
    if has_diagnosis:
        print_table(
            ['Component', 'Branch', 'Built', 'HEAD', 'Diagnosis'],
            [(s['component'], s['branch'],
              s['built_commit'], s['head_commit'],
              _short_diagnosis(s.get('diagnosis', {})))
             for s in stale_list],
        )
    else:
        print_table(
            ['Component', 'Branch', 'Built', 'HEAD', 'Repo'],
            [(s['component'], s['branch'],
              s['built_commit'], s['head_commit'],
              s['repository_url'].rstrip('/').split('/')[-1])
             for s in stale_list],
        )

    print()
    _print_stale_footer(result, dim, yellow, cyan, bold)


def _short_diagnosis(diag):
    """Format diagnosis cause for table display."""
    labels = {
        'missing_pac_repository': 'No webhook',
        'nudge_misconfiguration': 'Bad nudge',
        'recent_build_failed': 'Build failed',
        'build_running': 'Building...',
        'unknown': '?',
    }
    return labels.get(diag.get('cause', ''), diag.get('cause', '?'))


def _print_stale_footer(result, dim, yellow, cyan, bold):
    cache = result.get('cache', {})
    api_calls = result.get('api_calls', result.get('unique_refs', 0))
    parts = ['Checked {} components'.format(result.get('checked', 0))]
    if result.get('skipped'):
        parts.append('{} skipped'.format(result['skipped']))
    parts.append('{} GitHub API calls'.format(api_calls))
    if cache:
        parts.append('{} cache hits'.format(cache.get('hits', 0)))
    print(dim('  ' + ' — '.join(parts)))
    if result.get('warning'):
        print(yellow('Warning: ' + result['warning']))
    print()
    print(cyan('Tip:') + ' A stale component means a push to the branch did not trigger a build.')
    print('  Use {} to see component details.'.format(bold('ic source <component>')))
    print()


@cli.command()
@click.argument('application', required=False)
@click.option('--json', 'output_json', is_flag=True, hidden=True)
@click.pass_context
def nightly(ctx, application, output_json):
    """Nightly build status: FBC fragment health + blockers."""
    import json as json_mod
    from cli.db import require_db
    from cli.formatting import bold, cyan, dim, green, red, section_header, yellow
    from config import CollectorConfig
    from proactive.health_monitor import HealthMonitor
    from repositories.connection import DatabaseConnection

    output_json = output_json or (ctx.obj.get('json') if ctx.obj else False)
    app = application or cfg.APPLICATION_NAME

    try:
        conf = CollectorConfig.from_env()
        db = DatabaseConnection(conf.db)
    except Exception:
        if not require_db():
            return
        return

    monitor = HealthMonitor(db)
    status = monitor.get_nightly_status(app)

    if output_json:
        print(json_mod.dumps(status, indent=2, default=str))
        return

    section_header('Nightly Status: {}'.format(app))
    print()

    fbc = status.get('fbc_health')
    fbc_name = status.get('fbc_component', '')
    if fbc:
        score = fbc.get('health_score')
        score_str = '{}%'.format(score) if score is not None else 'N/A'
        st = fbc.get('current_status') or 'unknown'
        color = green if st == 'Succeeded' else red if st == 'Failed' else yellow
        print(bold('FBC Fragment: ') + fbc_name)
        print('  Status: {}  Health: {}'.format(color(st), score_str))
        last = fbc.get('last_successful_build')
        if last:
            print('  Last success: {}'.format(str(last)[:16]))
    else:
        print(bold('FBC Fragment: ') + fbc_name)
        print('  ' + dim('No health data found'))

    if status.get('fbc_image'):
        print('  Image: {}'.format(status['fbc_image'][:80]))

    conforma = status.get('fbc_conforma')
    if conforma and (conforma.get('violations_count', 0) > 0 or conforma.get('warnings_count', 0) > 0):
        print('  Conforma: {} violations, {} warnings'.format(
            conforma['violations_count'], conforma['warnings_count']))

    print()

    blockers = status.get('blockers', [])
    if blockers:
        print(bold(red('{} blocker(s):'.format(len(blockers)))))
        print()
        for b in blockers:
            err = b.get('error_type') or b.get('failure_category') or 'unknown'
            print('  {} — {}'.format(bold(b['component']), err))
            if b.get('root_cause_summary'):
                print('    {}'.format(dim(b['root_cause_summary'])))
            if b.get('last_successful_build'):
                print('    Last success: {}'.format(str(b['last_successful_build'])[:16]))
    else:
        print(green('No build blockers'))

    gha = status.get('gha_validation')
    if gha:
        conclusion = gha.get('conclusion', 'unknown')
        gha_color = green if conclusion == 'success' else red if conclusion == 'failure' else yellow
        print(bold('GHA Validation:') + ' {} ({})'.format(
            gha_color(conclusion), gha.get('created_at', '')[:10]))
        if gha.get('url'):
            print('  {}'.format(dim(gha['url'])))
    print()
    print(cyan('Tip:') + ' Use {} for details on a blocker'.format(bold('ic describe <component>')))
    print()


@cli.command('nightly-history')
@click.argument('application', required=False)
@click.option('--days', type=int, default=14, help='Number of days to show')
@click.option('--json', 'output_json', is_flag=True, hidden=True)
@click.pass_context
def nightly_history(ctx, application, days, output_json):
    """Nightly build history: operator builds + FBC fragment freshness."""
    import json as json_mod
    from cli.formatting import bold, dim, green, red, section_header, yellow

    output_json = output_json or (ctx.obj.get('json') if ctx.obj else False)
    app = application or cfg.APPLICATION_NAME


    from cli.mode import ensure_cluster
    if ensure_cluster():
        from cli.api_client import get_client
        data = get_client().get('/api/v1/applications/{}/nightly/history'.format(app),
                                params={'days': days})
    else:
        from config import CollectorConfig
        from repositories.build_failure_repository import BuildFailureRepository
        from repositories.connection import DatabaseConnection
        conf = CollectorConfig.from_env()
        db = DatabaseConnection(conf.db)
        repo = BuildFailureRepository(db)
        data = repo.get_nightly_history(app, days=days)

    if not data:
        from cli.formatting import yellow
        print(yellow('No nightly history data found'))
        return

    if output_json:
        print(json_mod.dumps(data, indent=2, default=str))
        return

    section_header('Nightly History: {} (last {} days)'.format(app, days))
    print()

    builds = data.get('nightly_builds', [])
    fbc_list = data.get('fbc_fragments', [])

    if not builds:
        print(dim('  No nightly operator builds found'))
        print()
    else:
        from cli.db import print_table
        rows = []
        for b in builds:
            st = b.get('status', 'Unknown')
            color_fn = green if st == 'Succeeded' else red
            date_str = str(b.get('build_date', ''))[:10]
            pr_short = (b.get('pipelinerun_name', '') or '')[-20:]
            rows.append((date_str, pr_short, color_fn(st)))
        print(bold('Operator Builds (trigger_type=nightly):'))
        print_table(['Date', 'PipelineRun', 'Status'], rows)
        print()

    if fbc_list:
        print(bold('FBC Fragment Status:'))
        for fbc in fbc_list:
            name = fbc.get('component_name', '')
            last = fbc.get('last_successful_build')
            st = fbc.get('current_status', 'unknown')
            if last:
                from datetime import datetime, timezone
                now = datetime.now(timezone.utc)
                if hasattr(last, 'tzinfo') and last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                elif isinstance(last, str):
                    last = datetime.fromisoformat(last.replace('Z', '+00:00'))
                hours_ago = (now - last).total_seconds() / 3600
                freshness = green('fresh ({:.0f}h)'.format(hours_ago)) if hours_ago < 24 \
                    else yellow('aging ({:.0f}h)'.format(hours_ago)) if hours_ago < 48 \
                    else red('stale ({:.0f}h)'.format(hours_ago))
            else:
                freshness = red('no successful build')
            print('  {} — {}'.format(name, freshness))
        print()


@cli.command()
@click.argument('image_ref')
@click.pass_context
def lookup(ctx, image_ref):
    """Find which component produced a given image or digest."""
    import json as json_mod
    from cli.db import get_repo, print_table, require_db
    from cli.formatting import bold, cyan, green, section_header
    from repositories.build_failure_repository import BuildFailureRepository
    output_json = ctx.obj.get('json') if ctx.obj else False

    db_matches = []
    try:
        if require_db():
            db_matches = get_repo(BuildFailureRepository).find_by_image(image_ref)
    except SystemExit:
        pass

    cluster_matches = []
    all_comps = []
    url_component = None
    try:
        from clients.kubernetes import KubernetesClient
        kc = KubernetesClient(namespace=cfg.NAMESPACE)
        all_comps = kc.list_components()
        term = image_ref.lower()
        cluster_matches = [c for c in all_comps if term in (c.get('container_image') or '').lower()]

        if not cluster_matches and '/' in image_ref:
            path = image_ref.split('/')[-1].split('@')[0].split(':')[0]
            if path:
                url_component = path
                cluster_matches = [c for c in all_comps if c.get('name', '') == path]
                if not cluster_matches:
                    import re
                    core = re.sub(r'-(rhel[0-9]+|v[0-9]+-[0-9]+-.*|ea-[0-9]+.*)$', '', path)
                    if core and core != path:
                        cluster_matches = [c for c in all_comps
                                           if c.get('name', '').startswith(core)]
    except Exception:
        pass

    quay_match = None
    if not db_matches and not cluster_matches:
        quay_match = _resolve_via_quay(image_ref, all_comps)

    if output_json:
        result = {
            'query': image_ref,
            'db_matches': db_matches,
            'cluster_matches': cluster_matches,
        }
        if url_component:
            result['url_component'] = url_component
        if quay_match:
            result['quay_match'] = quay_match
        print(json_mod.dumps(result, indent=2, default=str))
        return

    section_header('Image Lookup: {}'.format(image_ref[:60]))
    print()

    if db_matches:
        print(bold('Found in build records:'))
        print_table(
            ['Component', 'Application', 'Status', 'PipelineRun', 'Date'],
            [(m['component_name'], m['application'],
              'Resolved' if m['is_resolved'] else m['status'],
              m['pipelinerun_name'], m.get('first_detected_at', ''))
             for m in db_matches],
        )
        print()
        for m in db_matches[:3]:
            if m.get('repository_url'):
                print('  Repo: {}  Branch: {}'.format(m['repository_url'], m.get('branch', '')))
    else:
        print('  No matches in build records.')

    print()
    if cluster_matches:
        if url_component:
            print(bold('Found via URL path (component: {}):'.format(url_component)))
        else:
            print(bold('Found in cluster components:'))
        print_table(
            ['Component', 'Application', 'Image'],
            [(c['name'], c['application'], c['container_image'][:70]) for c in cluster_matches],
        )
    else:
        print('  No matches in cluster components.')
    print()

    if quay_match:
        print(bold('Resolved via Quay manifest (per-arch image):'))
        print('  {} {}'.format(green('Component:'), quay_match.get('component', '')))
        print('  {} {}/{}'.format(
            green('Platform:'),
            quay_match.get('os', ''), quay_match.get('architecture', '')))
        print('  {} {}'.format(green('Manifest list tag:'), quay_match.get('manifest_list_tag', '')))
        print()

    if not db_matches and not cluster_matches and not quay_match:
        print(cyan('No matches found. Try a longer digest or full image URL.'))
        print()


def _resolve_via_quay(image_ref, all_comps):
    """Try to resolve a per-arch digest via Quay manifest list API."""
    from clients.registry_client import RegistryClient

    target_digest = image_ref.strip()
    if not target_digest.startswith('sha256:'):
        if '@sha256:' in image_ref:
            target_digest = image_ref.split('@')[-1]
        else:
            return None

    if len(target_digest) < 20:
        return None

    rc = RegistryClient()

    if '/' in image_ref and '@' in image_ref:
        registry, repository, _ = rc.parse_image_ref(image_ref)
        result = rc.resolve_per_arch_digest(registry, repository, target_digest)
        if result:
            comp_name = repository.rsplit('/', 1)[-1] if '/' in repository else repository
            result['component'] = comp_name
            return result

    for comp in all_comps[:20]:
        ci = comp.get('container_image', '')
        if not ci:
            continue
        registry, repository, _ = rc.parse_image_ref(ci)
        result = rc.resolve_per_arch_digest(registry, repository, target_digest)
        if result:
            result['component'] = comp.get('name', '')
            return result

    return None


@cli.command('snapshot-check')
@click.pass_context
def snapshot_check(ctx):
    """Check snapshot freshness — detect stale or failed build images."""
    import json as json_mod
    from cli.formatting import bold, cyan, green, red, section_header, yellow
    from proactive.health_monitor import HealthMonitor

    output_json = ctx.obj.get('json') if ctx.obj else False
    monitor = HealthMonitor(db=None)
    result = monitor.check_snapshot_freshness()

    if output_json:
        print(json_mod.dumps(result, indent=2, default=str))
        return

    if result.get('error'):
        print(red('Error: {}'.format(result['error'])))
        raise SystemExit(1)

    section_header('Snapshot Freshness — {}'.format(result.get('application', '')))
    print()
    print('{}  {}'.format(bold('Snapshot:'), cyan(result.get('snapshot', ''))))
    print('{}  {}'.format(bold('Created:'), result.get('snapshot_created', '')))
    print('{}  {}'.format(bold('Components:'), result.get('total_components', 0)))
    print()

    stale = result.get('stale', [])
    no_builds = result.get('no_builds', [])
    fresh_count = result.get('fresh_count', 0)

    if not stale and not no_builds:
        print(green('All {} components are fresh — snapshot matches latest builds.'.format(fresh_count)))
        print()
        return

    print('{} fresh, {} stale, {} no builds'.format(
        green(str(fresh_count)), red(str(len(stale))), yellow(str(len(no_builds)))))
    print()

    if stale:
        print(bold('Stale components:'))
        print()
        for s in stale:
            reason = s.get('reason', '')
            if reason == 'latest_build_failed':
                print('  {} {}'.format(red('FAILED'), bold(s['component'])))
                print('    Latest build failed: {} ({})'.format(
                    s.get('latest_build_pr', ''), s.get('latest_build_date', '')[:10]))
                print('    Snapshot has pre-failure image')
            elif reason == 'newer_build_available':
                print('  {} {}'.format(yellow('STALE'), bold(s['component'])))
                print('    Newer build available: {}'.format(s.get('latest_build_date', '')[:10]))
            print()

    if no_builds:
        print(bold('No builds found ({}):'.format(len(no_builds))))
        for nb in no_builds:
            print('  {} {}'.format(yellow('?'), nb['component']))
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
    from cli.mode import ensure_cluster
    if ensure_cluster() and args:
        component = args[0]
        from cli.formatting import cyan, section_header
        section_header('Fix: {}'.format(component))
        print()
        print('  Available actions:')
        print('    {} — analyze with AI'.format(cyan('ic ai analyze component {}'.format(component))))
        print('    {} — create Jira ticket'.format(cyan('ic export {} jira'.format(component))))
        print('    {} — generate Slack message'.format(cyan('ic export {} slack'.format(component))))
        print('    {} — view failure details'.format(cyan('ic describe component {}'.format(component))))
        print()
        return
    _bash_fallback(['fix'] + list(args))


@cli.command('export')
@click.argument('args', nargs=-1)
def export_cmd(args):
    """Export analysis to ticket format (e.g. ic export component-name slack)."""
    import json as json_mod
    from cli.mode import ensure_cluster
    if ensure_cluster() and len(args) >= 1:
        from cli.data import get_export
        component = args[0]
        fmt = args[1] if len(args) > 1 else 'slack'
        data = get_export(component, fmt)
        if data:
            if isinstance(data, dict) and 'text' in data:
                print(data['text'])
            else:
                print(json_mod.dumps(data, indent=2, default=str))
        else:
            print('No export data for: {}'.format(component))
        return
    _bash_fallback(['export'] + list(args))


# --- conforma group ---

@cli.group(invoke_without_command=True)
@click.pass_context
def conforma(ctx):
    """Conforma (Enterprise Contract) commands."""
    if ctx.invoked_subcommand is None:
        from cli.mode import ensure_cluster
        if ensure_cluster():
            ctx.invoke(conforma_report)
        else:
            _bash_fallback(['conforma'])


@conforma.command('report')
@click.option('--stage', is_flag=True, help='Show stage policy violations (from reporter)')
@click.option('--nightly', is_flag=True, help='Show nightly build violations (from reporter)')
@click.argument('args', nargs=-1)
def conforma_report(stage, nightly, args):
    """Daily Conforma violations report."""
    import json as json_mod
    from cli.mode import ensure_cluster
    use_reporter = stage or nightly
    if use_reporter or ensure_cluster():
        from cli.formatting import dim, red, section_header, yellow
        if stage or nightly:
            from cli.data import get_conforma_violations
            reporter_env = 'stage' if stage else None
            reporter_build_type = 'nightly' if nightly else None
            data = get_conforma_violations(
                reporter_env=reporter_env, reporter_build_type=reporter_build_type)
        else:
            from cli.data import get_conforma_report
            data = get_conforma_report()
        if isinstance(data, str):
            print(data)
        elif isinstance(data, dict):
            print(json_mod.dumps(data, indent=2, default=str))
        elif isinstance(data, list):
            section_header('Conforma Report')
            print()
            for item in data:
                comp = item.get('component_name', item.get('component', '?'))
                viol = item.get('violations_count', 0) or 0
                warn = item.get('warnings_count', 0) or 0
                scenario = item.get('scenario', '')
                viol_color = red if viol > 0 else yellow
                print('  {:<45} {} viol  {} warn  {}'.format(
                    comp[:45], viol_color(str(viol).rjust(3)), str(warn).rjust(3), dim(scenario[:30])))
            print()
        else:
            print(data)
        return
    _bash_fallback(['conforma', 'report'] + list(args))


@conforma.command('categories')
@click.option('--stage', is_flag=True, help='Show stage policy violations (from reporter)')
@click.option('--nightly', is_flag=True, help='Show nightly build violations (from reporter)')
@click.argument('args', nargs=-1)
def conforma_categories(stage, nightly, args):
    """Violation categories across components."""
    from cli.mode import ensure_cluster
    use_reporter = stage or nightly
    if use_reporter or ensure_cluster():
        from cli.data import get_conforma_violations
        from cli.formatting import bold, section_header
        from utils.conforma_utils import categorize_policy, count_unique_violations
        reporter_env = 'stage' if stage else None
        reporter_build_type = 'nightly' if nightly else None
        data = get_conforma_violations(
            reporter_env=reporter_env, reporter_build_type=reporter_build_type)
        groups = {}
        scenarios = {}
        if isinstance(data, list):
            for v in data:
                scenario = v.get('scenario', v.get('error_type', 'unknown'))
                cat = categorize_policy(scenario)
                uv = v.get('unique_violations') or 0
                if not uv:
                    uv, _ = count_unique_violations(v.get('violation_summary', ''))
                if cat not in groups:
                    groups[cat] = {'components': 0, 'violations': 0}
                groups[cat]['components'] += 1
                groups[cat]['violations'] += uv
                scenarios[scenario] = scenarios.get(scenario, 0) + 1
        section_header('Conforma Categories')
        print()
        for cat in ('FBC', 'Components', 'Charts'):
            g = groups.get(cat, {'components': 0, 'violations': 0})
            print('  {:<15} {} components, {} unique violations'.format(
                bold(cat + ':'), g['components'], g['violations']))
        print()
        section_header('By Scenario')
        print()
        for sc, count in sorted(scenarios.items(), key=lambda x: -x[1]):
            print('  {:<60} {}'.format(sc[:60], bold(str(count))))
        print()
        return
    _bash_fallback(['conforma', 'categories'] + list(args))


@conforma.command('rules')
@click.option('--stage', is_flag=True, help='Show stage policy violations (from reporter)')
@click.option('--nightly', is_flag=True, help='Show nightly build violations (from reporter)')
def conforma_rules(stage, nightly):
    """Violations grouped by rule with affected components."""
    from cli.mode import ensure_cluster
    use_reporter = stage or nightly
    if use_reporter or ensure_cluster():
        from cli.data import get_conforma_rules
        from cli.formatting import bold, dim, section_header
        reporter_env = 'stage' if stage else None
        reporter_build_type = 'nightly' if nightly else None
        rules = get_conforma_rules(
            reporter_env=reporter_env, reporter_build_type=reporter_build_type)
        source_label = ''
        if stage:
            source_label = ' (stage)'
        if nightly:
            source_label += ' (nightly)' if source_label else ' (nightly)'
        section_header('Conforma Rules — {}{}'.format(cfg.APPLICATION_NAME, source_label))
        if not rules:
            print('\n  No violations found.\n')
            return
        all_comps = set()
        for r in rules:
            all_comps.update(r.get('components', []))
        print('\n{} rules failing across {} components\n'.format(
            bold(str(len(rules))), bold(str(len(all_comps)))))
        app_name = cfg.APPLICATION_NAME or ''
        version_suffix = ''
        if '-v' in app_name:
            version_suffix = '-v' + app_name.split('-v', 1)[1]
        for r in rules:
            rule_name = r['rule']
            count = r['count']
            print('{:<55} {} components'.format(bold(rule_name), count))
            solution = r.get('solution', '')
            if solution:
                sol_text = solution[:120] + '...' if len(solution) > 120 else solution
                print('  Solution: {}'.format(dim(sol_text)))
            comps = r.get('components', [])
            if comps:
                display_comps = [c.replace(version_suffix, '') for c in comps]
                comp_line = ', '.join(display_comps[:8])
                if len(comps) > 8:
                    comp_line += ', ... (+{})'.format(len(comps) - 8)
                print('  Components: {}'.format(comp_line))
            print()
        return
    _bash_fallback(['conforma', 'rules'])


@conforma.command('csv')
@click.option('--stage', is_flag=True, help='Show stage policy violations (from reporter)')
@click.option('--nightly', is_flag=True, help='Show nightly build violations (from reporter)')
@click.argument('args', nargs=-1)
def conforma_csv(stage, nightly, args):
    """Export Conforma data as CSV."""
    from cli.mode import ensure_cluster
    use_reporter = stage or nightly
    if use_reporter or ensure_cluster():
        from cli.data import get_conforma_violations
        reporter_env = 'stage' if stage else None
        reporter_build_type = 'nightly' if nightly else None
        data = get_conforma_violations(
            reporter_env=reporter_env, reporter_build_type=reporter_build_type)
        print('component,violations,warnings,scenario,first_seen')
        if isinstance(data, list):
            for v in data:
                comp = v.get('component', v.get('component_name', ''))
                print('{},{},{},{},{}'.format(
                    comp,
                    v.get('violations_count', 0),
                    v.get('warnings_count', 0),
                    v.get('scenario', v.get('error_type', '')),
                    str(v.get('first_seen', ''))[:10],
                ))
        return
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
        from cli.mode import ensure_cluster
        if ensure_cluster():
            ctx.invoke(release_status)
        else:
            _bash_fallback(['release'])


@release.command('status')
@click.argument('args', nargs=-1)
def release_status(args):
    """Release process checklist."""
    from cli.mode import ensure_cluster
    if ensure_cluster():
        from cli.api_client import get_client
        from cli.formatting import green, red, section_header
        app = cfg.APPLICATION_NAME
        data = get_client().get('/api/v1/applications/{}/readiness'.format(app))
        if not data:
            print('No readiness data available')
            return
        section_header('Release Readiness: {}'.format(app))
        print()
        for k, v in data.items():
            if k == 'application':
                continue
            if isinstance(v, bool):
                color = green if v else red
                print('  {:<30} {}'.format(k + ':', color('✓' if v else '✗')))
            elif isinstance(v, (int, float)):
                print('  {:<30} {}'.format(k + ':', v))
            elif isinstance(v, str):
                print('  {:<30} {}'.format(k + ':', v[:60]))
            elif isinstance(v, list):
                print('  {:<30} {} items'.format(k + ':', len(v)))
        print()
        return
    _bash_fallback(['release', 'status'] + list(args))


@release.command('diff')
@click.argument('args', nargs=-1)
def release_diff(args):
    """Compare snapshots between releases."""
    from cli.mode import ensure_cluster
    if ensure_cluster():
        from cli.formatting import yellow
        print(yellow('release diff requires direct cluster access'))
        print('  Use local mode: ic config use-local')
        return
    _bash_fallback(['release', 'diff'] + list(args))


@release.command('verify')
@click.argument('args', nargs=-1)
def release_verify(args):
    """Check image availability in Pyxis."""
    from cli.mode import ensure_cluster
    if ensure_cluster():
        from cli.formatting import yellow
        print(yellow('release verify requires direct cluster + Pyxis access'))
        print('  Use local mode: ic config use-local')
        return
    _bash_fallback(['release', 'verify'] + list(args))


# --- report group ---

@cli.group()
def report():
    """Reports (build, conforma)."""


@report.command('build')
@click.argument('args', nargs=-1)
def report_build(args):
    """Daily build failures standup table."""
    from cli.mode import ensure_cluster
    if ensure_cluster():
        from cli.data import get_alerts
        from cli.formatting import bold, section_header
        data = get_alerts()
        section_header('Build Report: {}'.format(cfg.APPLICATION_NAME))
        print()
        builds = data.get('build_failures', [])
        if not builds:
            print('  No build failures')
        for f in builds:
            comp = f.get('component', '?')
            err = f.get('error_type', '')
            print('  {} — {}'.format(bold(comp), err))
        print()
        return
    _bash_fallback(['report', 'build'] + list(args))


@report.command('conforma')
@click.argument('args', nargs=-1)
def report_conforma(args):
    """Daily Conforma violations report."""
    from cli.mode import ensure_cluster
    if ensure_cluster():
        from click import get_current_context
        ctx = get_current_context()
        ctx.invoke(conforma_report)
        return
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
        from cli.mode import ensure_cluster
        if ensure_cluster():
            ctx.invoke(jira_status)
        else:
            _bash_fallback(['jira'])


@jira.command('create')
@click.argument('args', nargs=-1)
def jira_create(args):
    """Create Jira ticket for a component failure."""
    from cli.mode import ensure_cluster
    if ensure_cluster() and args:
        component = args[0]
        from cli.data import get_export
        from cli.formatting import green
        data = get_export(component, 'jira')
        if data:
            if isinstance(data, dict) and 'text' in data:
                print(data['text'])
            else:
                import json as json_mod
                print(json_mod.dumps(data, indent=2, default=str))
            print()
            print(green('Copy the above to create a Jira ticket'))
        else:
            print('No data for: {}'.format(component))
        return
    _bash_fallback(['jira', 'create'] + list(args))


@jira.command('inbox')
@click.argument('args', nargs=-1)
def jira_inbox(args):
    """Check Jira inbox for new comments."""
    from cli.mode import ensure_cluster
    if ensure_cluster():
        from cli.data import get_triage_items
        from cli.formatting import bold, section_header
        items = get_triage_items()
        section_header('Jira Inbox')
        print()
        has_jira = False
        all_items = items if isinstance(items, list) else items.get('items', [])
        for item in all_items:
            if item.get('jira_key'):
                has_jira = True
                print('  {} — {}'.format(
                    bold(item['jira_key']),
                    ', '.join(item.get('components', []))))
        if not has_jira:
            print('  No Jira tickets linked to triage items')
        print()
        return
    _bash_fallback(['jira', 'inbox'] + list(args))


@jira.command('link')
@click.argument('args', nargs=-1)
def jira_link(args):
    """Link Jira ticket to component."""
    _bash_fallback(['jira', 'link'] + list(args))


@jira.command('status')
def jira_status():
    """Show Jira ticket links."""
    from cli.mode import ensure_cluster
    if ensure_cluster():
        from cli.data import get_alerts
        from cli.formatting import bold, cyan, dim, section_header
        data = get_alerts()
        all_items = data.get('build_failures', []) + data.get('conforma_violations', [])
        section_header('Jira Status')
        print()
        has_jira = False
        for item in all_items:
            jira = item.get('jira_key')
            if jira:
                has_jira = True
                comp = item.get('component', '?')
                print('  {} → {}'.format(bold(comp), cyan(jira)))
        if not has_jira:
            print(dim('  No Jira tickets linked'))
        print()
        return
    _bash_fallback(['jira', 'status'])


# --- patterns group ---

@cli.group(invoke_without_command=True)
@click.pass_context
def patterns(ctx):
    """Error pattern commands."""
    if ctx.invoked_subcommand is None:
        from cli.mode import ensure_cluster
        if ensure_cluster():
            ctx.invoke(patterns_list)
        else:
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
    from cli.mode import ensure_cluster
    if ensure_cluster():
        from cli.api_client import get_client
        from cli.formatting import bold, section_header
        data = get_client().get('/api/v1/patterns') or []
        shared = [p for p in data if (p.get('occurrence_count') or 0) > 1]
        section_header('Shared Patterns (seen in multiple failures)')
        print()
        if not shared:
            print('  No shared patterns found')
        for p in shared:
            print('  {} — {} ({} occurrences)'.format(
                bold(p.get('pattern_name', '?')),
                p.get('failure_category', ''),
                p.get('occurrence_count', 0)))
        print()
        return
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


@patterns.command('link-skill')
@click.argument('pattern_name')
@click.argument('skill_name')
def patterns_link_skill(pattern_name, skill_name):
    """Link an error pattern to a skill for auto-execution.

    When the worker detects a failure matching this pattern and AUTONOMOUS_MODE
    is enabled, it will auto-execute the linked skill.
    """
    from cli.db import get_repo, require_db
    from cli.formatting import cyan, green, red
    from repositories.error_pattern_repository import ErrorPatternRepository
    if not require_db():
        return
    repo = get_repo(ErrorPatternRepository)
    p = repo.get_by_name_or_category(pattern_name)
    if not p:
        print(red('Pattern not found: ') + cyan(pattern_name))
        raise SystemExit(1)
    from skills.db_registry import get_registry
    registry = get_registry()
    skill = registry.get_skill(skill_name)
    if not skill:
        for s in registry.list_skills():
            if s.name == skill_name:
                skill = s
                break
    if not skill:
        print(red('Skill not found: ') + cyan(skill_name))
        raise SystemExit(1)
    with repo.db.connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE error_patterns SET skill_name = %s WHERE id = %s",
            (skill.qualified_name, p['id']))
    print(green('✓ Linked: ') + '{} → {}'.format(cyan(p['pattern_name']), cyan(skill.qualified_name)))


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
@click.option('--branch', 'branch_flag', default=None, help='Git branch to clone (overrides @branch syntax)')
def skills_add(source_name_or_url, branch_flag):
    """Add skills from a git repo or known source.

    Supports branch syntax: ic skills add <url>@<branch>
    Or explicit flag:       ic skills add <url> --branch <branch>
    """
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
        branch = None
    else:
        name, url, branch = resolve_source(source_name_or_url, branch_override=branch_flag)
        branch_msg = ' (branch: {})'.format(branch) if branch else ''
        print('Cloning {}{}...'.format(url, branch_msg))
        local_path, commit = clone_source(url, name, branch)

    registry.add_source(name, url, commit, local_path, branch)

    found = discover_skills(local_path)
    if not found:
        print('No SKILL.md files found in {}'.format(local_path))
        registry.save()
        return

    from skills.validator import SkillValidator
    from cli.formatting import red, yellow
    validator = SkillValidator()
    added = []
    blocked = []

    for skill_dir, meta in found:
        result = validator.validate(skill_dir, meta)
        if not result.passed:
            blocked.append((meta.name, result))
            continue
        if result.warning_count > 0:
            print(yellow('  {} warnings for {}'.format(result.warning_count, meta.name)))
        registry.add_skill(meta.name, name, skill_dir, meta)
        added.append(meta)

    if not added and blocked:
        registry.remove_source(name)

    registry.save()

    if added:
        print(green('Added {} skill(s) from {}'.format(len(added), bold(name))))
        for meta in added:
            print('  - {}: {}'.format(meta.name, meta.description[:60]))

    if blocked:
        print(red('\nBlocked {} skill(s) with critical findings:'.format(len(blocked))))
        for skill_name, result in blocked:
            criticals = [f for f in result.findings if f.severity == 'critical']
            print(red('  {} — {}'.format(skill_name, criticals[0].message)))
        if not added:
            raise SystemExit(1)


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
        local_path, commit = clone_source(src.url, src.name, src.branch)
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
        if source.branch:
            print('  Branch: {}'.format(source.branch))
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
            branch_info = '  branch {}'.format(src.branch) if src.branch else ''
            print('  {:<30}  {} skill(s)  commit {}{}'.format(
                bold(src.name), skill_count, src.commit[:8], branch_info))
    else:
        print('  None registered yet.')
    print()

    section_header('Known Sources (shortcuts)')
    print()
    for name, info in sorted(KNOWN_SOURCES.items()):
        registered = green('✓') if name in registry.sources else dim('—')
        branch_note = ' [{}]'.format(info['branch']) if info.get('branch') else ''
        print('  {} {:<30}  {}{}'.format(registered, name, info['description'], branch_note))
    print()
    print("  Add with: ic skills add <name>   (e.g., ic skills add aiops-infra)")
    print("  With branch: ic skills add <url>@<branch>  or  --branch <branch>")
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


@skills.command('validate')
@click.argument('name_or_path')
def skills_validate(name_or_path):
    """Run static security analysis on a skill (registered name or local path)."""
    from skills.validator import SkillValidator
    from cli.formatting import bold, green, red, yellow

    validator = SkillValidator()
    expanded = os.path.expanduser(name_or_path)

    if os.path.isdir(expanded):
        from skills.loader import parse_skill_md
        skill_file = os.path.join(expanded, 'SKILL.md')
        metadata = parse_skill_md(skill_file) if os.path.isfile(skill_file) else None
        result = validator.validate(expanded, metadata)
    else:
        from skills.db_registry import get_registry
        registry = get_registry()
        try:
            skill = registry.get_skill(name_or_path)
        except KeyError as e:
            print(str(e).strip('"\''))
            raise SystemExit(1)
        if not skill:
            print('Skill not found: {}'.format(name_or_path))
            raise SystemExit(1)
        result = validator.validate(skill.path, skill.metadata)

    if not result.findings:
        print(green('No issues found in {}'.format(bold(result.skill_name))))
        return

    criticals = [f for f in result.findings if f.severity == 'critical']
    warnings = [f for f in result.findings if f.severity == 'warning']

    if criticals:
        print(red(bold('CRITICAL ({})'.format(len(criticals)))))
        for f in criticals:
            loc = '{}:{}'.format(f.file, f.line) if f.line else f.file
            print('  {} — {} [{}]'.format(red(loc), f.message, f.check))

    if warnings:
        print(yellow(bold('WARNINGS ({})'.format(len(warnings)))))
        for f in warnings:
            loc = '{}:{}'.format(f.file, f.line) if f.line else f.file
            print('  {} — {} [{}]'.format(yellow(loc), f.message, f.check))

    if not result.passed:
        raise SystemExit(1)


@skills.command('doctor')
@click.argument('path', required=False)
def skills_doctor(path):
    """Check prerequisites for registered skills or a local skill directory."""
    from skills.validator import check_prerequisites
    from cli.formatting import bold, green, red, yellow

    skills_to_check = []

    if path:
        expanded = os.path.expanduser(path)
        if not os.path.isdir(expanded):
            print('Not a directory: {}'.format(path))
            raise SystemExit(1)
        from skills.loader import parse_skill_md, discover_skills
        found = discover_skills(expanded)
        if not found:
            skill_file = os.path.join(expanded, 'SKILL.md')
            meta = parse_skill_md(skill_file) if os.path.isfile(skill_file) else None
            if meta:
                skills_to_check.append((meta.name, meta))
            else:
                print('No SKILL.md found in {}'.format(expanded))
                raise SystemExit(1)
        else:
            for _, meta in found:
                skills_to_check.append((meta.name, meta))
    else:
        from skills.db_registry import get_registry
        registry = get_registry()
        for skill in registry.list_skills():
            skills_to_check.append((skill.qualified_name, skill.metadata))

    if not skills_to_check:
        print('No skills to check.')
        return

    has_issues = False
    for name, meta in skills_to_check:
        prereqs = check_prerequisites(meta)
        status = prereqs['status']
        if status == 'ok':
            icon = green('OK')
        elif status == 'warn':
            icon = yellow('WARN')
            has_issues = True
        else:
            icon = red('FAIL')
            has_issues = True

        print('{:<40} {}'.format(bold(name), icon))

        for tool, found in prereqs['tools'].items():
            mark = green('found') if found else red('MISSING')
            print('  tool: {:<20} {}'.format(tool, mark))
        for var, found in prereqs['env'].items():
            mark = green('set') if found else yellow('NOT SET')
            print('  env:  {:<20} {}'.format(var, mark))

    if has_issues:
        raise SystemExit(1)


@skills.command('run')
@click.argument('name')
@click.option('--dry-run', is_flag=True, help='Show steps without executing')
@click.option('--yes', '-y', is_flag=True, help='Skip confirmation for medium-risk skills')
@click.option('--timeout', type=int, default=900, help='Total timeout in seconds (default: 900)')
@click.option('--param', '-p', multiple=True, help='Parameters as key=value')
@click.option('--agent', is_flag=True, help='Use AI agent to execute workflow-type skills')
@click.option('--interactive', is_flag=True, help='Agent pauses for user confirmation before tool calls')
@click.option('--max-turns', type=int, default=30, help='Max agent turns (default: 30)')
@click.option('--max-cost', type=float, default=2.0, help='Max agent cost in USD (default: 2.0)')
@click.option('--json', 'output_json', is_flag=True, hidden=True)
@click.pass_context
def skills_run(ctx, name, dry_run, yes, timeout, param, agent, interactive,
               max_turns, max_cost, output_json):
    """Run a skill."""
    from cli.formatting import cyan, red
    from skills.db_registry import get_registry

    registry = get_registry()
    skill = registry.get_skill(name)
    if not skill:
        for s in registry.list_skills():
            if s.name == name:
                skill = s
                break
    if not skill:
        print(red('Skill not found: ') + cyan(name))
        raise SystemExit(1)

    params = {}
    for p in param:
        if '=' in p:
            k, v = p.split('=', 1)
            params[k] = v

    use_agent = agent
    if not use_agent:
        from skills.type_detector import should_use_agent
        use_agent = should_use_agent(
            skill.path,
            execution_mode=skill.metadata.execution_mode,
        )

    if use_agent:
        result = _run_agent_skill(
            skill, params, dry_run, timeout, max_turns, max_cost,
            interactive, output_json, ctx,
        )
    else:
        result = _run_script_skill(
            skill, params, dry_run, yes, timeout, output_json, ctx,
        )

    try:
        registry.record_run(result)
    except Exception:
        pass

    _print_output_tip(skill)

    if result.status == 'failed':
        raise SystemExit(1)


def _find_skill_outputs(skill):
    """Find .work/ runs for a skill, newest first.

    Returns list of (dir_path, date_str, all_files) where all_files
    includes every file in the run directory.
    """
    from skills.models import resolve_working_dir
    work_dir = resolve_working_dir(skill.path, skill.metadata.working_dir)
    work_path = os.path.join(work_dir, '.work')
    if not os.path.isdir(work_path):
        return []
    runs = []
    for entry in sorted(os.listdir(work_path), reverse=True):
        run_dir = os.path.join(work_path, entry)
        if not os.path.isdir(run_dir) or len(entry) < 8 or not entry[:8].isdigit():
            continue
        files = []
        for f in os.listdir(run_dir):
            fp = os.path.join(run_dir, f)
            if os.path.isfile(fp):
                files.append(f)
        if files:
            runs.append((run_dir, entry, sorted(files)))
    return runs


def _render_file(filepath):
    """Render a file to the terminal. Markdown uses rich, others use plain text."""
    if filepath.endswith('.md'):
        try:
            from rich.console import Console
            from rich.markdown import Markdown
            with open(filepath) as f:
                content = f.read()
            Console().print(Markdown(content))
            return
        except ImportError:
            pass
    elif filepath.endswith('.json'):
        try:
            from rich.console import Console
            from rich.json import JSON
            with open(filepath) as f:
                content = f.read()
            Console().print(JSON(content))
            return
        except ImportError:
            pass
    with open(filepath) as f:
        print(f.read())


def _print_output_tip(skill):
    """Print tip about viewing output if .work/ has files."""
    from cli.formatting import cyan, dim
    runs = _find_skill_outputs(skill)
    if runs:
        md_count = sum(1 for f in runs[0][2] if f.endswith('.md'))
        if md_count:
            print()
            print(dim('Tip: ') + cyan('ic skills output {}'.format(skill.name)))


@skills.command('output')
@click.argument('name')
@click.option('--list', 'list_runs', is_flag=True, help='List all available runs')
@click.option('--run', 'run_index', type=int, default=1, help='Which run to view (1=latest)')
@click.option('--file', 'file_name', type=str, default=None, help='View a specific file by name')
@click.pass_context
def skills_output(ctx, name, list_runs, run_index, file_name):
    """View output files from a skill run."""
    from cli.formatting import bold, cyan, dim, green, red, yellow
    from skills.db_registry import get_registry

    registry = get_registry()
    try:
        skill = registry.get_skill(name)
    except KeyError as e:
        print(red(str(e)))
        raise SystemExit(1)
    if not skill:
        for s in registry.list_skills():
            if s.name == name:
                skill = s
                break
    if not skill:
        print(red('Skill not found: ') + cyan(name))
        raise SystemExit(1)

    runs = _find_skill_outputs(skill)
    if not runs:
        print(yellow('No output found for ') + cyan(name))
        print(dim('Run the skill first: ic skills run {}'.format(name)))
        raise SystemExit(1)

    if list_runs:
        print(bold('Runs for ') + cyan(name))
        print()
        for i, (run_dir, date_str, files) in enumerate(runs, 1):
            md_files = [f for f in files if f.endswith('.md')]
            data_files = [f for f in files if not f.endswith('.md')]
            total_size = sum(
                os.path.getsize(os.path.join(run_dir, f)) for f in files
            )
            date_display = '{}-{}-{} {}:{}'.format(
                date_str[:4], date_str[4:6], date_str[6:8],
                date_str[9:11], date_str[11:13],
            )
            marker = green(' (latest)') if i == 1 else ''
            print('  {} {}  {} md, {} data, {:.1f} KB{}'.format(
                bold('#{}'.format(i)),
                dim(date_display),
                len(md_files), len(data_files),
                total_size / 1024,
                marker,
            ))
        print()
        print(dim('View a run: ic skills output {} --run N'.format(name)))
        return

    if run_index < 1 or run_index > len(runs):
        print(red('Run #{} not found.'.format(run_index))
              + ' Available: 1-{}'.format(len(runs)))
        raise SystemExit(1)

    run_dir, date_str, files = runs[run_index - 1]

    if file_name:
        filepath = os.path.join(run_dir, file_name)
        if not os.path.isfile(filepath):
            print(red('File not found: ') + cyan(file_name))
            print(dim('Available: ') + ', '.join(files))
            raise SystemExit(1)
        _render_file(filepath)
        return

    md_files = sorted(
        [f for f in files if f.endswith('.md')],
        key=lambda f: os.path.getsize(os.path.join(run_dir, f)),
        reverse=True,
    )
    data_files = sorted(f for f in files if not f.endswith('.md'))

    date_display = '{}-{}-{} {}:{}'.format(
        date_str[:4], date_str[4:6], date_str[6:8],
        date_str[9:11], date_str[11:13],
    )
    print(bold('Run ') + dim(date_display) + bold(' — {} files'.format(len(files))))
    print()

    if md_files:
        print(bold('  Markdown:'))
        for i, f in enumerate(md_files, 1):
            size = os.path.getsize(os.path.join(run_dir, f))
            print('    {}. {}  ({:.1f} KB)'.format(i, cyan(f), size / 1024))

    if data_files:
        if len(data_files) <= 5:
            print(bold('  Data: ') + dim(', '.join(data_files)))
        else:
            shown = ', '.join(data_files[:3])
            print(bold('  Data: ') + dim('{} (+{} more)'.format(shown, len(data_files) - 3)))

    print()

    if len(md_files) == 1:
        _render_file(os.path.join(run_dir, md_files[0]))
    elif md_files:
        try:
            choice = input('View [1-{}, all, q]: '.format(len(md_files))).strip().lower()
        except (EOFError, KeyboardInterrupt):
            return
        if choice == 'q':
            return
        if choice == 'all':
            for f in md_files:
                print()
                print(bold('━━━ {} ━━━'.format(f)))
                print()
                _render_file(os.path.join(run_dir, f))
        elif choice.isdigit():
            idx = int(choice)
            if 1 <= idx <= len(md_files):
                _render_file(os.path.join(run_dir, md_files[idx - 1]))
            else:
                print(red('Invalid choice'))
    else:
        print(yellow('No markdown files in this run.'))
        print(dim('View a specific file: ic skills output {} --file <name>'.format(name)))


def _run_agent_skill(skill, params, dry_run, timeout, max_turns, max_cost,
                     interactive, output_json, ctx):
    """Execute a skill via the AI agent loop."""
    import json as json_mod
    from cli.formatting import bold, cyan, dim, green, red, yellow
    from skills.agent_config import AgentConfig
    from skills.agent_executor import AgentExecutor

    config = AgentConfig(
        max_turns=max_turns,
        cost_limit_usd=max_cost,
        total_timeout_seconds=timeout,
    )

    try:
        from clients.llm_provider import create_llm_provider
        from config import CollectorConfig
        app_config = CollectorConfig.from_env()
        if not app_config.llm:
            print(red('LLM provider not configured'))
            print(dim('Set ANTHROPIC_API_KEY or Vertex AI credentials'))
            raise SystemExit(1)
        llm = create_llm_provider(app_config.llm)
    except SystemExit:
        raise
    except Exception as e:
        print(red('LLM provider error: {}'.format(e)))
        print(dim('Set up Vertex AI or Anthropic API credentials'))
        raise SystemExit(1)

    print(bold('Skill:    ') + cyan(skill.qualified_name))
    print(bold('Mode:     ') + cyan('agent'))
    print(bold('Model:    ') + dim(llm.model_name()))
    print(bold('Limits:   ') + dim('{} turns, ${:.2f} max'.format(
        config.max_turns, config.cost_limit_usd)))
    if interactive:
        print(bold('Input:    ') + yellow('interactive'))
    print()

    executor = AgentExecutor(
        skill=skill,
        llm_provider=llm,
        config=config,
        params=params,
        interactive=interactive,
        dry_run=dry_run,
    )

    result = executor.execute()

    if output_json or (ctx.obj and ctx.obj.get('json')):
        print(json_mod.dumps(result.to_dict(), indent=2, default=str))
        return result

    print()
    status_color = green if result.status == 'success' else yellow if result.status == 'dry_run' else red
    print(bold('Status:   ') + status_color(result.status))
    if result.duration_seconds:
        print(bold('Duration: ') + '{:.1f}s'.format(result.duration_seconds))
    if result.steps_executed:
        print(bold('Turns:    ') + '{}'.format(result.steps_executed))

    if result.status == 'dry_run' and result.dry_run_steps:
        print()
        for step in result.dry_run_steps:
            print(dim('  ' + step))

    return result


def _run_script_skill(skill, params, dry_run, yes, timeout, output_json, ctx):
    """Execute a skill via the traditional code-block executor."""
    import json as json_mod
    from cli.formatting import bold, cyan, dim, green, red, yellow
    from skills.executor import SkillExecutor

    executor = SkillExecutor(skill, params=params, dry_run=dry_run, timeout=timeout)
    assessment = executor.assess()
    risk = assessment.level

    if not dry_run and risk in ('medium', 'high') and not yes:
        print(bold('Skill: ') + cyan(skill.qualified_name))
        risk_color = yellow if risk == 'medium' else red
        print(bold('Risk:  ') + risk_color(risk))
        for reason in assessment.reasons:
            print('  - {}'.format(reason))
        if assessment.security_warnings:
            print(bold('Security warnings:'))
            for w in assessment.security_warnings:
                print('  {} {}'.format(yellow('!'), w))
        prereqs = executor.check_prerequisites()
        if prereqs['tools']:
            print(bold('Tools: ') + ', '.join(prereqs['tools'].keys()))
        print()
        confirm = input('Execute? [y/N] ').strip().lower()
        if confirm != 'y':
            print(dim('Cancelled'))
            from skills.models import ExecutionResult
            return ExecutionResult(
                skill_name=skill.qualified_name,
                status='cancelled',
            )

    result = executor.execute()

    if output_json or (ctx.obj and ctx.obj.get('json')):
        print(json_mod.dumps(result.to_dict(), indent=2, default=str))
        return result

    status_color = green if result.status == 'success' else yellow if result.status == 'dry_run' else red
    risk_color = green if risk == 'low' else yellow if risk == 'medium' else red
    print(bold('Skill:    ') + cyan(skill.qualified_name))
    print(bold('Status:   ') + status_color(result.status))
    print(bold('Risk:     ') + risk_color(risk))
    if assessment.reasons:
        for reason in assessment.reasons:
            print('            - {}'.format(reason))
    if assessment.security_warnings:
        print(bold('Warnings: '))
        for w in assessment.security_warnings:
            print('            {} {}'.format(yellow('!'), w))

    if result.status == 'dry_run':
        print(bold('Steps:    ') + '{}'.format(result.steps_total))
        print()
        for i, step in enumerate(result.dry_run_steps, 1):
            print(dim('--- step {} ---'.format(i)))
            print(step[:500])
            print()
    elif result.status in ('success', 'failed'):
        print(bold('Steps:    ') + '{}/{}'.format(result.steps_executed, result.steps_total))
        print(bold('Duration: ') + '{:.1f}s'.format(result.duration_seconds))
        if result.stdout and result.stdout.strip():
            print()
            print(dim('--- stdout ---'))
            print(result.stdout[:2000])
        if result.stderr and result.stderr.strip():
            print()
            print(dim('--- stderr ---'))
            print(result.stderr[:1000])
    elif result.status == 'prereq_failed':
        print()
        print(red(result.stderr))

    return result


@skills.command('runs')
@click.option('--skill', 'skill_name', help='Filter by skill name')
@click.option('--limit', type=int, default=10)
@click.option('--json', 'output_json', is_flag=True, hidden=True)
@click.pass_context
def skills_runs(ctx, skill_name, limit, output_json):
    """Show skill execution history."""
    import json as json_mod
    from cli.db import print_table
    from cli.formatting import green, red, section_header, yellow
    from skills.db_registry import get_registry

    registry = get_registry()
    try:
        history = registry.get_run_history(skill_name=skill_name, limit=limit)
    except Exception:
        print('No execution history (skill_runs table may not exist)')
        return

    if output_json or (ctx.obj and ctx.obj.get('json')):
        print(json_mod.dumps(history, indent=2, default=str))
        return

    section_header('Skill Execution History')
    if not history:
        print('  (no runs)')
        return

    rows = []
    for r in history:
        st = r['status']
        color = green if st == 'success' else red if st == 'failed' else yellow
        rows.append((
            str(r.get('started_at', ''))[:16],
            r['skill_name'],
            color(st),
            '{:.1f}s'.format(r['duration_seconds']) if r.get('duration_seconds') else '-',
            r.get('triggered_by', '-'),
        ))
    print_table(['Started', 'Skill', 'Status', 'Duration', 'By'], rows)


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
