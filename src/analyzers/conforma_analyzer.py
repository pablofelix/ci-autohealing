"""Conforma violation analyzer using LLM provider.

Analyzes Enterprise Contract compliance violations. Similar to BuildFailureAnalyzer
but focused on policy compliance rather than code bugs.
"""

import json
import os
import re
import time

from clients.langfuse_tracker import LangfuseTracker
from clients.llm_provider import create_llm_provider
from logger import setup_logger
from prompt_loader import load_prompt
from repositories import AIAnalysisRepository, DatabaseConnection, ErrorPatternRepository
from repositories.conforma_rule_catalog_repository import ConformaRuleCatalogRepository

logger = setup_logger(__name__)


# Tool schema for structured output from Claude
CONFORMA_ANALYSIS_TOOL = {
    'name': 'record_conforma_analysis',
    'description': 'Record the analysis of a Conforma (Enterprise Contract) compliance violation',
    'input_schema': {
        'type': 'object',
        'properties': {
            'root_cause': {
                'type': 'string',
                'description': 'Clear explanation of why the violation occurred'
            },
            'failure_category': {
                'type': 'string',
                'enum': [
                    'policy_hermetic_build',
                    'policy_unpinned_task',
                    'policy_untrusted_image',
                    'policy_signing_key',
                    'policy_package_source',
                    'policy_rpm_repository',
                    'policy_version_label',
                    'policy_fips_check',
                    'policy_deprecated_task',
                    'policy_deprecated_image',
                    'policy_slsa_provenance',
                    'policy_snyk_error',
                    'policy_labels',
                    'policy_sbom_vendor_label',
                    'policy_cpe_label',
                    'policy_source_image',
                    'config_error',
                    'infrastructure'
                ],
                'description': 'Category of compliance violation'
            },
            'confidence_score': {
                'type': 'number',
                'minimum': 0,
                'maximum': 1,
                'description': 'Confidence in this analysis (0.0-1.0)'
            },
            'recommended_fix': {
                'type': 'string',
                'description': 'Specific fix recommendation or exception request guidance'
            },
            'recommended_files': {
                'type': 'array',
                'items': {'type': 'string'},
                'description': 'Files that need to be modified (if fixable via code)'
            },
            'can_auto_fix': {
                'type': 'boolean',
                'description': 'Whether this can be automatically fixed (usually false for policy violations)'
            },
            'requires_human_review': {
                'type': 'boolean',
                'description': 'Whether human review is needed (usually true - policy exceptions need approval)'
            },
            'evidence_references': {
                'type': 'array',
                'items': {
                    'type': 'object',
                    'properties': {
                        'type': {'type': 'string', 'enum': ['doc', 'config', 'log', 'policy']},
                        'url': {'type': 'string', 'description': 'URL to the resource'},
                        'description': {'type': 'string', 'description': 'What this reference shows'},
                    },
                    'required': ['type', 'description']
                },
                'description': 'Links to docs, config files, policy YAML, or log evidence supporting the diagnosis'
            },
            'source_transparency': {
                'type': 'object',
                'properties': {
                    'sources_consulted': {
                        'type': 'array',
                        'items': {'type': 'string'},
                        'description': 'Data sources actually used (e.g., "build logs", "EC policy YAML", ".tekton/push.yaml")'
                    },
                    'sources_unavailable': {
                        'type': 'array',
                        'items': {'type': 'string'},
                        'description': 'Sources attempted but not available (e.g., "commit diff not provided", "Dockerfile not in context")'
                    },
                    'limitations': {
                        'type': 'array',
                        'items': {'type': 'string'},
                        'description': 'Factors that could change the diagnosis (e.g., "could not verify if hermetic param is set at pipeline level")'
                    },
                },
                'description': 'Academic-style transparency: what was used, what was missing, and analysis limitations'
            },
            'differential_diagnosis': {
                'type': 'array',
                'items': {
                    'type': 'object',
                    'properties': {
                        'hypothesis': {'type': 'string', 'description': 'One-sentence explanation'},
                        'category': {'type': 'string', 'description': 'failure_category enum value'},
                        'confidence': {'type': 'number', 'minimum': 0, 'maximum': 1},
                        'supporting_evidence': {'type': 'array', 'items': {'type': 'string'}},
                        'contradicting_evidence': {'type': 'array', 'items': {'type': 'string'}},
                    },
                    'required': ['hypothesis', 'category', 'confidence']
                },
                'description': '2-3 competing hypotheses ranked by evidence strength. First = primary diagnosis.'
            },
        },
        'required': [
            'root_cause',
            'failure_category',
            'confidence_score',
            'recommended_fix',
            'can_auto_fix'
        ]
    }
}


CONFORMA_SYSTEM_PROMPT = load_prompt('conforma_analyzer')


class ConformaAnalyzer:
    """Analyzes Conforma compliance violations using an LLM provider."""

    def __init__(self, config, db=None, ai_repo=None, llm=None, langfuse=None):
        """Initialize analyzer with dependency injection.

        Args:
            config: CollectorConfig with LLM settings
            db: Database connection (created if None)
            ai_repo: AIAnalysisRepository (created if None)
            llm: LLMProvider (created from config if None)
            langfuse: LangfuseTracker (created if None)
        """
        if db is None:
            db = DatabaseConnection(config.db)

        self.config = config
        self._konflux_client = None
        self.ai_repo = ai_repo or AIAnalysisRepository(db)
        self.pattern_repo = ErrorPatternRepository(db)
        self.catalog_repo = ConformaRuleCatalogRepository(db)

        # Create LLM provider from config
        if llm is None:
            if not config.llm:
                raise ValueError("LLM not configured. Set LLM_PROVIDER in .env")
            llm = create_llm_provider(config.llm)
        self.llm = llm

        # Langfuse tracking (disabled if not configured)
        if langfuse is None:
            langfuse_enabled = bool(os.environ.get('LANGFUSE_PUBLIC_KEY'))
            langfuse = LangfuseTracker(enabled=langfuse_enabled)
        self.langfuse = langfuse

    def get_pending_violations(self, limit=5, component_filter=None, force=False, application=None):
        """Get unanalyzed Conforma violations from DB.

        Args:
            limit: Maximum number of violations to fetch
            component_filter: If specified, only get violations for this component
            force: If True, include already-analyzed violations
            application: Override config app. None uses config default.

        Returns:
            List of violation dicts ready for analysis
        """
        return self.ai_repo.get_pending_conforma_violations(
            application or self.config.k8s.application_name,
            limit=limit,
            component_filter=component_filter,
            force=force
        )

    def build_analysis_prompt(self, violation):
        """Construct system + user prompts from violation data.

        Args:
            violation: Dict with component, violation_summary, scenario, etc.

        Returns:
            Tuple of (system_prompt, user_prompt)
        """
        # Known-pattern context from previous occurrences of the same failure category
        pattern_section = self._format_pattern_section(violation)

        # Truncate violation summary if too long
        summary = violation.get('violation_summary', '') or ''
        if len(summary) > 80000:
            summary = summary[:80000] + '\n\n... (truncated - summary too long)'

        # Build commit info section (some fields may be None)
        commit_info = []
        if violation.get('commit_sha'):
            commit_info.append("- Commit: {commit_sha}".format(**violation))
        if violation.get('commit_author'):
            commit_info.append("- Commit Author: {commit_author}".format(**violation))
        if violation.get('commit_message'):
            commit_info.append("- Commit Message: {commit_message}".format(**violation))

        commit_section = '\n'.join(commit_info) if commit_info else "- Commit: (not available)"

        # Targeted knowledge graph context (fails silently if Neo4j unavailable)
        try:
            from utils.graph_context import conforma_context
            graph_section = conforma_context(violation)
        except Exception:
            graph_section = ""

        release_context = self._get_release_context(violation)

        user_prompt = """Analyze this Conforma (Enterprise Contract) compliance violation. Focus on identifying which policy is violated and the best path forward — fix vs. exception request.
{release_context}
## Component
- Component: {component}
- Repository: {repository}
- Snapshot: {snapshot}
{commit_info}

## Violation
- PipelineRun: {pipelinerun}
- Scenario: {scenario}
- Violations: {violations}
- Warnings: {warnings}
- Successes: {successes}

## Violation Details
```
{summary}
```
{pattern_section}{exclusion_section}{sarif_section}{graph_context}{catalog_section}{tekton_files}
Use the record_conforma_analysis tool. Remember:
- Match against the 14 known patterns (hermetic build, unpinned task, package source, etc.)
- Explain WHICH policy is violated and WHY
- Provide both fix instructions AND exception request guidance
- Be specific: reference exact rules, packages, files from the violation details
- Check Active Policy Exclusions: if the violated rule is already excluded, note this and recommend monitoring the waiver expiry instead of fixing
- Set confidence based on pattern match strength
- If confidence is below 0.70, recommend contacting #konflux-users or @owatkins in Slack
- Mark can_auto_fix=false unless it's a simple rebuild or zero-risk config change
- Include evidence_references with links to docs, config files, and policy YAML
- Include source_transparency: list what data you actually used, what was missing, and what limitations affect your diagnosis

## Reference Documentation
Use these URLs in evidence_references when relevant:
- EC policy customization: https://konflux-ci.dev/docs/compliance/customizing-policy/
- Policy evaluations: https://konflux-ci.dev/docs/compliance/policy-evaluations/
- Hermetic builds: https://konflux-ci.dev/docs/building/hermetic-builds/
- Prefetching dependencies: https://konflux-ci.dev/docs/building/prefetching-dependencies/
- Conforma policy config: https://conforma.dev/docs/cli/configuration.html
{doc_refs}
""".format(
            doc_refs=self._get_doc_refs(violation),
            component=violation.get('component_name', 'unknown'),
            repository=violation.get('repository', 'unknown'),
            snapshot=violation.get('snapshot_name', 'unknown'),
            commit_info=commit_section,
            pipelinerun=violation.get('pipelinerun_name', 'unknown'),
            scenario=violation.get('scenario', 'unknown'),
            violations=violation.get('violations_count', 0),
            warnings=violation.get('warnings_count', 0),
            successes=violation.get('successes_count', 0),
            summary=summary,
            pattern_section=pattern_section,
            exclusion_section=self._get_policy_exclusions(violation),
            sarif_section=self._get_sarif_context(violation),
            graph_context=graph_section,
            release_context=release_context,
            catalog_section=self._get_rule_catalog_context(violation),
            tekton_files=self._get_tekton_files(violation),
        )

        return (CONFORMA_SYSTEM_PROMPT, user_prompt)

    def _get_release_context(self, violation):
        """Build release timeline + blocker + systemic pattern context for AI prompt."""
        lines = []
        application = violation.get('application', '')

        # Release timeline
        try:
            if application:
                from api.routes.failures import _compute_freeze_countdown
                from api.routes.releases import get_schedule
                schedule = get_schedule(application)
                countdown = _compute_freeze_countdown(schedule)
                if countdown:
                    lines.append('\n## Release Context')
                    lines.append('- Phase: {}'.format(countdown.phase))
                    lines.append('- Urgency: {}'.format(countdown.urgency))
                    lines.append('- Timeline: {}'.format(countdown.message))
                    if countdown.urgency in ('critical', 'high'):
                        lines.append('- **NOTE: This violation is blocking a release with imminent deadline. Prioritize fix over exception request if feasible.**')
        except Exception:
            pass

        # Active blocker signals
        try:
            if application:
                from api.routes.failures import list_blockers
                blockers_result = list_blockers(application)
                if blockers_result.critical_signals:
                    lines.append('\n## Active Blocker Signals')
                    for sig in blockers_result.critical_signals[:5]:
                        lines.append('- {}'.format(sig))
                    lines.append('- **If this violation maps to an active blocker, note the connection in your analysis.**')
        except Exception:
            pass

        if lines:
            lines.append('')
            return '\n'.join(lines)
        return ''

    def _get_doc_refs(self, violation):
        """Generate policy URL references for the LLM context."""
        from utils.conforma_utils import (
            extract_policy_from_scenario,
            extract_violation_rules,
            policy_url_with_line,
        )
        refs = []
        scenario = violation.get('scenario', '')
        summary = violation.get('violation_summary', '')
        policy_name = extract_policy_from_scenario(scenario)
        if policy_name and summary:
            rules = extract_violation_rules(summary)
            url = policy_url_with_line(policy_name, rules)
            if url:
                refs.append('- EC policy file: {}'.format(url))
        repo = violation.get('repository', '')
        if repo:
            refs.append('- Component repo: {}'.format(repo))
        return '\n'.join(refs)

    def _get_tekton_files(self, violation):
        """Fetch .tekton pipeline content and prefetch config from GitHub."""
        repo = violation.get('repository', '')
        component = violation.get('component_name', '')
        if not repo or not component:
            return ''
        try:
            from clients.github_client import GitHubClient, parse_github_repo
            parsed = parse_github_repo(repo)
            if not parsed:
                return ''
            owner, repo_name = parsed
            gh = GitHubClient()

            branch = violation.get('branch', '')
            refs_to_try = []
            if branch:
                refs_to_try.append(branch)
            commit = violation.get('commit_sha', '')
            if commit:
                refs_to_try.append(commit)
            app = violation.get('application', '')
            if app:
                m = re.match(r'rhoai-v(\d+)-(\d+)(?:-(ea|rc)-(\d+))?$', app)
                if m:
                    release_branch = 'rhoai-{}.{}'.format(m.group(1), m.group(2))
                    if m.group(3):
                        release_branch += '-{}.{}'.format(m.group(3), m.group(4))
                    refs_to_try.append(release_branch)
            refs_to_try.append('konflux-{}'.format(component))
            refs_to_try.append(None)

            listing = None
            used_ref = None
            for ref in refs_to_try:
                listing = gh.get_directory_listing(owner, repo_name, '.tekton', ref=ref)
                if listing:
                    used_ref = ref
                    break
            if not listing:
                return ''

            files = self._match_tekton_files(component, listing)
            if not files:
                return ''
            lines = ['\n## Component Tekton Pipeline Files']
            for f in sorted(files):
                lines.append('- .tekton/{}'.format(f))

            push_file = next((f for f in files if 'push' in f), None)
            if push_file:
                lines.extend(self._extract_prefetch_context(
                    gh, owner, repo_name, push_file, used_ref))

            return '\n'.join(lines)
        except Exception:
            return ''

    def _extract_prefetch_context(self, gh, owner, repo_name, push_file, branch):
        """Read push pipeline YAML and extract prefetch-input + related files."""
        lines = []
        try:
            content = gh.get_file_content(
                owner, repo_name, '.tekton/{}'.format(push_file), ref=branch)
            if not content:
                return lines

            prefetch_match = re.search(
                r'name:\s*prefetch-input\s*\n\s*value:\s*(.+?)(?:\n\s*- name:|\nstatus:)',
                content, re.DOTALL)
            if not prefetch_match:
                return lines

            raw = prefetch_match.group(1).strip()
            lines.append('\n### Prefetch Configuration (from {})'.format(push_file))

            if raw.startswith("'") or raw.startswith('"'):
                raw = raw[1:-1] if raw[-1] in ('"', "'") else raw[1:]
            try:
                prefetch = json.loads(raw)
            except json.JSONDecodeError:
                raw_clean = re.sub(r'#.*$', '', raw, flags=re.MULTILINE).strip()
                raw_clean = re.sub(r',\s*]', ']', raw_clean)
                try:
                    prefetch = json.loads(raw_clean)
                except json.JSONDecodeError:
                    lines.append('```\n{}\n```'.format(raw[:2000]))
                    return lines

            if isinstance(prefetch, dict):
                prefetch = [prefetch]

            for entry in prefetch:
                pkg_type = entry.get('type', '?')
                path = entry.get('path', '.')
                lines.append('- Type: {}, Path: {}'.format(pkg_type, path))
                if entry.get('binary'):
                    lines.append('- **BINARY WHEELS ENABLED**: `"binary": {}`'.format(
                        json.dumps(entry['binary'])))
                    lines.append('  > This causes `hermeto:pip:package:binary=true` on every '
                                 'prefetched package, triggering '
                                 'sbom_spdx.disallowed_package_attributes violations.')
                if entry.get('allow_binary'):
                    lines.append('- allow_binary: {}'.format(entry['allow_binary']))
                req_files = entry.get('requirements_files', [])
                build_files = entry.get('requirements_build_files', [])
                if req_files:
                    lines.append('- Requirements: {}'.format(', '.join(req_files)))
                if build_files:
                    lines.append('- Build requirements: {}'.format(', '.join(build_files)))
                elif pkg_type == 'pip' and entry.get('binary'):
                    lines.append('- **No requirements_build_files configured** — '
                                 'needed to compile from source instead of using binary wheels')

                if pkg_type == 'pip' and path != '.':
                    self._check_repo_build_files(
                        gh, owner, repo_name, path, req_files, branch, lines)

        except Exception as exc:
            logger.debug("Prefetch context extraction failed: %s", exc)
        return lines

    @staticmethod
    def _check_repo_build_files(gh, owner, repo_name, path, req_files, branch, lines):
        """Check if build-requirements files exist in the repo but aren't configured."""
        try:
            for req in req_files:
                req_dir = req.rsplit('/', 1)[0] if '/' in req else ''
                search_dir = '{}/{}'.format(path, req_dir).rstrip('/')
                dir_listing = gh.get_directory_listing(
                    owner, repo_name, search_dir, ref=branch)
                if not dir_listing:
                    continue
                build_files = [f for f in dir_listing
                               if 'build' in f.lower() and 'req' in f.lower()]
                if build_files:
                    lines.append('\n### Unused Build Requirements Files Found')
                    lines.append('> These files exist in the repo but are NOT referenced '
                                 'in prefetch-input. Adding them as '
                                 '`requirements_build_files` would allow Hermeto to '
                                 'compile from source instead of downloading binary wheels.')
                    for bf in build_files:
                        lines.append('- {}/{}'.format(search_dir, bf))
        except Exception:
            pass

    @staticmethod
    def _match_tekton_files(component, listing):
        """Match component name to Tekton filenames with flexible matching."""
        base = component.rsplit('-v3-', 1)[0] if '-v3-' in component else component
        exact = [f for f in listing if base in f]
        if exact:
            return exact
        noise = {'odh', 'workbench', 'cpu', 'gpu', 'ubi9', 'ubi8', 'v3', 'v4',
                 'ea', 'ea1', 'ea2', 'rc1', 'rc2', 'py311', 'py312'}
        keywords = [p for p in base.split('-') if p and p not in noise and len(p) > 2]
        if not keywords:
            return []
        best, best_score = [], 0
        for f in listing:
            score = sum(1 for kw in keywords if kw in f)
            if score > best_score:
                best, best_score = [f], score
            elif score == best_score and score > 0:
                best.append(f)
        return best if best_score >= max(1, len(keywords) // 2) else []

    def _format_pattern_section(self, violation):
        """Return a prompt section with institutional memory from prior occurrences."""
        fix = violation.get('pattern_typical_fix')
        doc = violation.get('pattern_doc_context')
        name = violation.get('pattern_name', 'prior occurrence')
        if not fix and not doc:
            return ""
        parts = ["\n## Known Pattern: {}\n".format(name)]
        if fix:
            parts.append("### Previous Solution\n{}\n".format(fix))
        if doc:
            parts.append("### Relevant Documentation\n{}\n".format(doc[:2000]))
        return '\n'.join(parts)

    def _get_rule_catalog_context(self, violation):
        """Look up violated rules in the conforma_rule_catalog for rich context."""
        try:
            from utils.conforma_utils import extract_violation_rules
            summary = violation.get('violation_summary', '') or ''
            rules = extract_violation_rules(summary)
            if not rules:
                return ''
            rule_ids = [r.replace('.', '__') for r in rules]
            entries = self.catalog_repo.get_by_rule_ids(rule_ids)
            if not entries:
                return ''
            has_reporter_fix = any(e.get('reporter_solution') for e in entries)
            header = '\n## Conforma Rule Catalog (matched {} of {} violated rules)'.format(
                len(entries), len(rules))
            if has_reporter_fix:
                header += '\n> **High-confidence evidence**: RHOAI-specific fixes below come from the verified conforma-reporter resolution guide. Boost confidence by +0.15 when these fixes apply.'
            lines = [header]
            for e in entries:
                lines.append('### {}'.format(e['rule_name']))
                lines.append('- Rule: `{}`  (package: {}, policy: {})'.format(
                    e['rule_id'].replace('__', '.'), e['rule_package'], e.get('policy_type', '?')))
                if e.get('description'):
                    lines.append('- What it checks: {}'.format(e['description'][:300]))
                if e.get('reporter_solution'):
                    lines.append('- RHOAI-specific fix (VERIFIED): {}'.format(e['reporter_solution'][:500]))
                elif e.get('typical_fix'):
                    lines.append('- Generic fix: {}'.format(e['typical_fix'][:300]))
                if e.get('doc_url'):
                    lines.append('- Docs: {}'.format(e['doc_url']))
            return '\n'.join(lines) + '\n'
        except Exception as exc:
            logger.debug("Rule catalog lookup failed: %s", exc)
            return ''

    def _get_policy_exclusions(self, violation):
        """Fetch EC policy exclusions relevant to this violation's scenario."""
        scenario = violation.get('scenario', '')
        if not scenario:
            return ''
        try:
            if self._konflux_client is None:
                from clients.konflux_client import KonfluxClient
                self._konflux_client = KonfluxClient(namespace=self.config.k8s.namespace)
            kc = self._konflux_client
            scenarios = kc.get_integration_test_scenarios(
                self.config.k8s.namespace,
                app_filter=self.config.k8s.application_name,
            )
            policy_name = None
            for s in scenarios:
                meta = kc.extract_its_metadata(s)
                if meta['name'] == scenario and meta['policy_ref']:
                    policy_name = meta['policy_ref']
                    break
            if not policy_name:
                return ''

            policy = kc.get_ec_policy(policy_name)
            if not policy:
                return ''

            exclusions = kc.extract_exceptions(policy)
            if not exclusions:
                return ''

            lines = ['\n## Active Policy Exclusions ({})'.format(policy_name)]
            for exc in exclusions:
                if exc['permanent']:
                    lines.append('- PERMANENT: {} (config.exclude)'.format(exc['value']))
                else:
                    days = exc.get('days_left')
                    days_str = '{} days left'.format(days) if days is not None else 'no expiry'
                    lines.append('- VOLATILE: {} (until {}, {}, ref: {})'.format(
                        exc['value'],
                        exc.get('effectiveUntil', 'N/A'),
                        days_str,
                        exc.get('reference', 'none'),
                    ))
            return '\n'.join(lines) + '\n'
        except Exception as e:
            logger.warning("Cannot fetch EC policy exclusions: %s", e)
            return ''

    def _get_sarif_context(self, violation):
        """Fetch SARIF scan results if violation is scan-related."""
        summary = (violation.get('violation_summary') or '').lower()
        is_scan = any(kw in summary for kw in ('cve', 'vulnerability', 'clair', 'scan'))
        if not is_scan:
            return ''

        component = violation.get('component_name')
        if not component:
            return ''

        try:
            from clients.kubernetes import KubernetesClient
            kc = KubernetesClient(namespace=self.config.k8s.namespace)
            meta = kc.get_component_metadata(component)
            if not meta or not meta.get('container_image'):
                return ''

            from clients.registry_client import RegistryClient
            image = meta['container_image']
            registry, repository, tag_or_digest = RegistryClient.parse_image_ref(image)
            if not tag_or_digest.startswith('sha256:'):
                return ''

            rc = RegistryClient()
            results = rc.fetch_sarif_results(registry, repository, tag_or_digest)
            sarif_text = RegistryClient.format_sarif_summary(results)
            if sarif_text:
                return '\n' + sarif_text + '\n'
            return ''
        except Exception as e:
            logger.debug("SARIF fetch for conforma failed: %s", e)
            return ''

    def parse_analysis_response(self, llm_response):
        """Extract structured analysis from LLMResponse.tool_calls.

        Pure function with Pydantic validation.

        Args:
            llm_response: LLMResponse with tool_calls

        Returns:
            Dict with root_cause, failure_category, confidence_score, etc.

        Raises:
            ValueError: If tool_calls is empty, malformed, or validation fails
        """
        from pydantic import ValidationError

        from analyzers.models import ConformaAnalysisResult

        if not llm_response.tool_calls:
            raise ValueError("LLM did not return tool_use response")

        # Extract the record_conforma_analysis tool call
        analysis_call = None
        for call in llm_response.tool_calls:
            if call.get('name') == 'record_conforma_analysis':
                analysis_call = call
                break

        if not analysis_call:
            raise ValueError("LLM did not call record_conforma_analysis tool")

        input_data = analysis_call.get('input', {})

        # Validate with Pydantic (catches LLM hallucinations)
        try:
            result = ConformaAnalysisResult(**input_data)
            return result.model_dump()  # Convert back to dict for DB
        except ValidationError as e:
            logger.error("LLM returned invalid Conforma analysis: %s", e)
            logger.error("Raw input_data: %s", input_data)
            # Fall back to permissive dict (for backwards compatibility)
            # but log the validation failure for monitoring
            return {
                'root_cause': input_data.get('root_cause', 'Invalid LLM response'),
                'failure_category': 'infrastructure',  # Safe fallback
                'confidence_score': 0.0,
                'recommended_fix': input_data.get('recommended_fix', 'Manual review required'),
                'recommended_files': input_data.get('recommended_files', []),
                'can_auto_fix': False,
                'requires_human_review': True,
            }

    def _annotate_fix_status(self, analysis, violation):
        """Append fix-status notes when violation context shows a fix may already exist."""
        notes = []
        category = analysis.get('failure_category', '')
        if category in ('policy_version_mismatch', 'policy_outdated_label'):
            notes.append('- This violation type is typically resolved by a rebuild — check if one is already in progress')

        if violation.get('has_exception'):
            notes.append('- A policy exception already covers this component — this may be a false positive')

        if notes:
            fix_status = '\n\nFix Status (auto-detected):\n' + '\n\n'.join(notes)
            analysis['recommended_fix'] = analysis.get('recommended_fix', '') + fix_status

        return analysis

    def analyze_violation(self, violation):
        """Analyze one violation: build prompt -> call LLM -> parse response -> save to DB.

        Args:
            violation: Violation dict from get_pending_violations()

        Returns:
            Dict with analysis results

        Raises:
            Exception: If LLM call or parsing fails
        """
        system_prompt, user_prompt = self.build_analysis_prompt(violation)

        # Create Langfuse trace
        trace = self.langfuse.create_trace(
            name='conforma-violation-analysis',
            input_data={
                'component': violation['component_name'],
                'pipelinerun': violation['pipelinerun_name'],
                'violations': violation['violations_count'],
            },
            metadata={
                'violation_id': violation['id'],
                'scenario': violation.get('scenario'),
            }
        )

        # Call LLM
        start_time = time.time()
        response = self.llm.create_message(
            system=system_prompt,
            user_content=user_prompt,
            tools=[CONFORMA_ANALYSIS_TOOL],
        )
        duration = time.time() - start_time

        # Record in Langfuse
        self.langfuse.record_generation(
            trace,
            name='diagnose-compliance-violation',
            model=self.llm.model_name(),
            prompt=user_prompt[:5000],  # Truncate for Langfuse
            completion=str(response.tool_calls),
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            duration_ms=int(duration * 1000),
        )

        # Parse response
        analysis = self.parse_analysis_response(response)
        analysis = self._annotate_fix_status(analysis, violation)

        # Estimate cost (same as build failures)
        cost_usd = (response.input_tokens * 0.000003) + (response.output_tokens * 0.000015)

        # Save to database (these fields live in analysis_json, not DB columns)
        analysis.pop('evidence_references', None)
        analysis.pop('source_transparency', None)
        analysis_id = self.ai_repo.insert_analysis(
            conforma_result_id=violation['id'],
            model_used=self.llm.model_name(),
            langfuse_trace_id=getattr(trace, 'id', None) if trace else None,
            tokens_used=response.input_tokens + response.output_tokens,
            cost_usd=cost_usd,
            analysis_duration=duration,
            analysis_json=response.tool_calls,
            **analysis
        )

        # Update pattern library with this occurrence
        pattern = self.pattern_repo.find_or_create('conforma', analysis['failure_category'])
        self.pattern_repo.record_occurrence(pattern['id'], analysis['confidence_score'])
        if analysis_id:
            self.pattern_repo.link_analysis(analysis_id, pattern['id'])

        # Finalize trace
        self.langfuse.end_trace(trace, output=analysis)

        return analysis

    MAX_RETRIES = 3

    def run(self, limit=5, component_filter=None, force=False, application=None):
        """Analyze up to `limit` pending Conforma violations.

        Args:
            limit: Maximum number of violations to analyze
            component_filter: If specified, only analyze this component
            force: If True, re-analyze even if already analyzed
            application: Override config app. None uses config default.

        Returns:
            Dict with stats: analyzed, duration
        """
        app = application or self.config.k8s.application_name
        start_time = time.time()

        logger.info("=" * 70)
        logger.info("Conforma Violation AI Analysis")
        logger.info("Application: %s", app)
        if component_filter:
            logger.info("Component filter: %s", component_filter)
        if force:
            logger.info("Force mode: Re-analyzing existing analyses")
        logger.info("=" * 70)

        pending = self.get_pending_violations(
            limit=limit, component_filter=component_filter, force=force, application=app
        )

        if not pending:
            logger.info("No pending Conforma violations to analyze")
            return {'analyzed': 0, 'duration': 0}

        logger.info("Found %d pending violations", len(pending))

        analyzed = 0
        for i, violation in enumerate(pending, 1):
            logger.info("[%d/%d] %s", i, len(pending), violation['component_name'])
            logger.info("PipelineRun: %s", violation['pipelinerun_name'])
            logger.info("Violations: %d", violation['violations_count'])

            attempts = 0
            try:
                attempts = self.ai_repo.increment_attempts(
                    conforma_result_id=violation['id']
                )
                analysis = self.analyze_violation(violation)
                analyzed += 1

                logger.info("Category: %s (confidence: %.2f)",
                           analysis['failure_category'],
                           analysis['confidence_score'])
                logger.info("Can auto-fix: %s", analysis['can_auto_fix'])
                logger.info("Root cause: %s", analysis['root_cause'][:200])
                logger.info("")

            except Exception as e:
                logger.error("Analysis failed for %s (attempt %d): %s",
                             violation['component_name'], attempts, e)
                if attempts >= self.MAX_RETRIES:
                    self.ai_repo.mark_skipped(
                        'max_retries', conforma_result_id=violation['id']
                    )
                    logger.warning(
                        "Skipping %s permanently after %d failed attempts",
                        violation['component_name'], self.MAX_RETRIES,
                    )

        # Flush Langfuse events
        self.langfuse.flush()

        duration = time.time() - start_time

        logger.info("=" * 70)
        logger.info("Analysis Complete")
        logger.info("=" * 70)
        logger.info("Analyzed: %d/%d violations", analyzed, len(pending))
        logger.info("Duration: %.1fs", duration)

        return {'analyzed': analyzed, 'duration': duration}
