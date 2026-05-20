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
    from cli.db import require_db, sql, sql_table
    from cli.formatting import bold, cyan, green, section_header
    if not require_db():
        return
    app = cfg.APPLICATION_NAME
    section_header('AI Analysis Status')
    print()

    bp = sql("SELECT COUNT(*) FROM build_failures WHERE ai_analyzed = FALSE AND is_resolved = FALSE AND build_logs IS NOT NULL AND ai_skip_reason IS NULL AND application = '{}';".format(app)) or '0'
    bnl = sql("SELECT COUNT(*) FROM build_failures WHERE ai_analyzed = FALSE AND is_resolved = FALSE AND build_logs IS NULL AND ai_skip_reason IS NULL AND application = '{}';".format(app)) or '0'
    bsk = sql("SELECT COUNT(*) FROM build_failures WHERE ai_skip_reason IS NOT NULL AND application = '{}';".format(app)) or '0'
    ba = sql("SELECT COUNT(*) FROM ai_analysis a JOIN build_failures b ON a.build_failure_id = b.id WHERE b.application = '{}' AND a.analyzed_at > NOW() - INTERVAL '30 days';".format(app)) or '0'
    blc = sql("SELECT COUNT(*) FROM ai_analysis a JOIN build_failures b ON a.build_failure_id = b.id WHERE b.application = '{}' AND a.confidence_score < 0.7 AND a.analyzed_at > NOW() - INTERVAL '30 days';".format(app)) or '0'
    baf = sql("SELECT COUNT(*) FROM ai_analysis a JOIN build_failures b ON a.build_failure_id = b.id WHERE b.application = '{}' AND a.can_auto_fix = TRUE AND a.analyzed_at > NOW() - INTERVAL '30 days';".format(app)) or '0'

    cp = sql("SELECT COUNT(*) FROM conforma_results WHERE ai_analyzed = FALSE AND is_resolved = FALSE AND violation_summary IS NOT NULL AND ai_skip_reason IS NULL AND application = '{}';".format(app)) or '0'
    csk = sql("SELECT COUNT(*) FROM conforma_results WHERE ai_skip_reason IS NOT NULL AND application = '{}';".format(app)) or '0'
    ca = sql("SELECT COUNT(*) FROM ai_analysis a JOIN conforma_results c ON a.conforma_result_id = c.id WHERE c.application = '{}' AND a.analyzed_at > NOW() - INTERVAL '30 days';".format(app)) or '0'
    clc = sql("SELECT COUNT(*) FROM ai_analysis a JOIN conforma_results c ON a.conforma_result_id = c.id WHERE c.application = '{}' AND a.confidence_score < 0.7 AND a.analyzed_at > NOW() - INTERVAL '30 days';".format(app)) or '0'
    caf = sql("SELECT COUNT(*) FROM ai_analysis a JOIN conforma_results c ON a.conforma_result_id = c.id WHERE c.application = '{}' AND a.can_auto_fix = TRUE AND a.analyzed_at > NOW() - INTERVAL '30 days';".format(app)) or '0'

    tc = sql("SELECT COALESCE(SUM(a.cost_usd), 0) FROM ai_analysis a LEFT JOIN build_failures b ON a.build_failure_id = b.id LEFT JOIN conforma_results c ON a.conforma_result_id = c.id WHERE (b.application = '{}' OR c.application = '{}') AND a.analyzed_at > NOW() - INTERVAL '30 days';".format(app, app)) or '0'

    print(bold('Build Failures:'))
    print('  Pending:        {} awaiting analysis'.format(bp))
    if int(bnl) > 0:
        print('  No logs yet:    {} (waiting for commit context collector)'.format(bnl))
    if int(bsk) > 0:
        print('  Skipped:        {} (max retries or no-logs timeout)'.format(bsk))
    print('  Analyzed:       {} (last 30 days)'.format(ba))
    if int(ba) > 0:
        bh = int(ba) - int(blc)
        print('    High conf (>=70%): {}'.format(bh))
        if int(blc) > 0:
            print('    Low conf  (<70%):  {}  <- needs human review'.format(blc))
        bpct = int(baf) * 100 // max(int(ba), 1)
        print('  Auto-fixable:   {} ({}%)'.format(baf, bpct))

    print()
    print(bold('Conforma Violations:'))
    print('  Pending:        {} awaiting analysis'.format(cp))
    if int(csk) > 0:
        print('  Skipped:        {} (max retries)'.format(csk))
    print('  Analyzed:       {} (last 30 days)'.format(ca))
    if int(ca) > 0:
        ch = int(ca) - int(clc)
        print('    High conf (>=70%): {}'.format(ch))
        if int(clc) > 0:
            print('    Low conf  (<70%):  {}  <- needs human review'.format(clc))
        cpct = int(caf) * 100 // max(int(ca), 1)
        print('  Auto-fixable:   {} ({}%)'.format(caf, cpct))

    print()
    print(bold('Total cost:') + '   ${} (last 30 days)'.format(tc))
    print()

    sql_table("""
        SELECT
            ROW_NUMBER() OVER (ORDER BY a.analyzed_at DESC) as "#",
            COALESCE(b.component_name, c.component_name) as "Component",
            CASE WHEN a.build_failure_id IS NOT NULL THEN 'Build' ELSE 'Conforma' END as "Type",
            a.failure_category as "Category",
            ROUND(a.confidence_score * 100) || '%' as "Confidence",
            CASE WHEN a.can_auto_fix THEN '✓' ELSE '✗' END as "Fixable"
        FROM ai_analysis a
        LEFT JOIN build_failures b ON a.build_failure_id = b.id
        LEFT JOIN conforma_results c ON a.conforma_result_id = c.id
        WHERE (b.application = '{app}' OR c.application = '{app}')
          AND a.analyzed_at > NOW() - INTERVAL '7 days'
        ORDER BY a.analyzed_at DESC LIMIT 10;
    """.format(app=app))
    print()

    total_pending = int(bp) + int(cp)
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
    from cli.db import require_db, sql, sql_table
    from cli.formatting import bold, section_header
    if not require_db():
        return
    app = cfg.APPLICATION_NAME
    section_header('AI Analysis Statistics')
    print()
    print(bold('Analyses by Category (last 30 days):'))
    sql_table("""
        SELECT
            CASE WHEN a.build_failure_id IS NOT NULL THEN 'Build' ELSE 'Conforma' END as "Type",
            a.failure_category as "Category",
            COUNT(*) as "Count",
            ROUND(AVG(a.confidence_score * 100)) || '%' as "Avg Confidence",
            SUM(CASE WHEN a.can_auto_fix THEN 1 ELSE 0 END) as "Auto-fixable"
        FROM ai_analysis a
        LEFT JOIN build_failures b ON a.build_failure_id = b.id
        LEFT JOIN conforma_results c ON a.conforma_result_id = c.id
        WHERE (b.application = '{app}' OR c.application = '{app}')
          AND a.analyzed_at > NOW() - INTERVAL '30 days'
        GROUP BY CASE WHEN a.build_failure_id IS NOT NULL THEN 'Build' ELSE 'Conforma' END, a.failure_category
        ORDER BY COUNT(*) DESC;
    """.format(app=app))
    print()
    print(bold('Analyses by Date (last 7 days):'))
    sql_table("""
        SELECT
            DATE(a.analyzed_at) as "Date",
            COUNT(*) as "Analyzed",
            SUM(CASE WHEN a.can_auto_fix THEN 1 ELSE 0 END) as "Auto-fixable",
            COALESCE(SUM(a.cost_usd), 0)::numeric(10,2) as "Cost USD"
        FROM ai_analysis a
        LEFT JOIN build_failures b ON a.build_failure_id = b.id
        LEFT JOIN conforma_results c ON a.conforma_result_id = c.id
        WHERE (b.application = '{app}' OR c.application = '{app}')
          AND a.analyzed_at > NOW() - INTERVAL '7 days'
        GROUP BY DATE(a.analyzed_at)
        ORDER BY DATE(a.analyzed_at) DESC;
    """.format(app=app))
    print()
    tt = sql("SELECT COALESCE(SUM(a.tokens_used), 0) FROM ai_analysis a LEFT JOIN build_failures b ON a.build_failure_id = b.id LEFT JOIN conforma_results c ON a.conforma_result_id = c.id WHERE (b.application = '{}' OR c.application = '{}') AND a.analyzed_at > NOW() - INTERVAL '30 days';".format(app, app)) or '0'
    at = sql("SELECT COALESCE(AVG(a.tokens_used), 0)::int FROM ai_analysis a LEFT JOIN build_failures b ON a.build_failure_id = b.id LEFT JOIN conforma_results c ON a.conforma_result_id = c.id WHERE (b.application = '{}' OR c.application = '{}') AND a.analyzed_at > NOW() - INTERVAL '30 days';".format(app, app)) or '0'
    print(bold('Token Usage (last 30 days):'))
    print('  Total tokens: {}'.format(tt))
    print('  Avg per analysis: {} tokens'.format(at))
    print()


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
        from cli.db import require_db
        from cli.formatting import section_header
        if not require_db():
            return
        section_header('Component Health')
        print()
        try:
            from config import CollectorConfig
            from repositories.connection import DatabaseConnection
            from proactive.health_monitor import HealthMonitor
            db_config = CollectorConfig.from_env()
            db = DatabaseConnection(db_config.db)
            summary = HealthMonitor(db).get_component_health_summary()
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
        from config import CollectorConfig
        from repositories.connection import DatabaseConnection
        from proactive.health_monitor import HealthMonitor
        db_config = CollectorConfig.from_env()
        db = DatabaseConnection(db_config.db)
        warnings_list = HealthMonitor(db).run_checks()
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
    from cli.db import require_db
    from cli.formatting import bold
    if not require_db():
        return
    print(bold('Discovering new patterns from repeated failures...'))
    try:
        from config import CollectorConfig
        from repositories.connection import DatabaseConnection
        from repositories.error_pattern_repository import ErrorPatternRepository
        db_config = CollectorConfig.from_env()
        db = DatabaseConnection(db_config.db)
        created = ErrorPatternRepository(db).discover_new_patterns()
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
    from cli.db import require_db
    from cli.formatting import bold
    if not require_db():
        return
    print(bold('Stale patterns (unused 90+ days or low accuracy):'))
    try:
        from config import CollectorConfig
        from repositories.connection import DatabaseConnection
        from repositories.error_pattern_repository import ErrorPatternRepository
        db_config = CollectorConfig.from_env()
        db = DatabaseConnection(db_config.db)
        stale = ErrorPatternRepository(db).get_stale_patterns()
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
    from cli.db import require_db, sql, sql_table
    from cli.formatting import section_header
    if not require_db():
        return
    section_header('Error Pattern Library')
    print()
    sql_table("""
        SELECT
            failure_type AS "Type",
            failure_category AS "Category",
            occurrence_count AS "Seen",
            CASE WHEN avg_confidence IS NULL THEN '  —  '
                 ELSE ROUND(avg_confidence * 100)::text || '%' END AS "Avg Conf",
            CASE WHEN doc_context IS NOT NULL THEN 'yes' ELSE 'no ' END AS "Doc",
            CASE WHEN last_seen_at IS NULL THEN '—'
                 ELSE TO_CHAR(last_seen_at, 'YYYY-MM-DD') END AS "Last Seen",
            LEFT(pattern_name, 40) AS "Pattern"
        FROM error_patterns
        ORDER BY occurrence_count DESC, failure_type, failure_category;
    """)
    print()
    total = sql("SELECT COUNT(*) FROM error_patterns;") or '0'
    print("  {} patterns total. Run 'ic patterns show <pattern_name>' for details.".format(total))
    print()


@patterns.command('show')
@click.argument('name')
def patterns_show(name):
    """Show pattern details."""
    from cli.db import require_db, sql_dict_rows
    from cli.formatting import bold, section_header
    if not require_db():
        return
    rows = sql_dict_rows("""
        SELECT id, failure_type, failure_category, pattern_name, description,
               typical_fix, doc_url, doc_context, doc_fetched_at,
               occurrence_count, avg_confidence, first_seen_at, last_seen_at, created_by
        FROM error_patterns
        WHERE pattern_name = '{}' OR failure_category = '{}'
        LIMIT 1;
    """.format(name, name))
    if not rows:
        print('Pattern not found: {}'.format(name))
        print("Run 'ic patterns list' to see all patterns.")
        raise SystemExit(1)
    p = rows[0]
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
