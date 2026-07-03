"""Smart auto-verdict correlation engine.

When a failing component gets fixed, compares AI recommendations against
actual resolution to produce evidence-based verdicts (correct/partial/incorrect).

Pure comparison logic is separated from I/O (GitHub API, DB) per STYLE.md.
"""

import os
from datetime import datetime, timedelta

from logger import setup_logger
from clients.github_client import GitHubClient, parse_github_repo
from repositories.ai_analysis_repository import AIAnalysisRepository

logger = setup_logger(__name__)

CORRECT_OVERLAP_THRESHOLD = 0.5
PARTIAL_OVERLAP_THRESHOLD = 0.2
PR_MERGE_WINDOW = timedelta(hours=12)


# --- Pure functions: comparison logic ---

def calculate_file_overlap(recommended, actual):
    """Compute Jaccard-style overlap between recommended and actual file lists.

    Compares basenames when full paths differ (AI might recommend 'Dockerfile'
    while PR changes 'Dockerfile' at a different path).
    """
    if not recommended or not actual:
        return 0.0

    def basenames(files):
        return {os.path.basename(f) for f in files if f}

    rec_set = basenames(recommended)
    act_set = basenames(actual)

    if not rec_set or not act_set:
        return 0.0

    intersection = rec_set & act_set
    union = rec_set | act_set
    return len(intersection) / len(union) if union else 0.0


def classify_fix_type(pr_files):
    """Classify whether a PR is a config change, code change, or mixed."""
    config_patterns = ('.yaml', '.yml', '.json', '.toml', '.cfg', '.ini',
                       'Dockerfile', 'Containerfile', '.conf')
    if not pr_files:
        return 'unknown'

    config_count = sum(
        1 for f in pr_files
        if any(f.endswith(p) for p in config_patterns)
    )
    total = len(pr_files)
    if config_count == total:
        return 'config_change'
    elif config_count == 0:
        return 'code_change'
    return 'mixed'


def compare_recommendations(ai_fix_action_type, ai_recommended_files,
                            actual_changes):
    """Compare AI recommendation with actual fix. Returns (verdict, evidence).

    Args:
        ai_fix_action_type: rebuild, file_change, config_change, multi_step, etc.
        ai_recommended_files: Files AI said to modify
        actual_changes: dict with 'type', 'pr_url', 'files_changed', etc.

    Returns:
        (verdict, evidence_dict)
    """
    actual_type = actual_changes.get('type', 'unknown')
    files_changed = actual_changes.get('files_changed', [])
    pr_url = actual_changes.get('pr_url')

    if actual_type == 'unknown':
        return ('unknown', {'reason': 'Could not determine actual fix'})

    if ai_fix_action_type in ('investigation_needed', 'other', None):
        return ('unknown', {'reason': 'AI deferred classification'})

    if ai_fix_action_type == 'rebuild':
        if actual_type == 'rebuild':
            return ('correct', {'reason': 'AI recommended rebuild, component rebuilt without code changes'})
        if actual_type == 'pr_merged':
            return ('partial', {
                'reason': 'AI recommended rebuild, but a PR was merged (rebuild might have worked too)',
                'pr_url': pr_url,
            })

    if ai_fix_action_type in ('file_change', 'config_change'):
        if actual_type == 'rebuild':
            return ('incorrect', {
                'reason': 'AI recommended file changes, but a simple rebuild fixed it',
            })
        if actual_type == 'pr_merged':
            overlap = calculate_file_overlap(ai_recommended_files, files_changed)
            evidence = {
                'overlap': round(overlap, 2),
                'pr_url': pr_url,
                'ai_files': ai_recommended_files,
                'actual_files': files_changed,
            }

            actual_fix_type = classify_fix_type(files_changed)

            if not ai_recommended_files:
                if ai_fix_action_type == 'config_change' and actual_fix_type in ('config_change', 'mixed'):
                    evidence['reason'] = 'AI correctly identified config change type'
                    return ('correct', evidence)
                if ai_fix_action_type == 'file_change' and actual_fix_type in ('code_change', 'mixed'):
                    evidence['reason'] = 'AI correctly identified file change type'
                    return ('partial', evidence)
                evidence['reason'] = 'AI predicted {} but actual was {}'.format(
                    ai_fix_action_type, actual_fix_type)
                return ('partial', evidence)

            if overlap > CORRECT_OVERLAP_THRESHOLD:
                evidence['reason'] = 'AI recommended correct files (overlap {:.0%})'.format(overlap)
                return ('correct', evidence)
            if overlap > PARTIAL_OVERLAP_THRESHOLD:
                evidence['reason'] = 'AI recommended some correct files (overlap {:.0%})'.format(overlap)
                return ('partial', evidence)

            if ai_fix_action_type == 'config_change' and actual_fix_type in ('config_change', 'mixed'):
                evidence['reason'] = 'AI correctly identified config change type, wrong files'
                return ('partial', evidence)

            evidence['reason'] = 'AI recommended wrong files (overlap {:.0%})'.format(overlap)
            return ('incorrect', evidence)

    if ai_fix_action_type == 'multi_step':
        if actual_type == 'pr_merged':
            overlap = calculate_file_overlap(ai_recommended_files, files_changed)
            return ('partial', {
                'reason': 'Complex multi-step fix, partial credit',
                'overlap': round(overlap, 2),
                'pr_url': pr_url,
            })

    return ('unknown', {'reason': 'No matching verdict rule'})


def detect_actual_fix(merged_prs, resolution_commit_sha, resolution_timestamp):
    """Determine what fixed a component from merged PRs and commit data.

    Pure function — operates on pre-fetched data.

    Args:
        merged_prs: List of merged PR dicts (with 'merged_at', 'files_changed')
        resolution_commit_sha: The commit SHA of the fixing build
        resolution_timestamp: When the build succeeded

    Returns:
        dict with 'type' (rebuild|pr_merged|unknown), 'pr_url', 'files_changed', etc.
    """
    if not merged_prs:
        return {
            'type': 'rebuild',
            'pr_url': None,
            'files_changed': [],
            'commit_sha': resolution_commit_sha,
            'reason': 'No recent merged PRs found — likely a rebuild or upstream fix',
        }

    best_pr = None
    best_distance = None

    for pr in merged_prs:
        merged_at = pr.get('merged_at_dt')
        if not merged_at:
            continue
        if not resolution_timestamp:
            if best_pr is None or merged_at > best_pr.get('merged_at_dt'):
                best_pr = pr
        else:
            distance = abs((merged_at - resolution_timestamp).total_seconds())
            if distance < PR_MERGE_WINDOW.total_seconds():
                if best_distance is None or distance < best_distance:
                    best_distance = distance
                    best_pr = pr

    if best_pr:
        return {
            'type': 'pr_merged',
            'pr_url': best_pr.get('url', ''),
            'pr_number': best_pr.get('number'),
            'pr_title': best_pr.get('title', ''),
            'files_changed': best_pr.get('files_changed', []),
            'commit_sha': best_pr.get('merge_commit_sha', resolution_commit_sha),
            'merged_at': best_pr.get('merged_at'),
        }

    return {
        'type': 'rebuild',
        'pr_url': None,
        'files_changed': [],
        'commit_sha': resolution_commit_sha,
        'reason': 'No PRs merged within 12h window of resolution',
    }


# --- I/O class: orchestrates GitHub + DB ---

class VerdictCorrelator:
    """Correlates AI analysis with actual resolution using GitHub PR data."""

    def __init__(self, config, db, github_client=None):
        self.config = config
        self.db = db
        token = getattr(config, 'github_token', None)
        self.github = github_client or (GitHubClient(token=token) if token else None)

    def correlate_build_resolution(self, component_name, application,
                                   resolution_commit_sha, resolution_timestamp):
        """Correlate a build failure resolution with its AI analysis.

        Returns verdict string: correct, partial, incorrect, or unknown.
        """
        ai_repo = AIAnalysisRepository(self.db)

        analysis = ai_repo.get_analysis_by_component(component_name, application, 'build')
        if not analysis:
            logger.debug("No AI analysis for %s — skipping correlation", component_name)
            return 'unknown'

        ai_fix_action = self._extract_fix_action_type(analysis)
        ai_files = analysis.get('recommended_files') or []
        if isinstance(ai_files, str):
            ai_files = [f.strip() for f in ai_files.split(',') if f.strip()]

        repo_url = self._get_repo_url(component_name, application)
        if not repo_url or not self.github:
            logger.debug("No repo URL or GitHub client for %s — simple verdict", component_name)
            return 'correct'

        parsed = parse_github_repo(repo_url)
        if not parsed:
            return 'correct'

        owner, repo = parsed
        actual_changes = self._fetch_actual_changes(
            owner, repo, component_name, application,
            resolution_commit_sha, resolution_timestamp
        )

        verdict, evidence = compare_recommendations(ai_fix_action, ai_files, actual_changes)

        self._record_resolution_evidence(
            component_name, application, analysis.get('id'),
            actual_changes, verdict, evidence
        )

        logger.info("Verdict '%s' for %s: %s", verdict, component_name,
                     evidence.get('reason', 'no reason'))
        return verdict

    def _extract_fix_action_type(self, analysis):
        """Extract fix_action_type from analysis, checking analysis_json."""
        fix_action = analysis.get('fix_action_type')
        if fix_action:
            return fix_action

        analysis_json = analysis.get('analysis_json')
        if not analysis_json:
            return None

        if isinstance(analysis_json, list):
            for tc in analysis_json:
                if isinstance(tc, dict):
                    inp = tc.get('input', {})
                    if isinstance(inp, dict) and 'fix_action_type' in inp:
                        return inp['fix_action_type']
        return None

    def _get_repo_url(self, component_name, application):
        """Get repository URL for a component from build_failures."""
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT repository_url FROM build_failures
                WHERE component_name = %s AND application = %s
                ORDER BY last_updated_at DESC LIMIT 1
            """, (component_name, application))
            row = cursor.fetchone()
            return row[0] if row else None

    def _get_component_branch(self, component_name, application):
        """Get branch for a component from build_failures."""
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT branch FROM build_failures
                WHERE component_name = %s AND application = %s
                  AND branch IS NOT NULL
                ORDER BY last_updated_at DESC LIMIT 1
            """, (component_name, application))
            row = cursor.fetchone()
            return row[0] if row else None

    def _fetch_actual_changes(self, owner, repo, component_name, application,
                              resolution_commit_sha, resolution_timestamp):
        """Fetch merged PRs and determine what actually fixed the component."""
        branch = self._get_component_branch(component_name, application)

        merged_prs = self.github.list_pull_requests(
            owner, repo, base=branch, state='closed', limit=10
        )

        enriched_prs = []
        for pr in merged_prs:
            if not pr.get('merged'):
                continue
            pr_data = dict(pr)
            merged_at_str = pr.get('merged_at')
            if merged_at_str:
                try:
                    pr_data['merged_at_dt'] = datetime.fromisoformat(
                        merged_at_str.replace('Z', '+00:00')
                    ).replace(tzinfo=None)
                except (ValueError, TypeError):
                    pr_data['merged_at_dt'] = None

            if pr.get('number'):
                try:
                    pr_files = self._get_pr_files(owner, repo, pr['number'])
                    pr_data['files_changed'] = pr_files
                except Exception:
                    pr_data['files_changed'] = []
            enriched_prs.append(pr_data)

        return detect_actual_fix(enriched_prs, resolution_commit_sha, resolution_timestamp)

    def _get_pr_files(self, owner, repo, pr_number):
        """Get list of files changed in a PR."""
        resp = self.github._get('/repos/{}/{}/pulls/{}/files'.format(owner, repo, pr_number))
        if not resp:
            return []
        return [f.get('filename', '') for f in resp.json()[:100]]

    def _record_resolution_evidence(self, component_name, application,
                                    analysis_id, actual_changes, verdict, evidence):
        """Record resolution evidence in resolution_attempts table."""
        try:
            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id FROM build_failures
                    WHERE component_name = %s AND application = %s
                    ORDER BY last_updated_at DESC LIMIT 1
                """, (component_name, application))
                row = cursor.fetchone()
                if not row:
                    return
                failure_id = row[0]

                files_str = ', '.join(actual_changes.get('files_changed', [])[:20])
                notes = 'auto-verdict: {} — {}'.format(verdict, evidence.get('reason', ''))

                cursor.execute("""
                    INSERT INTO resolution_attempts (
                        build_failure_id, ai_analysis_id,
                        attempt_number, attempted_by, resolution_strategy,
                        pr_created, pr_number, pr_url, pr_merged, pr_merged_at,
                        files_modified, status, was_successful,
                        verified_at, verification_notes
                    ) VALUES (
                        %s, %s,
                        1, 'auto-correlator', 'auto_resolution',
                        %s, %s, %s, %s, %s,
                        %s, 'success', TRUE,
                        NOW(), %s
                    )
                    ON CONFLICT DO NOTHING
                """, (
                    failure_id, analysis_id,
                    bool(actual_changes.get('pr_url')),
                    actual_changes.get('pr_number'),
                    actual_changes.get('pr_url'),
                    bool(actual_changes.get('merged_at')),
                    actual_changes.get('merged_at'),
                    files_str[:500] if files_str else None,
                    notes[:500],
                ))
                conn.commit()
        except Exception:
            logger.debug("Failed to record resolution evidence for %s",
                         component_name, exc_info=True)
