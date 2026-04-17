#!/usr/bin/env python3
"""Extract comprehensive PipelineRun details for display."""

import sys
import json
from typing import Optional, Dict, Any
from unified_collector import UnifiedCollector


def get_comprehensive_details(pr_name: str, namespace: str = 'NAMESPACE_PLACEHOLDER') -> Dict[str, Any]:
    """Get all available details for a PipelineRun.

    Returns:
        Dict with:
        - metadata: commit SHA, URLs, etc.
        - failed_taskruns: List of failed TaskRuns with steps
        - error_summary: Extracted error information
        - logs_available: Whether logs are available
        - data_source: Where data came from
    """
    collector = UnifiedCollector(namespace=namespace)

    result = {
        'pipelinerun_name': pr_name,
        'metadata': {},
        'failed_taskruns': [],
        'error_summary': None,
        'logs_available': False,
        'data_source': 'none'
    }

    # Get PipelineRun metadata
    pr_data, source = collector.get_pipelinerun_complete(pr_name)
    result['data_source'] = source

    if not pr_data:
        return result

    # Extract metadata
    annotations = pr_data.get('metadata', {}).get('annotations', {})
    status_results = pr_data.get('status', {}).get('results', [])

    # Get commit info from multiple sources
    commit_sha = annotations.get('build.appstudio.redhat.com/commit_sha', '')
    if not commit_sha:
        # Try from status.results
        for r in status_results:
            if r.get('name') == 'CHAINS-GIT_COMMIT':
                commit_sha = r.get('value', '')
                break

    commit_url = annotations.get('pipelinesascode.tekton.dev/sha-url', '')
    if not commit_url:
        # Try from status.results
        for r in status_results:
            if r.get('name') == 'CHAINS-GIT_URL':
                repo_url = r.get('value', '')
                if repo_url and commit_sha:
                    commit_url = f"{repo_url}/commit/{commit_sha}"
                break

    result['metadata'] = {
        'commit_sha': commit_sha,
        'commit_sha_short': commit_sha[:8] if commit_sha else '',
        'commit_url': commit_url,
        'log_url': annotations.get('pipelinesascode.tekton.dev/log-url', ''),
        'git_provider': annotations.get('pipelinesascode.tekton.dev/git-provider', ''),
        'on_target_branch': annotations.get('pipelinesascode.tekton.dev/on-target-branch', ''),
    }

    # Get failed TaskRuns
    taskrun_details, tr_source = collector.get_taskruns_details(pr_name)

    for task in taskrun_details:
        if task.get('failed_steps'):
            failed_info = {
                'name': task['name'],
                'total_steps': len(task.get('steps', [])),
                'failed_steps': []
            }

            # Get details for each failed step
            for step_name in task['failed_steps']:
                steps = task.get('steps', [])
                step_info = next((s for s in steps if s.get('name') == step_name), None)

                if step_info:
                    failed_info['failed_steps'].append({
                        'name': step_name,
                        'exit_code': step_info.get('exit_code'),
                        'reason': step_info.get('reason', 'Unknown')
                    })

            result['failed_taskruns'].append(failed_info)

    # Create error summary from failed TaskRuns
    if result['failed_taskruns']:
        # Group by reason
        reasons = {}
        for tr in result['failed_taskruns']:
            for step in tr['failed_steps']:
                reason = step['reason']
                if reason not in reasons:
                    reasons[reason] = 0
                reasons[reason] += 1

        # Create summary
        reason_list = [f"{count}x {reason}" for reason, count in reasons.items()]
        result['error_summary'] = '; '.join(reason_list)

    # Check if logs are available
    logs, log_source = collector.get_logs_complete(pr_name, max_size=1000)
    result['logs_available'] = bool(logs and len(logs) > 100)

    return result


def main():
    """CLI entry point."""
    if len(sys.argv) < 2:
        print("Usage: pipelinerun_details.py <pipelinerun-name> [namespace]", file=sys.stderr)
        sys.exit(1)

    pr_name = sys.argv[1]
    namespace = sys.argv[2] if len(sys.argv) > 2 else 'NAMESPACE_PLACEHOLDER'

    details = get_comprehensive_details(pr_name, namespace)

    # Output as JSON
    print(json.dumps(details, indent=2))


if __name__ == '__main__':
    main()
