"""Smart auto-verdict correlation engine.

When a failing component gets fixed, compares AI recommendations against
actual resolution to produce evidence-based verdicts (correct/partial/incorrect).
Also infers ML training labels from PR file changes (LabelInference + LabelInferenceService).

Pure comparison logic is separated from I/O (GitHub API, DB) per STYLE.md.
"""

import json
import os
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta

from clients.github_client import GitHubClient, parse_github_repo
from logger import setup_logger
from repositories.ai_analysis_repository import AIAnalysisRepository

logger = setup_logger(__name__)

CORRECT_OVERLAP_THRESHOLD = 0.5
PARTIAL_OVERLAP_THRESHOLD = 0.2
PR_MERGE_WINDOW = timedelta(hours=12)

# --- Label inference constants ---

DEPENDENCY_FILES = frozenset({
    'go.mod', 'go.sum', 'requirements.txt', 'pyproject.toml',
    'setup.py', 'package.json', 'package-lock.json', 'Pipfile', 'Pipfile.lock',
})
BUILD_CONFIG_FILES = frozenset({'Dockerfile', 'Containerfile', 'Makefile'})
TEKTON_CONFIG_DIRS = ('.tekton/',)
TEST_SUFFIXES = ('_test.go', '.test.ts', '.test.js', '.spec.ts', '.spec.py', '_test.py')
TEST_PREFIXES = ('test_',)
BUILD_SCRIPT_DIRS = ('hack/', 'scripts/')
SOURCE_EXTENSIONS = ('.go', '.py', '.ts', '.js', '.java', '.rs', '.c', '.cpp', '.rb')

_CATEGORY_PRIORITY = {
    'tekton_configuration': 0,
    'dependency_issue': 1,
    'build_configuration': 2,
    'test_failure': 3,
    'build_script_error': 4,
    'source_code_change': 5,
}


@dataclass(frozen=True)
class LabelInference:
    failure_category: str
    label_confidence: float
    label_source: str


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


def _classify_file(filepath):
    """Classify a single file path into a failure category.

    Returns one of the category strings or None if unrecognized.
    """
    basename = os.path.basename(filepath)

    if any(filepath.startswith(d) for d in TEKTON_CONFIG_DIRS):
        return 'tekton_configuration'
    if basename in DEPENDENCY_FILES:
        return 'dependency_issue'
    if basename in BUILD_CONFIG_FILES:
        return 'build_configuration'
    if any(basename.endswith(s) for s in TEST_SUFFIXES):
        return 'test_failure'
    if any(basename.startswith(p) for p in TEST_PREFIXES):
        return 'test_failure'
    if any(filepath.startswith(d) for d in BUILD_SCRIPT_DIRS):
        return 'build_script_error'
    if any(basename.endswith(ext) for ext in SOURCE_EXTENSIONS):
        return 'source_code_change'
    return None


def infer_label_from_pr(pr_files):
    """Infer failure_category from a PR's changed file paths.

    Pure function — no I/O. Applies deterministic file-pattern rules to
    produce a failure_category label suitable for ML training.

    Args:
        pr_files: List of file paths changed in the PR (full or relative paths).

    Returns:
        LabelInference with failure_category, label_confidence (0.0–1.0),
        and label_source explaining which signal drove the label.

    Confidence rules:
        1.0 — all files match one category (not source_code_change)
        0.8 — ≥75% of files match one category (not source_code_change)
        0.5 — mixed signals across categories
        0.3 — all files are source code only (category not inferrable from files)
        0.0 — no files or no recognizable file patterns
    """
    if not pr_files:
        return LabelInference('unknown', 0.0, 'no files')

    counts = Counter(_classify_file(f) for f in pr_files)
    counts.pop(None, None)

    if not counts:
        return LabelInference('unknown', 0.0, 'no recognizable file patterns')

    total = len(pr_files)
    ranked = sorted(
        counts.items(),
        key=lambda kv: (-kv[1], _CATEGORY_PRIORITY.get(kv[0], 99)),
    )
    primary_cat, primary_count = ranked[0]

    if primary_count == total:
        if primary_cat == 'source_code_change':
            return LabelInference(primary_cat, 0.3, 'source code changes only')
        return LabelInference(
            primary_cat, 1.0,
            '{} only ({} file{})'.format(primary_cat, total, 's' if total > 1 else ''),
        )

    if primary_count / total >= 0.75:
        confidence = 0.3 if primary_cat == 'source_code_change' else 0.8
        source = '{} dominant ({}/{} files)'.format(primary_cat, primary_count, total)
        return LabelInference(primary_cat, confidence, source)

    top_cats = ', '.join(c for c, _ in ranked[:3])
    return LabelInference(primary_cat, 0.5, 'mixed: {}'.format(top_cats))


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
                except Exception as e:
                    logger.warning("Failed to fetch files for PR #%s: %s",
                                   pr['number'], e)
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
            logger.warning("Failed to record resolution evidence for %s",
                           component_name, exc_info=True)


# --- I/O class: infers ML training labels from fix PRs ---

class LabelInferenceService:
    """Infers ML training labels from GitHub PRs that fixed build failures.

    Orchestrates: DB query (resolved failures without labels) → GitHub API
    (PR files for resolution_commit_sha) → infer_label_from_pr() → DB write.

    Never contains inference logic — that lives in infer_label_from_pr().
    """

    def __init__(self, config, db=None, github_client=None):
        from repositories.connection import DatabaseConnection
        if db is None:
            db = DatabaseConnection(config.db)
        self.db = db
        token = getattr(config, 'github_token', None)
        self.github = github_client or (GitHubClient(token=token) if token else None)

    def backfill(self, application=None, limit=100, min_confidence=0.0, dry_run=False):
        """Label all resolved failures that have a resolution_commit_sha but no label.

        Uses PR file lookup first, then falls back to direct commit comparison
        (base=failing commit, head=fixing commit) when no PR is found. This
        handles direct pushes, cherry-picks, and cases where the PR lookup times out.

        Args:
            application: Filter by application name. None = all applications.
            limit: Maximum failures to process.
            min_confidence: Only persist labels at or above this threshold.
            dry_run: If True, compute but do not write labels.

        Returns:
            dict with counts: processed, labeled, skipped_no_files,
                              skipped_low_confidence, errors.
        """
        rows = self._get_resolved_unlabeled(application, limit)
        counts = {
            'processed': 0,
            'labeled': 0,
            'skipped_no_files': 0,
            'skipped_low_confidence': 0,
            'errors': 0,
        }

        for row in rows:
            counts['processed'] += 1
            try:
                result = self.label_one(
                    build_failure_id=row['id'],
                    resolution_commit_sha=row['resolution_commit_sha'],
                    repository_url=row['repository_url'],
                    failing_commit_sha=row.get('commit_sha'),
                    dry_run=dry_run,
                    min_confidence=min_confidence,
                    _counts=counts,
                )
                if result:
                    counts['labeled'] += 1
            except Exception:
                logger.warning("Error labeling failure id=%s component=%s",
                               row['id'], row.get('component_name', '?'),
                               exc_info=True)
                counts['errors'] += 1

        return counts

    def label_one(self, build_failure_id, resolution_commit_sha, repository_url,
                  failing_commit_sha=None, dry_run=False, min_confidence=0.0,
                  _counts=None):
        """Infer and optionally persist a label for one resolved failure.

        Args:
            build_failure_id: DB id of the build_failure row.
            resolution_commit_sha: The commit that fixed the build.
            repository_url: GitHub URL of the component repo.
            failing_commit_sha: The commit that was failing (used as base for
                comparison when no PR is found).
            dry_run: If True, compute but do not write.
            min_confidence: Skip labels below this threshold.

        Returns LabelInference on success, None when skipped.
        """
        if not self.github:
            logger.debug("No GitHub client — skipping label for failure %s", build_failure_id)
            return None

        parsed = parse_github_repo(repository_url or '')
        if not parsed:
            logger.debug("Cannot parse repo URL for failure %s: %s",
                         build_failure_id, repository_url)
            return None

        owner, repo = parsed
        pr_number, pr_url, pr_files = self._fetch_pr_files(
            owner, repo, resolution_commit_sha, failing_commit_sha=failing_commit_sha
        )

        if not pr_files:
            logger.debug("No changed files found for failure %s (commit=%s)",
                         build_failure_id, resolution_commit_sha[:8] if resolution_commit_sha else '?')
            if _counts is not None:
                _counts['skipped_no_files'] = _counts.get('skipped_no_files', 0) + 1
            return None

        inference = infer_label_from_pr(pr_files)

        if inference.label_confidence < min_confidence:
            logger.debug("Label confidence %.2f below threshold %.2f for failure %s",
                         inference.label_confidence, min_confidence, build_failure_id)
            if _counts is not None:
                _counts['skipped_low_confidence'] = _counts.get('skipped_low_confidence', 0) + 1
            return None

        if not dry_run:
            self._save_label(build_failure_id, resolution_commit_sha,
                             pr_number, pr_url, pr_files, inference)

        logger.info("Labeled failure %s as '%s' (confidence=%.2f, source=%s, dry_run=%s)",
                    build_failure_id, inference.failure_category,
                    inference.label_confidence, inference.label_source, dry_run)
        return inference

    def _get_resolved_unlabeled(self, application, limit):
        """Return resolved failures with a resolution SHA but no training label yet.

        Returns both commit_sha (the failing commit) and resolution_commit_sha
        (the fixing commit) so callers can use commit comparison as a fallback
        when no PR is found for the resolution commit.
        """
        app_filter = 'AND bf.application = %s' if application else ''
        params = [limit] if not application else [application, limit]

        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT bf.id, bf.component_name, bf.application,
                       bf.repository_url, bf.resolution_commit_sha, bf.commit_sha
                FROM build_failures bf
                LEFT JOIN ml_training_labels ml ON ml.build_failure_id = bf.id
                WHERE bf.is_resolved = TRUE
                  AND bf.resolution_commit_sha IS NOT NULL
                  AND bf.repository_url IS NOT NULL
                  AND ml.id IS NULL
                  {}
                ORDER BY bf.resolved_at DESC
                LIMIT %s
            """.format(app_filter), params)
            cols = ['id', 'component_name', 'application', 'repository_url',
                    'resolution_commit_sha', 'commit_sha']
            return [dict(zip(cols, row)) for row in cursor.fetchall()]

    def _fetch_pr_files(self, owner, repo, resolution_commit_sha,
                        failing_commit_sha=None):
        """Return changed files for a resolved build failure.

        Strategy (in order):
        1. PR lookup via get_pr_for_commit() — preferred because it provides
           pr_number and pr_url for provenance.
        2. Direct commit comparison — fallback for direct pushes, cherry-picks,
           or cases where no PR is linked to the commit on GitHub.

        Args:
            owner: Repository owner.
            repo: Repository name.
            resolution_commit_sha: The commit that fixed the build.
            failing_commit_sha: The commit that was failing (base for comparison).

        Returns:
            Tuple of (pr_number, pr_url, [filenames]).
            pr_number and pr_url are None when the commit comparison path is used.
        """
        pr = self.github.get_pr_for_commit(owner, repo, resolution_commit_sha)
        if pr and pr.get('number'):
            pr_number = pr['number']
            pr_url = pr.get('url', '')
            resp = self.github._get(
                '/repos/{}/{}/pulls/{}/files'.format(owner, repo, pr_number)
            )
            if resp:
                files = [f.get('filename', '') for f in resp.json()[:100]
                         if f.get('filename')]
                if files:
                    return pr_number, pr_url, files

        if failing_commit_sha and failing_commit_sha != resolution_commit_sha:
            files = self.github.compare_commits(
                owner, repo, failing_commit_sha, resolution_commit_sha
            )
            if files:
                logger.debug("Used commit comparison (%s...%s) for %s/%s",
                             failing_commit_sha[:8], resolution_commit_sha[:8],
                             owner, repo)
                return None, None, files

        return None, None, []

    def _save_label(self, build_failure_id, resolution_commit_sha,
                    pr_number, pr_url, pr_files, inference):
        """Upsert a training label row into ml_training_labels."""
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO ml_training_labels (
                    build_failure_id, resolution_commit_sha, pr_number, pr_url,
                    failure_category, label_confidence, label_source, pr_files_json,
                    inferred_at, inferred_by
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), 'auto')
                ON CONFLICT (build_failure_id) DO UPDATE SET
                    resolution_commit_sha = EXCLUDED.resolution_commit_sha,
                    pr_number             = EXCLUDED.pr_number,
                    pr_url                = EXCLUDED.pr_url,
                    failure_category      = EXCLUDED.failure_category,
                    label_confidence      = EXCLUDED.label_confidence,
                    label_source          = EXCLUDED.label_source,
                    pr_files_json         = EXCLUDED.pr_files_json,
                    inferred_at           = NOW()
            """, (
                build_failure_id, resolution_commit_sha, pr_number, pr_url,
                inference.failure_category, inference.label_confidence,
                inference.label_source, json.dumps(pr_files),
            ))
            conn.commit()
