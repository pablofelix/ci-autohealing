"""Triage subcommands: track, update, resolve, report, history."""

import json as json_mod

import click

from cli import config as cfg


@click.group(invoke_without_command=True)
@click.option('--json', 'output_json', is_flag=True, help='Output as JSON')
@click.pass_context
def triage(ctx, output_json):
    """Triage dashboard — track build failure investigations."""
    ctx.ensure_object(dict)
    ctx.obj['json'] = ctx.obj.get('json') or output_json
    if ctx.invoked_subcommand is None:
        ctx.invoke(triage_show)


@triage.command('show')
@click.option('--all', 'show_all', is_flag=True, help='Include resolved items')
@click.option('--json', 'output_json', is_flag=True, hidden=True)
@click.pass_context
def triage_show(ctx, show_all, output_json):
    """Show current triage items and untracked failures."""
    import json as json_mod

    from cli import ic_config
    from cli.formatting import bold, cyan, dim, green, red, section_header, yellow

    is_json = output_json or ctx.obj.get('json')

    if ic_config.get_mode() == 'cluster':
        from cli.data import get_triage_items, require_data
        if not require_data():
            return
        data = get_triage_items()
        if is_json:
            print(json_mod.dumps(data, indent=2, default=str))
            return
        section_header('Triage: {}'.format(cfg.APPLICATION_NAME))
        print()
        items = data if isinstance(data, list) else data.get('items', [])
        if not items:
            print(green('  No triage items'))
            print()
            return
        for item in items:
            status_color = green if item.get('status') == 'resolved' else yellow
            print('  {} {} — {}'.format(
                bold('#{}'.format(item.get('id', '?'))),
                status_color(item.get('group_label', item.get('status', '?'))),
                ', '.join(item.get('components', [])),
            ))
            if item.get('jira_key'):
                print('       Jira: {}'.format(cyan(item['jira_key'])))
            if item.get('root_cause'):
                print('       {}'.format(dim(item['root_cause'][:80])))
        print()
        return

    from cli.db import get_repo, require_db
    from repositories.build_failure_repository import BuildFailureRepository
    from repositories.triage_repository import TriageRepository

    if not require_db():
        return

    app = cfg.APPLICATION_NAME
    triage_repo = get_repo(TriageRepository)
    build_repo = get_repo(BuildFailureRepository)

    items = triage_repo.get_all(app) if show_all else triage_repo.get_active(app)
    summary = triage_repo.get_summary(app)
    build_triage = build_repo.get_triage_summary(app)

    tracked_components = set()
    for item in items:
        tracked_components.update(item['components'])

    untracked = [
        c for c in build_triage['failing_components']
        if c['component'] not in tracked_components
    ]

    if is_json:
        print(json_mod.dumps({
            'application': app,
            'summary': summary,
            'items': items,
            'untracked_failures': untracked,
        }, default=str, indent=2))
        return

    section_header('Triage Dashboard — {}'.format(app))
    print()
    print(bold('Overview:'))
    print('  Active: {}  Monitoring: {}  Resolved: {}'.format(
        red(str(summary['active'])) if summary['active'] else '0',
        yellow(str(summary['monitoring'])) if summary['monitoring'] else '0',
        green(str(summary['resolved'])),
    ))
    print('  Build failures (DB): {} failing, {} working'.format(
        build_triage['failing'], build_triage['working']))
    print()

    if items:
        print(bold('Tracked Items:'))
        print()
        for item in items:
            _print_item_row(item)
        print()

    if untracked:
        print(bold(red('Untracked Failures ({})'.format(len(untracked)))))
        print()
        for c in untracked:
            print('  {} {}'.format(red('•'), c['component']))
        print()
        print(dim("  Track with: ic triage track <component> [--group 'label']"))
        print()
    elif not items:
        print(green('  No active triage items or untracked failures.'))
        print()

    if summary['resolved']:
        resolved_with_jira = [
            i for i in triage_repo.get_all(app, days=7)
            if i['status'] == 'resolved' and i.get('jira_key')
        ]
        if resolved_with_jira:
            if _get_jira_client():
                open_jiras = _check_jira_statuses(resolved_with_jira)
                if open_jiras:
                    print(yellow('Open Jiras (builds resolved, Jira still open):'))
                    for jira_key, item in open_jiras:
                        label = item.get('group_label') or ', '.join(item['components'][:2])
                        print('  {} {} — #{} {}'.format(
                            yellow('•'), jira_key, item['id'], label))
                    print()
                    print(dim('  Resolve with: ic triage resolve <id>'))
                    print()
            else:
                print(dim('Linked Jiras (status unknown — set JIRA_EMAIL to check):'))
                for i in resolved_with_jira:
                    label = i.get('group_label') or ', '.join(i['components'][:2])
                    print('  {} — #{} {}'.format(i['jira_key'], i['id'], label))
                print()

    if items or untracked:
        print(cyan('Commands:'))
        print('  ic triage track <component>   Add to tracking')
        print('  ic triage update <id> ...     Update links/notes')
        print('  ic triage resolve <id>        Mark resolved')
        print('  ic triage report              Status report table')
        print()


@triage.command('track')
@click.argument('component')
@click.option('--group', 'group_label', help='Group label (e.g., "Go 1.26 mismatch")')
@click.option('--slack', 'slack_url', help='Slack thread URL (can be added multiple times)')
@click.option('--jira', 'jira_key', help='Jira ticket key (e.g., RHOAIENG-12345)')
@click.option('--step', 'failed_step', help='Failed step (e.g., build-images)')
@click.option('--cause', 'root_cause', help='Root cause description')
@click.option('--notes', help='Additional notes')
@click.option('--add-to', 'add_to_id', type=int,
              help='Add component to existing triage item instead of creating new')
@click.option('--json', 'output_json', is_flag=True, hidden=True)
@click.pass_context
def triage_track(ctx, component, group_label, slack_url, jira_key,
                 failed_step, root_cause, notes, add_to_id, output_json):
    """Track a component failure in triage."""
    from cli.db import get_repo, require_db
    from cli.formatting import cyan, green, yellow
    from repositories.triage_repository import TriageRepository

    is_json = output_json or ctx.obj.get('json')
    if not require_db():
        return

    app = cfg.APPLICATION_NAME
    repo = get_repo(TriageRepository)

    if add_to_id:
        existing = repo.get_by_id(add_to_id)
        if not existing:
            _error('Triage item #{} not found'.format(add_to_id), is_json)
            raise SystemExit(1)
        components = list(existing['components'])
        if component not in components:
            components.append(component)
        updates = {'components': components}
        if jira_key:
            updates['jira_key'] = jira_key
        if notes:
            updates['notes'] = notes
        repo.update_item(add_to_id, **updates)
        if slack_url:
            repo.add_slack_url(add_to_id, slack_url)
        if is_json:
            print(json_mod.dumps({'action': 'added', 'item_id': add_to_id,
                                  'component': component}, default=str))
        else:
            print(green('Added {} to triage item #{}'.format(
                cyan(component), add_to_id)))
        return

    existing = repo.find_by_component(component, app)
    if existing:
        if is_json:
            print(json_mod.dumps({'action': 'exists', 'item': existing}, default=str))
        else:
            print(yellow('Already tracked in item #{} ({})'.format(
                existing['id'], existing['group_label'] or 'no group')))
            print('  Use {} to update it'.format(
                cyan('ic triage update {}'.format(existing['id']))))
        return

    item_id = repo.create_item(
        application=app,
        components=[component],
        group_label=group_label,
        root_cause=root_cause,
        failed_step=failed_step,
        slack_thread_urls=[slack_url] if slack_url else None,
        jira_key=jira_key,
        notes=notes,
    )

    if is_json:
        print(json_mod.dumps({'action': 'created', 'item_id': item_id,
                              'component': component}, default=str))
    else:
        label = group_label or component
        print(green('Created triage item #{}: {}'.format(item_id, cyan(label))))
        if slack_url:
            print('  Slack: {}'.format(slack_url))
        if jira_key:
            print('  Jira: {}'.format(jira_key))


@triage.command('update')
@click.argument('item_id', type=int)
@click.option('--slack', 'slack_url', help='Add Slack thread URL (appends, does not replace)')
@click.option('--jira', 'jira_key', help='Jira ticket key')
@click.option('--notes', help='Update notes')
@click.option('--cause', 'root_cause', help='Root cause')
@click.option('--group', 'group_label', help='Group label')
@click.option('--status', type=click.Choice(['active', 'monitoring', 'resolved']))
@click.option('--json', 'output_json', is_flag=True, hidden=True)
@click.pass_context
def triage_update(ctx, item_id, slack_url, jira_key, notes,
                  root_cause, group_label, status, output_json):
    """Update a triage item's tracking info."""
    from cli.db import get_repo, require_db
    from cli.formatting import cyan, green
    from repositories.triage_repository import TriageRepository

    is_json = output_json or ctx.obj.get('json')
    if not require_db():
        return

    repo = get_repo(TriageRepository)
    existing = repo.get_by_id(item_id)
    if not existing:
        _error('Triage item #{} not found'.format(item_id), is_json)
        raise SystemExit(1)

    if status == 'resolved':
        repo.resolve_item(item_id)
        if is_json:
            print(json_mod.dumps({'action': 'resolved', 'item_id': item_id}, default=str))
        else:
            print(green('Resolved triage item #{}'.format(item_id)))
        return

    updates = {}
    if jira_key:
        updates['jira_key'] = jira_key
    if notes:
        updates['notes'] = notes
    if root_cause:
        updates['root_cause'] = root_cause
    if group_label:
        updates['group_label'] = group_label
    if status:
        updates['status'] = status

    if not updates and not slack_url:
        _error('No updates provided. Use --slack, --jira, --notes, etc.', is_json)
        raise SystemExit(1)

    if updates:
        repo.update_item(item_id, **updates)

    if slack_url:
        repo.add_slack_url(item_id, slack_url)
        updates['slack_thread_urls'] = 'added: {}'.format(slack_url)

    if is_json:
        print(json_mod.dumps({'action': 'updated', 'item_id': item_id,
                              'updates': list(updates.keys())}, default=str))
    else:
        print(green('Updated triage item #{}'.format(item_id)))
        for k, v in updates.items():
            print('  {}: {}'.format(k, cyan(str(v))))


@triage.command('resolve')
@click.argument('target')
@click.option('--resolution', '-r', help='How it was fixed')
@click.option('--pr', 'pr_url', help='Fix PR URL')
@click.option('--verdict', type=click.Choice(['correct', 'partial', 'incorrect', 'skip']),
              help='Was the AI diagnosis correct?')
@click.option('--no-jira', is_flag=True, help='Skip Jira update prompt')
@click.option('--json', 'output_json', is_flag=True, hidden=True)
@click.pass_context
def triage_resolve(ctx, target, resolution, pr_url, verdict, no_jira, output_json):
    """Mark a triage item as resolved. TARGET is item ID, component name, or Jira key."""
    from cli.db import get_repo, require_db
    from cli.formatting import cyan, green
    from repositories.ai_analysis_repository import AIAnalysisRepository
    from repositories.triage_repository import TriageRepository

    is_json = output_json or ctx.obj.get('json')
    if not require_db():
        return

    repo = get_repo(TriageRepository)

    if target.isdigit():
        item_id = int(target)
        item = repo.get_by_id(item_id)
    else:
        item = repo.find_by_component(target, cfg.APPLICATION_NAME)
        if not item:
            item = repo.find_by_jira_key(target, cfg.APPLICATION_NAME)
        item_id = item['id'] if item else None

    if not item:
        _error('Triage item not found: {}'.format(target), is_json)
        raise SystemExit(1)

    repo.resolve_item(item_id, resolution=resolution, pr_url=pr_url)

    if is_json:
        print(json_mod.dumps({'action': 'resolved', 'item_id': item_id}, default=str))
    else:
        label = item.get('group_label') or ', '.join(item['components'][:2])
        print(green('Resolved triage item #{}: {}'.format(item_id, cyan(label))))
        if resolution:
            print('  Resolution: {}'.format(resolution))
        if pr_url:
            print('  PR: {}'.format(pr_url))

    if verdict and verdict != 'skip':
        ai_repo = get_repo(AIAnalysisRepository)
        component = item.get('components', [None])[0]
        if component:
            ai_repo.record_verdict(component, cfg.APPLICATION_NAME, verdict, verdict_by='cli')
            if not is_json:
                print('  AI verdict: {}'.format(verdict))

    if not no_jira and not is_json and item.get('jira_key'):
        _prompt_jira_resolve(item, resolution)


@triage.command('report')
@click.option('--date', help='Report for specific date (YYYY-MM-DD)')
@click.option('--json', 'output_json', is_flag=True, hidden=True)
@click.pass_context
def triage_report(ctx, date, output_json):
    """Generate status report table."""
    from cli.db import get_repo, print_table, require_db
    from cli.formatting import bold, dim, section_header
    from repositories.triage_repository import TriageRepository

    is_json = output_json or ctx.obj.get('json')
    if not require_db():
        return

    app = cfg.APPLICATION_NAME
    repo = get_repo(TriageRepository)
    items = repo.get_report(app, date=date)
    summary = repo.get_summary(app)

    if is_json:
        print(json_mod.dumps({
            'application': app,
            'date': date,
            'summary': summary,
            'items': items,
        }, default=str, indent=2))
        return

    title = 'Triage Report — {}'.format(app)
    if date:
        title += ' ({})'.format(date)
    section_header(title)
    print()

    if not items:
        print('  No triage items found.')
        print()
        return

    resolved = [i for i in items if i['status'] == 'resolved']
    active = [i for i in items if i['status'] != 'resolved']

    if active:
        print(bold('Active / Monitoring'))
        print()
        headers = ['#', 'Root Cause', 'Components', 'Tracking', 'Status']
        rows = []
        for item in active:
            comps = ', '.join(item['components'][:3])
            if len(item['components']) > 3:
                comps += ' +{}'.format(len(item['components']) - 3)
            tracking = _tracking_str(item)
            label = item['group_label'] or (item['root_cause'] or '')[:40]
            status_str = item['status'].upper()
            rows.append((item['id'], label, comps, tracking, status_str))
        print_table(headers, rows)
        print()

    if resolved:
        print(bold('Resolved'))
        print()
        headers = ['#', 'Root Cause', 'Components', 'Resolution', 'Resolved']
        rows = []
        for item in resolved:
            comps = ', '.join(item['components'][:3])
            if len(item['components']) > 3:
                comps += ' +{}'.format(len(item['components']) - 3)
            label = item['group_label'] or (item['root_cause'] or '')[:40]
            res = (item['resolution'] or '')[:40]
            resolved_date = str(item['resolved_at'])[:10] if item['resolved_at'] else ''
            rows.append((item['id'], label, comps, res, resolved_date))
        print_table(headers, rows)
        print()

    print(dim('Summary: {} active, {} monitoring, {} resolved'.format(
        summary['active'], summary['monitoring'], summary['resolved'])))
    print()


@triage.command('history')
@click.option('--days', type=int, default=7, help='Days to look back (default: 7)')
@click.option('--json', 'output_json', is_flag=True, hidden=True)
@click.pass_context
def triage_history(ctx, days, output_json):
    """Show resolved triage items."""
    from cli.db import get_repo, print_table, require_db
    from cli.formatting import dim, section_header
    from repositories.triage_repository import TriageRepository

    is_json = output_json or ctx.obj.get('json')
    if not require_db():
        return

    app = cfg.APPLICATION_NAME
    repo = get_repo(TriageRepository)
    items = repo.get_all(app, days=days)
    resolved = [i for i in items if i['status'] == 'resolved']

    if is_json:
        print(json_mod.dumps({
            'application': app,
            'days': days,
            'items': resolved,
        }, default=str, indent=2))
        return

    section_header('Triage History — last {} days'.format(days))
    print()
    if not resolved:
        print('  No resolved items in the last {} days.'.format(days))
        print()
        return

    headers = ['#', 'Root Cause', 'Components', 'Resolution', 'Resolved']
    rows = []
    for item in resolved:
        comps = ', '.join(item['components'][:3])
        if len(item['components']) > 3:
            comps += ' +{}'.format(len(item['components']) - 3)
        label = item['group_label'] or (item['root_cause'] or '')[:40]
        res = (item['resolution'] or '')[:40]
        resolved_date = str(item['resolved_at'])[:10] if item['resolved_at'] else ''
        rows.append((item['id'], label, comps, res, resolved_date))
    print_table(headers, rows)
    print()
    print(dim('{} resolved item(s)'.format(len(resolved))))
    print()


def _check_jira_statuses(resolved_items):
    """Check which resolved triage items have Jiras still open. Returns [(jira_key, item)]."""
    if not resolved_items:
        return []

    jira = _get_jira_client()
    if not jira:
        return []

    open_jiras = []
    for item in resolved_items:
        try:
            category = jira.get_issue_status(item['jira_key'])
            if category and category != 'done':
                open_jiras.append((item['jira_key'], item))
        except Exception:
            pass
    return open_jiras


def _tracking_str(item):
    parts = []
    if item.get('jira_key'):
        parts.append(item['jira_key'])
    slack_urls = item.get('slack_thread_urls') or []
    if len(slack_urls) == 1:
        parts.append('Slack')
    elif len(slack_urls) > 1:
        parts.append('Slack ({})'.format(len(slack_urls)))
    return ', '.join(parts) if parts else '—'


def _print_item_row(item):
    from cli.formatting import bold, cyan, dim, green, red, yellow

    status = item['status']
    if status == 'active':
        icon = red('•')
        status_str = red('ACTIVE')
    elif status == 'monitoring':
        icon = yellow('•')
        status_str = yellow('MONITORING')
    else:
        icon = green('✓')
        status_str = green('RESOLVED')

    label = item['group_label'] or ', '.join(item['components'][:2])
    print('  {} #{} {} [{}]'.format(icon, item['id'], bold(label), status_str))

    comps = ', '.join(item['components'])
    print('    Components: {}'.format(cyan(comps)))

    if item.get('root_cause'):
        print('    Cause: {}'.format(item['root_cause'][:80]))

    tracking = []
    if item.get('jira_key'):
        tracking.append('Jira: {}'.format(item['jira_key']))
    slack_urls = item.get('slack_thread_urls') or []
    for url in slack_urls:
        tracking.append('Slack: {}'.format(url))
    if tracking:
        print('    {}'.format(dim('  '.join(tracking))))

    if item.get('notes'):
        print('    Notes: {}'.format(dim(item['notes'][:60])))

    print()


def _build_jira_comment(item, resolution):
    """Build a Jira resolution comment with build info."""
    from cli.db import get_repo
    from repositories.build_failure_repository import BuildFailureRepository

    lines = ['Build failure resolved.']
    if resolution:
        lines.append('Resolution: {}'.format(resolution))

    build_repo = get_repo(BuildFailureRepository)
    for comp in item['components']:
        try:
            with build_repo.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT resolution_commit_sha, resolved_at,
                           pipelinerun_name, commit_sha, build_completion_time
                    FROM build_failures
                    WHERE component_name = %s AND application = %s
                      AND is_resolved = TRUE
                    ORDER BY resolved_at DESC NULLS LAST LIMIT 1
                """, (comp, item.get('application', cfg.APPLICATION_NAME)))
                row = cursor.fetchone()
                if not row:
                    continue
                res_sha, resolved_at, orig_pr, orig_sha, fail_time = row

                if not res_sha:
                    res_sha, success_pr = _find_resolution_build(comp, fail_time)
                else:
                    success_pr = None

                lines.append('')
                lines.append('Component: {}'.format(comp))
                if res_sha:
                    lines.append('Resolution commit: {}'.format(res_sha[:12]))
                if success_pr:
                    lines.append('Successful build: {}'.format(success_pr))
                if orig_pr:
                    lines.append('Failed build: {}'.format(orig_pr))
                if resolved_at:
                    lines.append('Resolved at: {}'.format(str(resolved_at)[:19]))
        except Exception:
            pass

    return '\n'.join(lines)


def _find_resolution_build(component_name, fail_time):
    """Query cluster for the first successful push build after a failure."""
    from clients.pipelinerun_query import query_pipelineruns

    try:
        label_selector = (
            'appstudio.openshift.io/component={},'
            'pipelines.appstudio.openshift.io/type=build'
        ).format(component_name)
        prs = query_pipelineruns(
            cfg.NAMESPACE, label_selector, max_pages=1, page_size=20)

        best = None
        for pr in prs:
            labels = pr.get('metadata', {}).get('labels', {})
            event = labels.get('pipelinesascode.tekton.dev/event-type', '')
            if event not in ('push', 'incoming'):
                continue
            conditions = pr.get('status', {}).get('conditions', [])
            if not conditions or conditions[-1].get('status') != 'True':
                continue
            ts = pr.get('metadata', {}).get('creationTimestamp', '')
            if fail_time and ts <= str(fail_time):
                continue
            if not best or ts < best[0]:
                annots = pr.get('metadata', {}).get('annotations', {})
                sha = (annots.get('build.appstudio.redhat.com/commit_sha')
                       or annots.get('pipelinesascode.tekton.dev/sha'))
                name = pr.get('metadata', {}).get('name', '')
                best = (ts, sha, name)

        if best:
            return best[1], best[2]
    except Exception:
        pass
    return None, None


def _get_jira_client():
    """Create a JiraClient from environment config."""
    import os

    from clients.jira_client import JiraClient

    base_url = os.environ.get('JIRA_BASE_URL', 'https://issues.redhat.com')
    email = os.environ.get('JIRA_EMAIL', '')
    token = os.environ.get('JIRA_TOKEN', '')
    project = os.environ.get('JIRA_PROJECT', 'RHOAIENG')

    if not email or not token:
        return None
    return JiraClient(base_url, email, token, project)


def _prompt_jira_resolve(item, resolution):
    """Interactive prompt to comment on and resolve the linked Jira."""
    from cli.formatting import bold, cyan, dim, green, yellow

    jira_key = item['jira_key']
    print()
    print(bold('Linked Jira: {}'.format(cyan(jira_key))))

    comment = _build_jira_comment(item, resolution)
    print()
    print(dim('--- Proposed comment ---'))
    print(comment)
    print(dim('--- end ---'))
    print()

    choice = click.prompt(
        'Update Jira?',
        type=click.Choice(['send', 'edit', 'skip'], case_sensitive=False),
        default='send',
    )

    if choice == 'skip':
        print(dim('Skipped Jira update.'))
        return

    if choice == 'edit':
        print(dim('Enter new comment (end with empty line):'))
        lines = []
        while True:
            line = click.prompt('', default='', show_default=False, prompt_suffix='')
            if line == '':
                break
            lines.append(line)
        if lines:
            comment = '\n'.join(lines)
        else:
            print(yellow('Empty comment — using original.'))

    jira = _get_jira_client()
    if not jira:
        print(yellow('JIRA_EMAIL / JIRA_TOKEN not set — cannot update Jira.'))
        print(dim('Comment to post manually:'))
        print(comment)
        return

    result = jira.add_comment(jira_key, comment)
    if result:
        print(green('Comment added to {}'.format(jira_key)))
    else:
        print(yellow('Failed to add comment to {}'.format(jira_key)))
        return

    do_resolve = click.confirm(
        'Transition {} to Resolved?'.format(jira_key), default=True)
    if not do_resolve:
        return

    transitions = jira.get_transitions(jira_key)
    resolve_t = None
    for t in transitions:
        if t['name'].lower() in ('resolve', 'resolved', 'done', 'close'):
            resolve_t = t
            break

    if not resolve_t:
        available = ', '.join(t['name'] for t in transitions)
        print(yellow('No Resolve transition found. Available: {}'.format(available)))
        return

    if jira.transition_issue(jira_key, resolve_t['id']):
        print(green('{} transitioned to {}'.format(jira_key, resolve_t['name'])))
    else:
        print(yellow('Failed to transition {}'.format(jira_key)))


def _error(msg, is_json):
    if is_json:
        print(json_mod.dumps({'error': msg}))
    else:
        from cli.formatting import red
        print(red('Error: {}'.format(msg)))
