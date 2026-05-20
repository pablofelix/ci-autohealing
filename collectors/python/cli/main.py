"""IC CLI entry point — Click-based command tree."""

import os
import subprocess
import sys

import click

from cli import config as cfg


@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx):
    """IC — kubectl-style interface for CI Auto-Healing."""
    if ctx.invoked_subcommand is None:
        _bash_fallback(['help'])


@cli.command('help')
def help_cmd():
    """Show full usage information."""
    _bash_fallback(['help'])


# --- get group ---

@cli.group()
def get():
    """List resources (components, alerts, conforma, ...)."""


@get.command('components')
@click.option('-r', '--refresh', is_flag=True, help='Refresh from cluster')
def get_components(refresh):
    """List currently failing components."""
    args = ['get', 'components']
    if refresh:
        args.append('--refresh')
    _bash_fallback(args)


@get.command('component')
@click.argument('name')
def get_component(name):
    """Get component summary."""
    from cli.db import require_db, sql
    from cli.formatting import bold, cyan, red
    if not require_db():
        return
    total = sql("SELECT COUNT(*) FROM build_failures WHERE component_name = '{}';".format(name))
    if total == '0' or total is None:
        print(red('Error: Component not found: {}'.format(name)))
        raise SystemExit(1)
    with_logs = sql("SELECT COUNT(*) FROM build_failures WHERE component_name = '{}' AND build_logs IS NOT NULL;".format(name))
    first_seen = sql("SELECT MIN(first_detected_at) FROM build_failures WHERE component_name = '{}';".format(name))
    last_seen = sql("SELECT MAX(first_detected_at) FROM build_failures WHERE component_name = '{}';".format(name))
    print(bold('Component: ') + cyan(name))
    print()
    print(bold('Summary:'))
    print('  Total failures: {}'.format(total))
    print('  With logs: {}'.format(with_logs))
    print('  First seen: {}'.format(first_seen))
    print('  Last seen: {}'.format(last_seen))
    print()
    print(cyan('For full details with logs:') + ' ic describe component {}'.format(name))
    print()


@get.command('alerts')
@click.option('--group', is_flag=True, help='Group by root cause')
@click.option('--all', 'show_all', is_flag=True, help='All applications')
@click.option('--date', help='Alerts on specific date')
@click.option('--from', 'from_date', help='Start date for range')
@click.option('--to', 'to_date', help='End date for range')
def get_alerts(group, show_all, date, from_date, to_date):
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
    _bash_fallback(args)


@get.command('conforma')
@click.argument('filter', required=False, default='')
def get_conforma(filter):
    """Conforma test failures."""
    args = ['get', 'conforma']
    if filter:
        args.append(filter)
    _bash_fallback(args)


@get.command('exceptions')
def get_exceptions():
    """Policy exceptions expiring/expired."""
    _bash_fallback(['get', 'exceptions'])


@get.command('bindings')
def get_bindings():
    """RPA -> EC policy mappings."""
    _bash_fallback(['get', 'bindings'])


@get.command('pipelineruns')
@click.option('--limit', type=int, help='Limit results')
def get_pipelineruns(limit):
    """List PipelineRun failures."""
    args = ['get', 'pipelineruns']
    if limit:
        args.extend(['--limit', str(limit)])
    _bash_fallback(args)


@get.command('pipelinerun')
@click.argument('name')
def get_pipelinerun(name):
    """Get PipelineRun details."""
    _bash_fallback(['get', 'pipelinerun', name])


@get.command('apps')
def get_apps():
    """List available RHOAI applications."""
    from cli.db import require_db, sql, sql_rows
    from cli.formatting import bold, cyan, green, section_header, yellow
    if not require_db():
        return
    section_header('Available RHOAI Applications')
    print()
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
        apps = sorted(
            [item['metadata']['name'] for item in result.get('items', [])
             if 'rhoai' in item['metadata']['name']],
        )
    except Exception:
        print(yellow('⚠ Cluster unreachable — showing applications from database'))
        print()
        rows = sql_rows("SELECT DISTINCT application FROM build_failures ORDER BY application;")
        apps = [r[0] for r in rows if r[0]]
    if not apps:
        print(yellow('No applications found'))
        return
    current = cfg.APPLICATION_NAME
    for app in apps:
        db_count = sql("SELECT COUNT(*) FROM build_failures WHERE application = '{}';".format(app)) or '0'
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
@click.argument('name', required=False)
def get_releases(stage, prod, limit, name):
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
    _bash_fallback(args)


@get.command('jira')
@click.argument('component', required=False, default='')
def get_jira(component):
    """Show Jira ticket links."""
    args = ['get', 'jira']
    if component:
        args.append(component)
    _bash_fallback(args)


@get.command('fixes')
@click.option('--all', 'show_all', is_flag=True)
@click.argument('component', required=False, default='')
def get_fixes(show_all, component):
    """List fix attempts."""
    args = ['get', 'fixes']
    if component:
        args.append(component)
    if show_all:
        args.append('--all')
    _bash_fallback(args)


# --- describe group ---

@cli.group()
def describe():
    """Show detailed resource information."""


@describe.command('component')
@click.argument('name')
@click.option('--log', is_flag=True, help='Show complete logs')
def describe_component(name, log):
    """Full component details + logs."""
    args = ['describe', 'component', name]
    if log:
        args.append('--log')
    _bash_fallback(args)


@describe.command('conforma')
@click.argument('name')
def describe_conforma(name):
    """Conforma violation details."""
    _bash_fallback(['describe', 'conforma', name])


@describe.command('pipelinerun')
@click.argument('name')
def describe_pipelinerun(name):
    """Full PipelineRun details + logs."""
    _bash_fallback(['describe', 'pipelinerun', name])


@describe.command('jira')
@click.argument('key')
def describe_jira(key):
    """Show Jira ticket details."""
    _bash_fallback(['describe', 'jira', key])


@describe.command('release')
@click.argument('name')
@click.option('--logs', is_flag=True)
def describe_release(name, logs):
    """Release CR details."""
    args = ['describe', 'release', name]
    if logs:
        args.append('--logs')
    _bash_fallback(args)


# --- config group ---

@cli.group(invoke_without_command=True)
@click.pass_context
def config(ctx):
    """Show or modify configuration."""
    if ctx.invoked_subcommand is None:
        from cli.db import check_db, sql
        from cli.formatting import bold, cyan, section_header
        section_header('Current Configuration')
        print()
        print(bold('Environment:'))
        print('  Namespace: {}'.format(cyan(cfg.NAMESPACE)))
        print('  Application: {}'.format(cyan(cfg.APPLICATION_NAME)))
        print('  Config: {}/.env'.format(cfg.PROJECT_DIR))
        print()
        if check_db():
            print(bold('Database:'))
            print('  Failures: {}'.format(sql('SELECT COUNT(*) FROM build_failures;')))
            print('  Components: {}'.format(sql('SELECT COUNT(DISTINCT component_name) FROM build_failures;')))
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
    from cli.db import check_db, sql
    from cli.formatting import cyan, green, red
    if check_db():
        in_db = sql("SELECT COUNT(*) FROM build_failures WHERE application = '{}';".format(app_name))
        if (in_db == '0' or in_db is None) and not force:
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
        has_cache = sql("SELECT COUNT(*) FROM sync_status WHERE application = '{}';".format(app_name))
        if has_cache == '0' or has_cache is None:
            print('  No cached data yet. Run {} to sync.'.format(cyan('ic get components -r')))
        else:
            print('  Cached sync data available. Run {} to view.'.format(cyan('ic get components')))
    print()


# --- stats group ---

@cli.group(invoke_without_command=True)
@click.pass_context
def stats(ctx):
    """Statistics."""
    if ctx.invoked_subcommand is None:
        from cli.db import require_db, sql_table
        from cli.formatting import section_header
        if not require_db():
            return
        section_header('Statistics')
        print()
        sql_table("""
            SELECT
                COUNT(*) as "Total Failures",
                COUNT(DISTINCT component_name) as "Components",
                COUNT(CASE WHEN build_logs IS NOT NULL THEN 1 END) as "With Logs"
            FROM build_failures;
        """)
        print()


@stats.command('daily')
def stats_daily():
    """Failures by day."""
    from cli.db import require_db, sql_table
    from cli.formatting import section_header
    if not require_db():
        return
    section_header('Failures by Day')
    print()
    sql_table("""
        SELECT
            DATE(first_detected_at) as "Date",
            COUNT(*) as "Failures"
        FROM build_failures
        GROUP BY DATE(first_detected_at)
        ORDER BY DATE(first_detected_at) DESC
        LIMIT 7;
    """)
    print()


@stats.command('resolved')
def stats_resolved():
    """Resolved failures statistics."""
    from cli.db import require_db, sql_table
    from cli.formatting import section_header
    if not require_db():
        return
    section_header('Resolved Failures Statistics')
    print()
    sql_table("""
        SELECT
            COUNT(*) as "Total Records",
            SUM(CASE WHEN is_resolved = TRUE THEN 1 ELSE 0 END) as "Resolved",
            SUM(CASE WHEN is_resolved = FALSE THEN 1 ELSE 0 END) as "Unresolved",
            SUM(CASE WHEN status = 'Succeeded' THEN 1 ELSE 0 END) as "Successes"
        FROM build_failures
        WHERE application = '{app}';
    """.format(app=cfg.APPLICATION_NAME))
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
    _bash_fallback(['ai', 'status'])


@ai.command('stats')
@click.option('--fixes', is_flag=True)
def ai_stats(fixes):
    """Detailed AI statistics."""
    args = ['ai', 'stats']
    if fixes:
        args.append('--fixes')
    _bash_fallback(args)


# --- Top-level commands ---

@cli.command()
def triage():
    """Currently failing components overview."""
    from cli.db import require_db, sql, sql_table
    from cli.formatting import bold, cyan, green, red, section_header
    if not require_db():
        return
    app = cfg.APPLICATION_NAME
    section_header('Triage Dashboard')
    print()
    total = sql("SELECT COUNT(*) FROM build_failures WHERE application = '{}';".format(app))
    failing = sql("""
        SELECT COUNT(*) FROM (
            SELECT DISTINCT ON (component_name) component_name, status, is_resolved
            FROM build_failures WHERE application = '{}'
            ORDER BY component_name, first_detected_at DESC
        ) s WHERE status = 'Failed' AND is_resolved = FALSE;
    """.format(app))
    working_count = sql("""
        SELECT COUNT(*) FROM (
            SELECT DISTINCT ON (component_name) component_name, status
            FROM build_failures WHERE application = '{}'
            ORDER BY component_name, first_detected_at DESC
        ) s WHERE status = 'Succeeded';
    """.format(app))
    print(bold('Overview:'))
    print('  Total build records: {}'.format(total))
    print('  Currently failing: {}'.format(red(str(failing))))
    print('  Currently working: {}'.format(green(str(working_count))))
    print()
    print(bold(red('Currently Failing Components:')))
    sql_table("""
    WITH latest_builds AS (
        SELECT DISTINCT ON (component_name)
            component_name, pipelinerun_name, status,
            first_detected_at, build_logs IS NOT NULL as has_logs
        FROM build_failures WHERE application = '{app}'
        ORDER BY component_name, first_detected_at DESC
    )
    SELECT
        component_name as "Component",
        first_detected_at::date as "Last Failure",
        CASE WHEN has_logs THEN 'Yes' ELSE 'No' END as "Has Logs"
    FROM latest_builds WHERE status = 'Failed'
    ORDER BY first_detected_at DESC;
    """.format(app=app))
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
    from cli.db import require_db, sql_table
    from cli.formatting import cyan, section_header
    if not require_db():
        return
    section_header('Resolved Components')
    print()
    sql_table("""
    SELECT DISTINCT
        component_name as "Component",
        MAX(resolved_at) as "Resolved At",
        COUNT(*) as "Failures Fixed"
    FROM build_failures
    WHERE is_resolved = TRUE AND application = '{app}'
    GROUP BY component_name
    ORDER BY MAX(resolved_at) DESC;
    """.format(app=cfg.APPLICATION_NAME))
    print()
    print(cyan('Tip:') + " Use 'ic history <component>' to see full history")
    print()


@cli.command()
def working():
    """Components currently working."""
    from cli.db import require_db, sql_table
    from cli.formatting import bold, cyan, green, section_header
    if not require_db():
        return
    section_header('Currently Working Components')
    print()
    print(bold(green('Components with last build = Succeeded:')))
    sql_table("""
    WITH latest_builds AS (
        SELECT DISTINCT ON (component_name)
            component_name, pipelinerun_name, status,
            commit_short_sha, commit_message, first_detected_at
        FROM build_failures WHERE application = '{app}'
        ORDER BY component_name, first_detected_at DESC
    )
    SELECT
        component_name as "Component",
        LEFT(commit_short_sha, 8) as "Commit",
        LEFT(commit_message, 50) as "Message",
        first_detected_at::date as "Last Build"
    FROM latest_builds WHERE status = 'Succeeded'
    ORDER BY first_detected_at DESC;
    """.format(app=cfg.APPLICATION_NAME))
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
    from cli.db import require_db, sql, sql_table
    from cli.formatting import bold, cyan, green, red, section_header
    if not require_db():
        return
    app = cfg.APPLICATION_NAME
    section_header('History: {}'.format(component))
    print()
    sql_table("""
    SELECT
        COUNT(*) as "Total Builds",
        SUM(CASE WHEN status = 'Failed' THEN 1 ELSE 0 END) as "Failures",
        SUM(CASE WHEN status = 'Succeeded' THEN 1 ELSE 0 END) as "Successes",
        SUM(CASE WHEN is_resolved = TRUE THEN 1 ELSE 0 END) as "Resolved"
    FROM build_failures
    WHERE component_name = '{comp}' AND application = '{app}';
    """.format(comp=component, app=app))
    print()
    print(bold('Recent Builds (Last 20):'))
    print()
    sql_table("""
    SELECT
        pipelinerun_name as "PipelineRun",
        status as "Status",
        CASE WHEN is_resolved THEN '✓' ELSE '' END as "Resolved",
        LEFT(commit_short_sha, 8) as "Commit",
        first_detected_at as "Date"
    FROM build_failures
    WHERE component_name = '{comp}' AND application = '{app}'
    ORDER BY first_detected_at DESC LIMIT 20;
    """.format(comp=component, app=app))
    print()
    last_status = sql("""
        SELECT status FROM build_failures
        WHERE component_name = '{comp}' AND application = '{app}'
        ORDER BY first_detected_at DESC LIMIT 1;
    """.format(comp=component, app=app))
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
    from cli.db import require_db, sql
    from cli.formatting import bold, cyan, green, red, section_header, yellow
    if not require_db():
        return
    app = cfg.APPLICATION_NAME
    section_header('CI Auto-Healing Dashboard')
    print('  Application: {}'.format(cyan(app)))
    print('  Date: {}'.format(datetime.now().strftime('%Y-%m-%d %H:%M')))
    print()

    print(bold('Analysis Queue'))
    bp = sql("SELECT COUNT(*) FROM build_failures WHERE application = '{}' AND ai_analyzed = FALSE AND is_resolved = FALSE AND ai_skip_reason IS NULL;".format(app)) or '0'
    cp = sql("SELECT COUNT(*) FROM conforma_results WHERE application = '{}' AND ai_analyzed = FALSE AND ai_skip_reason IS NULL;".format(app)) or '0'
    bd = sql("SELECT COUNT(*) FROM build_failures WHERE application = '{}' AND ai_analyzed = TRUE;".format(app)) or '0'
    cd = sql("SELECT COUNT(*) FROM conforma_results WHERE application = '{}' AND ai_analyzed = TRUE;".format(app)) or '0'
    print('  Build:    {} analyzed  {} pending'.format(green(bd), yellow(bp)))
    print('  Conforma: {} analyzed  {} pending'.format(green(cd), yellow(cp)))
    print()

    print(bold('Enrichment Coverage'))
    et = sql("SELECT COUNT(*) FROM build_failures WHERE application = '{}' AND commit_sha IS NOT NULL AND is_resolved = FALSE;".format(app)) or '0'
    ee = sql("SELECT COUNT(*) FROM build_failures WHERE application = '{}' AND commit_sha IS NOT NULL AND enriched_context IS NOT NULL;".format(app)) or '0'
    ef = sql("SELECT COUNT(*) FROM build_failures WHERE application = '{}' AND commit_sha IS NOT NULL AND enrichment_error IS NOT NULL;".format(app)) or '0'
    epct = int(ee) * 100 // max(int(et), 1)
    print('  Enriched: {} ({}%)  Failed: {}'.format(green('{}/{}'.format(ee, et)), epct, red(ef)))
    print()

    print(bold('Pattern Library'))
    pt = sql("SELECT COUNT(*) FROM error_patterns;") or '0'
    pw = sql("SELECT COUNT(*) FROM error_patterns WHERE avg_confidence IS NOT NULL;") or '0'
    pa = sql("SELECT COALESCE(ROUND(AVG(avg_confidence)::numeric * 100), 0) FROM error_patterns;") or '0'
    po = sql("SELECT COALESCE(SUM(occurrence_count), 0) FROM error_patterns;") or '0'
    print('  Patterns: {} total ({} with confidence data)'.format(pt, pw))
    print('  Avg confidence: {}  Total occurrences: {}'.format(cyan('{}%'.format(pa)), po))
    print()

    print(bold('Fix Outcomes (last 30 days)'))
    ft = sql("SELECT COUNT(*) FROM resolution_attempts WHERE attempted_at >= NOW() - INTERVAL '30 days';") or '0'
    fs = sql("SELECT COUNT(*) FROM resolution_attempts WHERE attempted_at >= NOW() - INTERVAL '30 days' AND was_successful = TRUE;") or '0'
    ff = sql("SELECT COUNT(*) FROM resolution_attempts WHERE attempted_at >= NOW() - INTERVAL '30 days' AND was_successful = FALSE;") or '0'
    fp = sql("SELECT COUNT(*) FROM resolution_attempts WHERE attempted_at >= NOW() - INTERVAL '30 days' AND was_successful IS NULL AND status = 'pr_created';") or '0'
    resolved_count = int(fs) + int(ff)
    frate = int(fs) * 100 // max(resolved_count, 1)
    print('  Total: {}  {} Success: {}  {} Failed: {}  Pending: {}'.format(ft, '', green(fs), '', red(ff), fp))
    print('  Success rate: {}'.format(cyan('{}%'.format(frate))))
    print()

    print(bold('API Costs (last 30 days)'))
    ca = sql("SELECT COUNT(*) FROM ai_analysis WHERE analyzed_at >= NOW() - INTERVAL '30 days';") or '0'
    ct = sql("SELECT COALESCE(SUM(tokens_used), 0) FROM ai_analysis WHERE analyzed_at >= NOW() - INTERVAL '30 days';") or '0'
    cc = sql("SELECT ROUND(COALESCE(SUM(cost_usd), 0)::numeric, 2) FROM ai_analysis WHERE analyzed_at >= NOW() - INTERVAL '30 days';") or '0.00'
    print('  Analyses: {}  Tokens: {}  Cost: {}'.format(ca, ct, cyan('${}'.format(cc))))
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
        _bash_fallback(['health'])


@health.command('warnings')
def health_warnings():
    """Health warnings."""
    _bash_fallback(['health', 'warnings'])


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
    _bash_fallback(['patterns', 'learn'])


@patterns.command('stale')
def patterns_stale():
    """Show stale patterns."""
    _bash_fallback(['patterns', 'stale'])


@patterns.command('shared')
def patterns_shared():
    """Shared patterns across components."""
    _bash_fallback(['patterns', 'shared'])


@patterns.command('list')
def patterns_list():
    """List all patterns."""
    _bash_fallback(['patterns', 'list'])


@patterns.command('show')
@click.argument('name')
def patterns_show(name):
    """Show pattern details."""
    _bash_fallback(['patterns', 'show', name])


# --- components group ---

@cli.group()
def components():
    """Component commands."""


@components.command('health')
def components_health():
    """All components with build/conforma status."""
    _bash_fallback(['components', 'health'])


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

    cli(standalone_mode=False)
