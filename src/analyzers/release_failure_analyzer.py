"""Release failure analyzer using LLM provider.

Analyzes release pipeline failures by collecting context from multiple
sources (cluster, KubeArchive, GitLab, GitHub), then sending to an LLM
for root cause analysis. Follows the same pattern as BuildFailureAnalyzer
and ConformaAnalyzer.
"""

import json
import os
import re
import time

from kubernetes import client

from logger import setup_logger
from repositories import (
    DatabaseConnection, AIAnalysisRepository, ErrorPatternRepository,
    BuildFailureRepository, ConformaRepository, TriageRepository,
)
from clients.llm_provider import create_llm_provider
from clients.langfuse_tracker import LangfuseTracker
from clients.kubearchive import KubeArchiveClient
from clients.github_client import GitHubClient
from clients.gitlab_client import GitLabClient
from clients.tekton_results import TektonResultsClient
from prompt_loader import load_prompt
from openshift_auth import _ensure_k8s_config

logger = setup_logger(__name__)

RELENG_NAMESPACE = os.environ.get('RELENG_NAMESPACE', '')

GITLAB_PROJECT = os.environ.get('GITLAB_RELEASE_PROJECT', '')
GITLAB_RPA_BASE = os.environ.get('GITLAB_RPA_BASE', '')
GITLAB_EC_PATHS = [
    p.strip() for p in
    os.environ.get('GITLAB_EC_PATHS', '').split(',')
    if p.strip()
]

GITHUB_BUILD_CONFIG_OWNER = os.environ.get('GITHUB_BUILD_CONFIG_OWNER', '')
GITHUB_BUILD_CONFIG_REPO = os.environ.get('GITHUB_BUILD_CONFIG_REPO', '')

GITHUB_OPERATOR_OWNER = os.environ.get('GITHUB_OPERATOR_OWNER', 'red-hat-data-services')
GITHUB_OPERATOR_REPO = os.environ.get('GITHUB_OPERATOR_REPO', 'rhods-operator')
OPERATOR_NUDGING_PATH = os.environ.get('OPERATOR_NUDGING_PATH', 'build/operator-nudging.yaml')

RELEASE_ANALYSIS_TOOL = {
    'name': 'record_release_analysis',
    'description': 'Record the analysis of a release pipeline failure',
    'input_schema': {
        'type': 'object',
        'properties': {
            'root_cause': {
                'type': 'string',
                'description': 'What caused the release to fail'
            },
            'failure_category': {
                'type': 'string',
                'enum': [
                    'unmapped_image',
                    'rpa_mapping_typo',
                    'cross_product_dependency',
                    'missing_ec_exception',
                    'build_artifact_missing',
                    'validation_error',
                    'publish_failure',
                    'access_denied',
                    'infrastructure'
                ],
                'description': 'Category of release failure'
            },
            'confidence_score': {
                'type': 'number',
                'minimum': 0,
                'maximum': 1,
                'description': 'Confidence in this analysis (0.0-1.0)'
            },
            'recommended_fix': {
                'type': 'string',
                'description': 'Specific fix recommendation'
            },
            'recommended_files': {
                'type': 'array',
                'items': {'type': 'string'},
                'description': 'Files that need modification'
            },
            'fix_action_type': {
                'type': 'string',
                'enum': [
                    'rebuild',
                    'file_change',
                    'config_change',
                    'multi_step',
                    'investigation_needed',
                    'other'
                ],
                'description': 'Type of fix action required: rebuild (fresh build in Konflux), file_change (modify source/config files), config_change (Tekton/pipeline config), multi_step (coordinated sequence), investigation_needed (unclear root cause), other (novel pattern)'
            },
            'can_auto_fix': {
                'type': 'boolean',
                'description': 'Whether this can be automatically fixed'
            },
            'requires_human_review': {
                'type': 'boolean',
                'description': 'Whether human review is needed'
            },
            'affected_images': {
                'type': 'array',
                'items': {'type': 'string'},
                'description': 'Image refs that caused the failure'
            },
            'owner_team': {
                'type': 'string',
                'description': 'Which team should fix this (e.g., RHOAI, RHAII, RelEng)'
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
                        'description': 'Data sources actually used (e.g., "release PipelineRun logs", "RPA mapping", "EC policy YAML")'
                    },
                    'sources_unavailable': {
                        'type': 'array',
                        'items': {'type': 'string'},
                        'description': 'Sources attempted but not available (e.g., "snapshot manifest not provided", "Build-Config repo not accessible")'
                    },
                    'limitations': {
                        'type': 'array',
                        'items': {'type': 'string'},
                        'description': 'Factors that could change the diagnosis (e.g., "could not verify image digest matches snapshot")'
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


SYSTEM_PROMPT = load_prompt('release_failure_analyzer')


class ReleaseFailureAnalyzer:
    """Analyzes release pipeline failures using an LLM provider."""

    def __init__(self, config, db=None, ai_repo=None, llm=None, langfuse=None):
        if db is None:
            db = DatabaseConnection(config.db)

        self.config = config
        self.db = db
        self.ai_repo = ai_repo or AIAnalysisRepository(db)
        self.pattern_repo = ErrorPatternRepository(db)
        self.build_repo = BuildFailureRepository(db)
        self.conforma_repo = ConformaRepository(db)
        self.triage_repo = TriageRepository(db)

        if llm is None:
            if not config.llm:
                raise ValueError("LLM not configured. Set LLM_PROVIDER in .env")
            llm = create_llm_provider(config.llm)
        self.llm = llm

        if langfuse is None:
            langfuse_enabled = bool(os.environ.get('LANGFUSE_PUBLIC_KEY'))
            langfuse = LangfuseTracker(enabled=langfuse_enabled)
        self.langfuse = langfuse

        self.kubearchive = None
        self.github = None
        self.gitlab = None
        self._tekton_results = None

    def _get_kubearchive(self):
        if self.kubearchive is None:
            self.kubearchive = KubeArchiveClient(
                api_url=self.config.k8s.kubearchive_api_url,
                namespace=RELENG_NAMESPACE,
            )
        return self.kubearchive

    def _get_github(self):
        if self.github is None:
            self.github = GitHubClient(token=self.config.github_token)
        return self.github

    def _get_gitlab(self):
        if self.gitlab is None:
            self.gitlab = GitLabClient()
        return self.gitlab

    _CRD_PLURALS = {
        'release': 'releases',
        'snapshot': 'snapshots',
    }

    def _k8s_get_json(self, resource, name, namespace=None):
        ns = namespace or self.config.k8s.namespace
        plural = self._CRD_PLURALS.get(resource)
        if not plural:
            return None
        try:
            _ensure_k8s_config()
            api = client.CustomObjectsApi()
            return api.get_namespaced_custom_object(
                group='appstudio.redhat.com', version='v1alpha1',
                namespace=ns, plural=plural, name=name,
                _request_timeout=15,
            )
        except Exception:
            return None

    @staticmethod
    def _extract_violations(detailed_report):
        """Extract [Violation] lines with their ImageRef from the full report.

        Returns a list of dicts with 'image_ref', 'rule', and 'reason'.
        This runs BEFORE log truncation so violations buried in the
        middle of a large report are never lost.
        """
        violations = []
        current_image = ''
        for line in detailed_report.splitlines():
            stripped = line.strip()
            if stripped.startswith('ImageRef:'):
                current_image = stripped[len('ImageRef:'):].strip()
            elif '[Violation]' in stripped:
                rule = stripped.split('[Violation]')[-1].strip()
                violations.append({
                    'image_ref': current_image,
                    'rule': rule,
                })
            elif stripped.startswith('Reason:') and violations:
                violations[-1]['reason'] = stripped[len('Reason:'):].strip()
        return violations

    def _get_tekton_results(self, namespace='rhoai-tenant'):
        if self._tekton_results is None or self._tekton_results.namespace != namespace:
            self._tekton_results = TektonResultsClient(namespace=namespace)
        return self._tekton_results

    @staticmethod
    def _image_ref_to_component(image_ref, application_suffix=''):
        """Map a quay.io ImageRef to a Konflux component name.

        quay.io/rhoai/odh-workbench-jupyter-minimal-cpu-py312-rhel9@sha256:...
        -> odh-workbench-jupyter-minimal-cpu-py312-v3-5-ea-2
        """
        if not image_ref:
            return ''
        name_part = image_ref.split('@')[0].split('/')[-1]
        name_part = re.sub(r'-rhel\d+$', '', name_part)
        if application_suffix:
            return '{}-{}'.format(name_part, application_suffix)
        return name_part

    @staticmethod
    def _application_to_suffix(application):
        """rhoai-v3-5-ea-2 -> v3-5-ea-2"""
        match = re.match(r'\w+-(.+)', application)
        return match.group(1) if match else ''

    def _enrich_violation_context(self, context):
        """For each violated component, gather build history and failure details.

        Queries the IC database first; falls back to Tekton Results for
        build-level SHA data the DB doesn't store.
        """
        violations = context.get('violations_summary', [])
        if not violations:
            return

        application = context.get('application', '')
        app_suffix = self._application_to_suffix(application)
        build_ns = self.config.k8s.namespace

        # Deduplicate: group violations by component
        components_seen = {}
        for v in violations:
            component = self._image_ref_to_component(v.get('image_ref', ''), app_suffix)
            if component and component not in components_seen:
                components_seen[component] = v.get('image_ref', '').split('@')[-1] if '@' in v.get('image_ref', '') else ''

        logger.info("  Enriching %d violated component(s) with build context", len(components_seen))
        enriched = {}

        for component, violation_sha in components_seen.items():
            comp_data = {'component': component, 'violation_sha': violation_sha}

            # DB build history (fast)
            try:
                history = self.build_repo.get_component_history(component, application, limit=5)
                if history:
                    comp_data['build_history'] = [
                        {
                            'pipelinerun': h.get('pr_name', ''),
                            'status': h.get('status', ''),
                            'created': str(h.get('created_at', '')),
                            'error': h.get('error_message', '')[:200] if h.get('error_message') else '',
                        }
                        for h in history
                    ]
                    logger.info("    %s: %d builds from DB", component, len(history))
            except Exception as e:
                logger.debug("    %s: DB history unavailable: %s", component, e)

            # Tekton Results for SHA-level data (fallback / supplement)
            try:
                tr = self._get_tekton_results(namespace=build_ns)
                records = tr.query_component_build_history(application, component, page_size=5)
                if records:
                    builds_with_sha = []
                    for rec in records:
                        name = rec.get('metadata', {}).get('name', '')
                        created = rec.get('metadata', {}).get('creationTimestamp', '')
                        conds = rec.get('status', {}).get('conditions', [])
                        status = conds[-1].get('reason', 'Unknown') if conds else 'Unknown'
                        sha = ''
                        for r in rec.get('status', {}).get('results', []):
                            if r.get('name') == 'IMAGE_DIGEST':
                                sha = r.get('value', '')
                                break
                        builds_with_sha.append({
                            'name': name, 'created': created,
                            'status': status, 'image_sha': sha,
                        })
                    comp_data['builds_with_sha'] = builds_with_sha
                    logger.info("    %s: %d builds from Tekton Results", component, len(builds_with_sha))

                    # SHA tracing: find which build matches the violation SHA
                    if violation_sha:
                        for b in builds_with_sha:
                            if b['image_sha'] and violation_sha.startswith(b['image_sha'][:20]):
                                comp_data['violation_build'] = {
                                    'name': b['name'], 'status': b['status'],
                                    'created': b['created'],
                                }
                                break
                        # Find latest green build with different SHA
                        for b in builds_with_sha:
                            if b['status'] in ('Completed', 'Succeeded') and b['image_sha'] != violation_sha:
                                comp_data['latest_green_build'] = {
                                    'name': b['name'], 'status': b['status'],
                                    'image_sha': b['image_sha'],
                                    'created': b['created'],
                                }
                                break
            except Exception as e:
                logger.debug("    %s: Tekton Results unavailable: %s", component, e)

            # Build failure details: if latest build failed, get the failed TaskRun
            if comp_data.get('builds_with_sha'):
                latest = comp_data['builds_with_sha'][0]
                if latest['status'] in ('Failed', 'PipelineRunTimeout'):
                    try:
                        tr = self._get_tekton_results(namespace=build_ns)
                        failed_task, failed_logs, _ = tr.find_failed_taskrun(latest['name'])
                        if failed_task:
                            comp_data['failed_task'] = failed_task
                            comp_data['failed_task_logs'] = failed_logs[:500] if failed_logs else ''
                    except Exception as e:
                        logger.debug("    %s: failed TaskRun lookup error: %s", component, e)

            # Source image verification for source_image.exists violations
            self._enrich_source_image_status(comp_data, violation_sha, context)

            enriched[component] = comp_data

        context['violation_enrichment'] = enriched

    def _enrich_source_image_status(self, comp_data, violation_sha, context):
        """Check source image existence for violation SHA and latest green build SHA.

        Uses RegistryClient to query quay.io referrers API and .src tag convention.
        Results help the AI distinguish between 'needs rebuild' vs 'needs config change'.
        """
        violations_summary = context.get('violations_summary', [])
        has_source_violation = any(
            'source_image' in v.get('rule', '') or 'source_image' in v.get('message', '')
            for v in violations_summary
        )
        if not has_source_violation:
            return

        try:
            from clients.registry_client import RegistryClient
            client = RegistryClient()
        except Exception:
            return

        component = comp_data.get('component', '')

        # Determine registry/repository from violation image refs
        image_ref = ''
        for v in violations_summary:
            ref = v.get('image_ref', '')
            if component.replace('-', '') in ref.replace('-', ''):
                image_ref = ref
                break
        if not image_ref:
            return

        registry, repository, _ = client.parse_image_ref(image_ref)
        if not registry or not repository:
            return

        source_checks = {}

        # Check violation SHA
        if violation_sha and violation_sha.startswith('sha256:'):
            result = client.check_source_image(registry, repository, violation_sha)
            source_checks['violation_sha'] = {
                'digest': violation_sha[:20] + '...',
                **result,
            }
            logger.info("    %s: source image for violation SHA: %s (%s)",
                        component, result.get('exists'), result.get('method'))

        # Check latest green build SHA
        green_build = comp_data.get('latest_green_build', {})
        green_sha = green_build.get('image_sha', '')
        if green_sha and green_sha.startswith('sha256:'):
            result = client.check_source_image(registry, repository, green_sha)
            source_checks['green_build_sha'] = {
                'digest': green_sha[:20] + '...',
                'build_name': green_build.get('name', ''),
                **result,
            }
            logger.info("    %s: source image for green build SHA: %s (%s)",
                        component, result.get('exists'), result.get('method'))

        if source_checks:
            comp_data['source_image_status'] = source_checks

    def _enrich_application_context(self, context):
        """Add application-level health data: alerts, nightly, triage, conforma."""
        application = context.get('application', '')
        if not application:
            return

        logger.info("  Enriching application-level context for %s", application)

        # Active build failures
        try:
            failing = self.build_repo.find_failing_component_names(application)
            if failing:
                context['active_build_failures'] = sorted(failing)
                logger.info("    %d active build failure(s)", len(failing))
        except Exception as e:
            logger.debug("    Build failures query error: %s", e)

        # Active conforma violations
        try:
            conforma_failing = self.conforma_repo.find_unresolved_component_names(application)
            if conforma_failing:
                context['active_conforma_violations'] = sorted(conforma_failing)
                logger.info("    %d active conforma violation(s)", len(conforma_failing))
        except Exception as e:
            logger.debug("    Conforma violations query error: %s", e)

        # Triage items
        try:
            triage_items = self.triage_repo.get_active(application)
            if triage_items:
                context['triage_items'] = [
                    {
                        'id': t.get('id'),
                        'components': t.get('components', []),
                        'group_label': t.get('group_label', ''),
                        'root_cause': t.get('root_cause', ''),
                        'jira_key': t.get('jira_key', ''),
                        'status': t.get('status', ''),
                    }
                    for t in triage_items
                ]
                logger.info("    %d active triage item(s)", len(triage_items))
        except Exception as e:
            logger.debug("    Triage query error: %s", e)

        # Nightly build history
        try:
            nightly = self.build_repo.get_nightly_history(application, days=7)
            if nightly:
                context['nightly_history'] = nightly[:5]
                logger.info("    %d nightly build(s)", len(nightly))
        except Exception as e:
            logger.debug("    Nightly history query error: %s", e)

        # Component health summary (counts)
        try:
            working = self.build_repo.get_working_components(application)
            unresolved = self.build_repo.find_unresolved_component_names(application)
            context['health_summary'] = {
                'working': len(working) if working else 0,
                'failing': len(unresolved) if unresolved else 0,
            }
        except Exception as e:
            logger.debug("    Health summary error: %s", e)

    def _enrich_with_component_prs(self, context):
        """Check for nudge PRs and fetch operator-nudging.yaml for SHA verification."""
        enrichment = context.get('violation_enrichment', {})
        if not enrichment:
            return

        application = context.get('application', '')
        owner = GITHUB_OPERATOR_OWNER
        operator_repo = GITHUB_OPERATOR_REPO
        branch = self._derive_branch(application)
        # rhoai-3.5-ea.2 format for the operator branch
        base_branch = branch.replace('.', '-ea.') if 'ea' in branch else branch

        try:
            gh = self._get_github()
        except Exception:
            return

        # Fetch operator-nudging.yaml — single source of truth for image SHAs
        try:
            nudging_content = gh.get_file_content(
                owner, operator_repo, OPERATOR_NUDGING_PATH, ref=base_branch
            )
            if nudging_content:
                context['operator_nudging_content'] = nudging_content
                context['operator_nudging_source'] = '{}/{} branch {} build/operator-nudging.yaml'.format(
                    owner, operator_repo, base_branch)
                logger.info("    Got operator-nudging.yaml (%d chars)", len(nudging_content))
        except Exception as e:
            logger.debug("    operator-nudging.yaml fetch error: %s", e)

        for component, data in enrichment.items():
            try:
                prs = gh.list_pull_requests(owner, operator_repo, base=base_branch, state='all', limit=10)
                comp_short = re.sub(r'-v\d+-\d+.*$', '', component)
                nudge_prs = [
                    {'number': p.get('number'), 'title': p.get('title', '')[:100],
                     'state': p.get('state', ''), 'url': p.get('html_url', '')}
                    for p in (prs or [])
                    if comp_short in p.get('title', '').lower() or 'nudge' in p.get('title', '').lower()
                ]
                if nudge_prs:
                    data['nudge_prs'] = nudge_prs[:3]
                    logger.info("    %s: %d nudge PR(s)", component, len(nudge_prs))
            except Exception as e:
                logger.debug("    %s: PR lookup error: %s", component, e)

    def _derive_branch(self, application):
        """Derive the GitHub branch name from application name.

        product-v3-4 -> product-3.4
        product-v3-5-ea-2 -> product-3.5-ea.2
        """
        match = re.match(r'(.+)-v(\d+)-(\d+)(?:-ea-(\d+))?$', application)
        if match:
            product, major, minor, ea = match.groups()
            base = '{}-{}.{}'.format(product, major, minor)
            if ea:
                return '{}-ea.{}'.format(base, ea)
            return base
        return 'main'

    def _derive_rpa_filename(self, release_plan):
        """Derive the RPA filename from the ReleasePlan name.

        rhoai-onprem-v3-4-components-prod -> rhoai-onperm-v3-4-components-prod.yaml
        Note: The RPA files use 'onperm' (typo) not 'onprem'.
        """
        return '{}.yaml'.format(release_plan)

    def collect_context(self, release_name, namespace=None):
        """Collect all context needed for release failure analysis.

        Gathers data from cluster (oc), KubeArchive, GitLab, and GitHub.
        Returns a context dict that will be formatted into the LLM prompt.
        """
        ns = namespace or self.config.k8s.namespace
        context = {
            'release_name': release_name,
            'namespace': ns,
        }

        # 1. Get Release CR from cluster
        logger.info("  Fetching Release CR: %s", release_name)
        release_cr = self._k8s_get_json('release', release_name, ns)
        if not release_cr:
            logger.error("Release CR not found: %s in namespace %s", release_name, ns)
            return context

        spec = release_cr.get('spec', {})
        status = release_cr.get('status', {})

        context['snapshot'] = spec.get('snapshot', '')
        context['release_plan'] = spec.get('releasePlan', '')
        context['created_at'] = release_cr.get('metadata', {}).get('creationTimestamp', '')

        # Parse conditions
        conditions = status.get('conditions', [])
        context['conditions'] = [
            {
                'type': c.get('type', ''),
                'status': c.get('status', ''),
                'reason': c.get('reason', ''),
                'message': c.get('message', ''),
            }
            for c in conditions
        ]

        # Determine target (prod/stage) and type (components/fbc/charts)
        rp = context['release_plan']
        context['target'] = 'prod' if 'prod' in rp else 'stage'
        if 'fbc' in rp:
            context['type'] = 'fbc'
        elif 'chart' in rp:
            context['type'] = 'charts'
        else:
            context['type'] = 'components'

        # Determine application name
        application = spec.get('snapshot', '').rsplit('-', 1)[0] if spec.get('snapshot') else ''
        if not application:
            application = self.config.k8s.application_name
        context['application'] = application

        # Pipeline info
        managed = status.get('managedProcessing', {})
        pipeline_ref = managed.get('pipelineRun', '')
        context['pipeline'] = {'ref': pipeline_ref}

        if pipeline_ref:
            pipeline_parts = pipeline_ref.split('/')
            pipeline_name = pipeline_parts[-1] if pipeline_parts else pipeline_ref

            # Extract failed task from conditions
            for c in conditions:
                msg = c.get('message', '')
                if 'failed' in msg.lower() and c.get('status') == 'False':
                    task_match = re.search(r'task (\S+) failed', msg)
                    if task_match:
                        context['pipeline']['failed_task'] = task_match.group(1)

            # 2. Get pipeline details from KubeArchive
            logger.info("  Fetching pipeline logs from KubeArchive: %s", pipeline_name)
            try:
                ka = self._get_kubearchive()

                # Get TaskRun for the failed task
                failed_task = context['pipeline'].get('failed_task', '')
                if failed_task:
                    taskrun_name = '{}-{}'.format(pipeline_name, failed_task)
                    taskrun = ka.get_taskrun(taskrun_name, namespace=RELENG_NAMESPACE)
                    if taskrun:
                        # Extract TEST_OUTPUT from results
                        results = taskrun.get('status', {}).get('results', [])
                        for r in results:
                            if r.get('name') == 'TEST_OUTPUT':
                                try:
                                    context['pipeline']['test_output'] = json.loads(r['value'])
                                except (json.JSONDecodeError, KeyError):
                                    pass

                    # Get pod logs
                    pod_name = '{}-pod'.format(taskrun_name)
                    for step in ['step-validate', 'step-report', 'step-detailed-report']:
                        logs = ka.get_pod_logs(pod_name, container=step, namespace=RELENG_NAMESPACE)
                        if logs:
                            # Extract violations BEFORE truncating (they can be
                            # anywhere in the report and truncation loses them)
                            if step == 'step-detailed-report':
                                violations = self._extract_violations(logs)
                                if violations:
                                    context['violations_summary'] = violations
                                    logger.info("    Extracted %d violation(s) from %s",
                                                len(violations), step)
                            if len(logs) > 50000:
                                logs = logs[-50000:]
                            context.setdefault('logs', {})
                            context['logs'][step] = logs
                            logger.info("    Got %d chars from %s", len(logs), step)
            except Exception as e:
                logger.warning("  KubeArchive unavailable: %s", e)

        # 3. Get Snapshot components
        snapshot_name = context.get('snapshot', '')
        if snapshot_name:
            logger.info("  Fetching Snapshot: %s", snapshot_name)
            snapshot_cr = self._k8s_get_json('snapshot', snapshot_name, ns)
            if snapshot_cr:
                components = snapshot_cr.get('spec', {}).get('components', [])
                context['snapshot_components'] = [
                    {'name': c.get('name', ''), 'image': c.get('containerImage', '')}
                    for c in components
                ]
                logger.info("    %d components", len(context.get('snapshot_components', [])))
            else:
                logger.warning("    Snapshot not found (may be GC'd)")

        # 4. Get RPA mappings from GitLab
        if context.get('release_plan'):
            logger.info("  Fetching RPA mappings from GitLab")
            try:
                gl = self._get_gitlab()
                # Try to find the RPA file
                rpa_filename = self._derive_rpa_filename(context['release_plan'])
                rpa_path = '{}/{}'.format(GITLAB_RPA_BASE, rpa_filename)
                rpa_content = gl.get_file_content(GITLAB_PROJECT, rpa_path)
                if rpa_content:
                    context['rpa_content'] = rpa_content
                    context['rpa_source'] = rpa_path
                    logger.info("    Got RPA: %s", rpa_filename)
                else:
                    # Try listing the directory to find available RPAs
                    rpa_files = gl.list_directory(GITLAB_PROJECT, GITLAB_RPA_BASE)
                    if rpa_files:
                        available = [f['name'] for f in rpa_files if f['name'].endswith('.yaml')]
                        logger.info("    RPA %s not found. Available: %s", rpa_filename, available[:5])
                        # Try matching by pattern
                        for f in available:
                            if context['release_plan'].replace('onprem', 'onperm') in f or \
                               context['release_plan'] in f:
                                rpa_content = gl.get_file_content(GITLAB_PROJECT, '{}/{}'.format(GITLAB_RPA_BASE, f))
                                if rpa_content:
                                    context['rpa_content'] = rpa_content
                                    context['rpa_source'] = '{}/{}'.format(GITLAB_RPA_BASE, f)
                                    logger.info("    Found matching RPA: %s", f)
                                    break
            except Exception as e:
                logger.warning("  GitLab unavailable: %s", e)

        # 5. Get EC policy exceptions from GitLab
        logger.info("  Fetching EC policies from GitLab")
        try:
            gl = self._get_gitlab()
            ec_contents = []
            for ec_base in GITLAB_EC_PATHS:
                ec_files = gl.list_directory(GITLAB_PROJECT, ec_base)
                if ec_files:
                    for f in ec_files:
                        if f['name'].endswith('.yaml'):
                            content = gl.get_file_content(GITLAB_PROJECT, f['path'])
                            if content:
                                ec_contents.append({
                                    'path': f['path'],
                                    'content': content[:10000],
                                })
            if ec_contents:
                context['ec_policies'] = ec_contents
                logger.info("    Got %d EC policy files", len(ec_contents))
        except Exception as e:
            logger.warning("  GitLab EC policies unavailable: %s", e)

        # 6. Get bundle images from GitHub
        if context.get('application'):
            logger.info("  Fetching bundle images from GitHub")
            try:
                gh = self._get_github()
                branch = self._derive_branch(context['application'])
                patch_content = gh.get_file_content(
                    GITHUB_BUILD_CONFIG_OWNER, GITHUB_BUILD_CONFIG_REPO,
                    'bundle/additional-images-patch.yaml', ref=branch
                )
                if patch_content:
                    context['bundle_images_content'] = patch_content
                    context['bundle_images_source'] = 'RHOAI-Build-Config bundle/additional-images-patch.yaml ({})'.format(branch)
                    logger.info("    Got bundle images patch (%d chars)", len(patch_content))
            except Exception as e:
                logger.warning("  GitHub unavailable: %s", e)

        # 7. Enrich with build context for violated components
        self._enrich_violation_context(context)

        # 8. Enrich with application-level health data
        self._enrich_application_context(context)

        # 9. Check for nudge PRs for violated components
        self._enrich_with_component_prs(context)

        return context

    def build_analysis_prompt(self, context):
        """Construct system + user prompts from collected context."""

        sections = ["Analyze this release pipeline failure. Identify the root cause and recommend specific fixes.\n"]

        # Release info
        sections.append("## Release")
        sections.append("- Release: {}".format(context.get('release_name', 'unknown')))
        sections.append("- Application: {}".format(context.get('application', 'unknown')))
        sections.append("- Snapshot: {}".format(context.get('snapshot', 'unknown')))
        sections.append("- ReleasePlan: {}".format(context.get('release_plan', 'unknown')))
        sections.append("- Target: {}".format(context.get('target', 'unknown')))
        sections.append("- Type: {}".format(context.get('type', 'unknown')))
        sections.append("- Created: {}".format(context.get('created_at', 'unknown')))

        # Conditions
        conditions = context.get('conditions', [])
        if conditions:
            sections.append("\n## Release Conditions")
            for c in conditions:
                sections.append("- {}: status={}, reason={}, message={}".format(
                    c['type'], c['status'], c['reason'], c['message'][:500]
                ))

        # Pipeline info
        pipeline = context.get('pipeline', {})
        if pipeline.get('ref'):
            sections.append("\n## Pipeline")
            sections.append("- PipelineRun: {}".format(pipeline['ref']))
            if pipeline.get('failed_task'):
                sections.append("- Failed task: {}".format(pipeline['failed_task']))
            test_output = pipeline.get('test_output', {})
            if test_output:
                sections.append("- Test output: successes={}, failures={}, warnings={}, result={}".format(
                    test_output.get('successes', 0), test_output.get('failures', 0),
                    test_output.get('warnings', 0), test_output.get('result', 'unknown')
                ))

        # Pre-extracted violations (immune to log truncation)
        violations = context.get('violations_summary', [])
        if violations:
            sections.append("\n## VIOLATIONS FOUND (extracted from step-detailed-report before truncation)")
            sections.append("These are the AUTHORITATIVE policy violations that caused the release to fail.")
            sections.append("Use these to determine failure_category — NOT the step-validate errors below.\n")
            rules = {}
            for v in violations:
                rule = v.get('rule', 'unknown')
                rules.setdefault(rule, []).append(v.get('image_ref', ''))
            for rule, images in rules.items():
                sections.append("Rule: {} ({} violation(s))".format(rule, len(images)))
                for img in images:
                    reason = ''
                    for v in violations:
                        if v.get('image_ref') == img and v.get('rule') == rule:
                            reason = v.get('reason', '')
                            break
                    sections.append("  - ImageRef: {}".format(img))
                    if reason:
                        sections.append("    Reason: {}".format(reason))

        # Pipeline logs — with descriptive headers per step
        logs = context.get('logs', {})
        if logs:
            sections.append("\n## Pipeline Logs")
            step_descriptions = {
                'step-detailed-report': 'AUTHORITATIVE policy violations — [Violation] lines here are the actual failures',
                'step-validate': 'Image evaluation results and fetch errors — NOT counted as policy violations',
                'step-report': 'Summary statistics',
            }
            for step_name, log_content in logs.items():
                desc = step_descriptions.get(step_name, '')
                header = "\n### {} logs".format(step_name)
                if desc:
                    header += " ({})".format(desc)
                sections.append(header)
                sections.append("```")
                sections.append(log_content)
                sections.append("```")

        # Snapshot components
        components = context.get('snapshot_components', [])
        if components:
            sections.append("\n## Snapshot Components ({} total)".format(len(components)))
            for c in components[:120]:
                sections.append("- {}: {}".format(c['name'], c['image']))

        # RPA mappings
        if context.get('rpa_content'):
            sections.append("\n## RPA Mappings (from {})".format(context.get('rpa_source', 'GitLab')))
            sections.append("```yaml")
            sections.append(context['rpa_content'][:30000])
            sections.append("```")

        # EC policies
        if context.get('ec_policies'):
            sections.append("\n## EC Policy Exception Rules")
            for ec in context['ec_policies']:
                sections.append("\n### {}".format(ec['path']))
                sections.append("```yaml")
                sections.append(ec['content'])
                sections.append("```")

        # Bundle images
        if context.get('bundle_images_content'):
            sections.append("\n## Bundle Images (from {})".format(
                context.get('bundle_images_source', 'GitHub')
            ))
            sections.append("```yaml")
            sections.append(context['bundle_images_content'][:30000])
            sections.append("```")

        # ---- ENRICHED CONTEXT ----

        # Operator nudging file (source of truth for image SHAs in FBC)
        if context.get('operator_nudging_content'):
            sections.append("\n## Operator Nudging File (from {})".format(
                context.get('operator_nudging_source', 'GitHub')
            ))
            sections.append("This file maps component images to their SHA digests.")
            sections.append("The operator-processor updates this file nightly from quay.io.")
            sections.append("If a violation SHA matches a SHA here, the nudging picked up a bad build.")
            nudging = context['operator_nudging_content']
            # Only include relevant lines (search for violated component names)
            enrichment = context.get('violation_enrichment', {})
            if enrichment and len(nudging) > 5000:
                relevant_lines = []
                for comp in enrichment:
                    comp_short = re.sub(r'-v\d+-\d+.*$', '', comp)
                    for i, line in enumerate(nudging.splitlines()):
                        if comp_short in line:
                            start = max(0, i - 1)
                            end = min(len(nudging.splitlines()), i + 3)
                            for j in range(start, end):
                                relevant_lines.append('L{}: {}'.format(j + 1, nudging.splitlines()[j]))
                            relevant_lines.append('')
                if relevant_lines:
                    sections.append("```yaml (relevant lines only)")
                    sections.append('\n'.join(relevant_lines))
                    sections.append("```")
                else:
                    sections.append("```yaml")
                    sections.append(nudging[:8000])
                    sections.append("```")
            else:
                sections.append("```yaml")
                sections.append(nudging[:8000])
                sections.append("```")

        # Build history and SHA tracing for violated components
        enrichment = context.get('violation_enrichment', {})
        if enrichment:
            sections.append("\n## Component Build History (for violated images)")
            sections.append("For each component whose image violated a policy, here is the build history.")
            sections.append("Compare the violation SHA against the builds to trace which build the snapshot used.\n")

            for comp, data in enrichment.items():
                sections.append("### {}".format(comp))

                # SHA tracing summary
                if data.get('violation_build'):
                    vb = data['violation_build']
                    sections.append("VIOLATION SHA belongs to build: {} (status: {}, created: {})".format(
                        vb['name'], vb['status'], vb['created']))
                if data.get('latest_green_build'):
                    gb = data['latest_green_build']
                    sections.append("LATEST GREEN BUILD: {} (sha: {}, created: {})".format(
                        gb['name'], gb['image_sha'], gb['created']))
                    if data.get('violation_build'):
                        sections.append(">> SHA MISMATCH: Snapshot uses a {} build, not the latest green build.".format(
                            data['violation_build']['status']))

                # Source image verification results
                source_status = data.get('source_image_status', {})
                if source_status:
                    sections.append("SOURCE IMAGE VERIFICATION:")
                    for key, check in source_status.items():
                        label = 'Violation SHA' if key == 'violation_sha' else 'Green Build SHA'
                        exists = check.get('exists')
                        if exists is True:
                            status_str = 'PRESENT'
                        elif exists is False:
                            status_str = 'MISSING'
                        else:
                            status_str = 'UNKNOWN'
                        sections.append("  {} ({}): source image {} — {}".format(
                            label, check.get('digest', '?'),
                            status_str, check.get('details', '')))
                    # Add diagnostic guidance
                    v_check = source_status.get('violation_sha', {})
                    g_check = source_status.get('green_build_sha', {})
                    if v_check.get('exists') is False and g_check.get('exists') is True:
                        sections.append(">> DIAGNOSTIC: Violation SHA has no source image but green build does.")
                        sections.append(">> This confirms the fix is a REBUILD — the green build SHA has a valid source image.")
                    elif v_check.get('exists') is False and g_check.get('exists') is False:
                        sections.append(">> DIAGNOSTIC: BOTH SHAs lack source images.")
                        sections.append(">> A simple rebuild may NOT fix this — the source-build task may be misconfigured or timing out consistently.")

                # Build history with SHAs
                builds = data.get('builds_with_sha', [])
                if builds:
                    sections.append("Recent builds:")
                    for b in builds:
                        sections.append("  - {} | {} | sha={} | {}".format(
                            b['created'], b['status'],
                            b['image_sha'] if b['image_sha'] else 'n/a',
                            b['name']))

                # Failed task details
                if data.get('failed_task'):
                    sections.append("Failed task: {}".format(data['failed_task']))
                if data.get('failed_task_logs'):
                    sections.append("Error: {}".format(data['failed_task_logs']))

                # DB build history (complementary)
                db_history = data.get('build_history', [])
                if db_history and not builds:
                    sections.append("Build history (from DB):")
                    for h in db_history:
                        sections.append("  - {} | {} | {}".format(
                            h['created'], h['status'], h['pipelinerun']))
                        if h['error']:
                            sections.append("    Error: {}".format(h['error']))

                # Nudge PRs with clickable URLs
                nudge_prs = data.get('nudge_prs', [])
                if nudge_prs:
                    sections.append("Nudge PRs in rhods-operator:")
                    for pr in nudge_prs:
                        pr_url = pr.get('html_url', '')
                        if not pr_url and GITHUB_OPERATOR_OWNER and GITHUB_OPERATOR_REPO:
                            pr_url = 'https://github.com/{}/{}/pull/{}'.format(
                                GITHUB_OPERATOR_OWNER, GITHUB_OPERATOR_REPO, pr['number'])
                        sections.append("  - PR #{}: {} [{}] — {}".format(
                            pr['number'], pr['title'], pr['state'], pr_url))

                sections.append("")

        # Application health summary
        if context.get('health_summary'):
            hs = context['health_summary']
            sections.append("\n## Application Health")
            sections.append("- Working components: {}".format(hs.get('working', '?')))
            sections.append("- Failing components: {}".format(hs.get('failing', '?')))

        if context.get('active_build_failures'):
            sections.append("- Components with active build failures: {}".format(
                ', '.join(context['active_build_failures'][:20])))

        if context.get('active_conforma_violations'):
            sections.append("- Components with active conforma violations: {}".format(
                ', '.join(context['active_conforma_violations'][:20])))

        # Nightly build history
        nightly = context.get('nightly_history', [])
        if nightly:
            sections.append("\n## Recent Nightly Builds")
            for n in nightly:
                sections.append("- {} | {} | {}".format(
                    n.get('created_at', ''), n.get('status', ''),
                    n.get('pr_name', '')))

        # Triage items
        triage = context.get('triage_items', [])
        if triage:
            sections.append("\n## Active Triage Items")
            sections.append("These are issues currently being tracked/investigated:")
            for t in triage:
                components_str = ', '.join(t.get('components', [])[:5])
                sections.append("- Triage #{}: {} | {} | Jira: {} | Components: {}".format(
                    t.get('id', '?'), t.get('group_label', ''),
                    t.get('root_cause', '')[:100], t.get('jira_key', 'none'),
                    components_str))

        # Reference documentation URLs for evidence_references
        sections.append("\n## Reference Documentation")
        sections.append("Use these URLs in evidence_references when relevant:")
        sections.append("- EC policy customization: https://konflux-ci.dev/docs/compliance/customizing-policy/")
        sections.append("- Policy evaluations: https://konflux-ci.dev/docs/compliance/policy-evaluations/")
        sections.append("- Release troubleshooting: https://konflux-ci.dev/docs/troubleshooting/releases/")
        sections.append("- Conforma policy config: https://conforma.dev/docs/cli/configuration.html")
        sections.append("- Hermetic builds: https://konflux-ci.dev/docs/building/hermetic-builds/")
        if GITHUB_OPERATOR_OWNER and GITHUB_OPERATOR_REPO:
            sections.append("- operator-nudging.yaml: https://github.com/{}/{}/blob/main/{}".format(
                GITHUB_OPERATOR_OWNER, GITHUB_OPERATOR_REPO, OPERATOR_NUDGING_PATH))
            sections.append("- Nudge PRs search: https://github.com/{}/{}/pulls?q=nudge".format(
                GITHUB_OPERATOR_OWNER, GITHUB_OPERATOR_REPO))
        if GITHUB_BUILD_CONFIG_OWNER and GITHUB_BUILD_CONFIG_REPO:
            sections.append("- Build-Config repo: https://github.com/{}/{}".format(
                GITHUB_BUILD_CONFIG_OWNER, GITHUB_BUILD_CONFIG_REPO))
        if context.get('rpa_source'):
            sections.append("- RPA file: {}".format(context['rpa_source']))
        if context.get('ec_policies'):
            for ec in context['ec_policies']:
                sections.append("- EC policy: {}".format(ec['path']))

        # Dynamic URLs from enrichment (nudge PRs, quay images)
        if enrichment:
            sections.append("\n### Component-specific URLs (use in evidence_references)")
            for comp, data in enrichment.items():
                nudge_prs = data.get('nudge_prs', [])
                for pr in nudge_prs:
                    pr_url = pr.get('html_url', '')
                    if not pr_url and GITHUB_OPERATOR_OWNER and GITHUB_OPERATOR_REPO:
                        pr_url = 'https://github.com/{}/{}/pull/{}'.format(
                            GITHUB_OPERATOR_OWNER, GITHUB_OPERATOR_REPO, pr['number'])
                    if pr_url:
                        sections.append("- Nudge PR #{} ({}): {}".format(
                            pr['number'], comp, pr_url))

        # Institutional memory: include known patterns for this failure type
        pattern_section = self._format_pattern_section(context)
        if pattern_section:
            sections.append(pattern_section)

        # Targeted knowledge graph context (fails silently if Neo4j unavailable)
        try:
            from utils.graph_context import release_context
            graph_section = release_context(context)
            if graph_section:
                sections.append(graph_section)
        except Exception:
            pass

        sections.append("\nUse the record_release_analysis tool. Remember:")
        sections.append("- CRITICAL: Determine failure_category from step-detailed-report [Violation] lines, NOT from step-validate errors")
        sections.append("- If step-validate has errors (UNAUTHORIZED, 404), report them in root_cause as secondary issues but use the violation rule from step-detailed-report as the primary category")
        sections.append("- Use Component Build History to trace which build the snapshot used — compare violation SHA against build SHAs")
        sections.append("- If SHA MISMATCH is flagged, explain that the snapshot uses a stale/broken build instead of the latest green one")
        sections.append("- Check operator-nudging.yaml to verify whether the nudging picked up the correct SHA")
        sections.append("- Cross-reference Active Triage Items to see if the issue is already being tracked")
        sections.append("- Include Nightly Build status and Application Health in your diagnosis context")
        sections.append("- State ONLY what you observe in the evidence")
        sections.append("- Quote exact error messages from logs")
        sections.append("- Cite the source for every claim (log line, RPA file, bundle file, build history, nudging file)")
        sections.append("- Identify the specific team that owns the fix")
        sections.append("- Include source_transparency: list what data you used, what was missing, and what limitations affect your diagnosis")
        sections.append("- Set fix_action_type: rebuild, file_change, config_change, multi_step, investigation_needed, or other")
        sections.append("- If SOURCE IMAGE VERIFICATION shows violation SHA missing but green build present → fix is nudging update + propagation (multi_step), NOT rebuild")
        sections.append("- If BOTH violation and green build SHAs lack source images → fix_action_type MUST be investigation_needed")
        sections.append("- Include verification commands in recommended_fix: specific skopeo/curl/gh commands using the actual SHAs and component names from this context")

        user_prompt = '\n'.join(sections)
        return (SYSTEM_PROMPT, user_prompt)

    def _format_pattern_section(self, context):
        """Return a prompt section with institutional memory from seeded patterns."""
        patterns = self.pattern_repo.get_all(failure_type='release')
        if not patterns:
            return ""
        parts = ["\n## Known Release Failure Patterns\n"]
        for p in patterns:
            parts.append("### {}\n{}\n".format(p['pattern_name'], p['description']))
            if p.get('typical_fix'):
                parts.append("**Typical fix:**\n{}\n".format(p['typical_fix']))
        return '\n'.join(parts)

    def _apply_confidence_cap(self, analysis, context):
        """Cap confidence based on available enrichment sources.

        The AI might report high confidence from pattern matching alone.
        If key enrichment data was missing, cap the confidence to reflect
        the incomplete evidence.
        """
        score = analysis.get('confidence_score', 0.0)
        penalties = []

        if not context.get('violation_enrichment'):
            penalties.append(('no component build history', 0.10))
        if not context.get('operator_nudging_content'):
            penalties.append(('no operator-nudging.yaml', 0.10))
        if not context.get('health_summary'):
            penalties.append(('no application health data', 0.05))
        if not context.get('triage_items'):
            penalties.append(('no triage items data', 0.05))
        if not context.get('violations_summary') and not context.get('logs'):
            penalties.append(('no pipeline logs or violations', 0.15))

        if penalties:
            total_penalty = sum(p for _, p in penalties)
            max_allowed = max(0.55, 1.0 - total_penalty)
            if score > max_allowed:
                reasons = ', '.join(r for r, _ in penalties)
                logger.info("  Confidence capped: %.2f -> %.2f (missing: %s)",
                            score, max_allowed, reasons)
                analysis['confidence_score'] = round(max_allowed, 2)
                # Append limitation to source_transparency
                transparency = analysis.get('source_transparency', {})
                if transparency:
                    limitations = transparency.get('limitations', [])
                    limitations.append(
                        'Confidence capped from {:.2f} to {:.2f} due to missing enrichment: {}'.format(
                            score, max_allowed, reasons))
                    transparency['limitations'] = limitations
        return analysis

    def parse_analysis_response(self, llm_response):
        """Extract structured analysis from LLMResponse.tool_calls."""
        from pydantic import ValidationError
        from analyzers.models import ReleaseAnalysisResult

        if not llm_response.tool_calls:
            raise ValueError("LLM did not return tool_use response")

        analysis_call = None
        for call in llm_response.tool_calls:
            if call.get('name') == 'record_release_analysis':
                analysis_call = call
                break

        if not analysis_call:
            raise ValueError("LLM did not call record_release_analysis tool")

        input_data = analysis_call.get('input', {})

        try:
            result = ReleaseAnalysisResult(**input_data)
            return result.model_dump()
        except ValidationError as e:
            logger.error("LLM returned invalid release analysis: %s", e)
            logger.error("Raw input_data: %s", input_data)
            return {
                'root_cause': input_data.get('root_cause', 'Invalid LLM response'),
                'failure_category': 'infrastructure',
                'confidence_score': 0.0,
                'recommended_fix': input_data.get('recommended_fix', 'Manual review required'),
                'recommended_files': input_data.get('recommended_files', []),
                'can_auto_fix': False,
                'requires_human_review': True,
                'affected_images': input_data.get('affected_images', []),
                'owner_team': input_data.get('owner_team', ''),
            }

    def _is_release_failed(self, release_name, namespace=None):
        """Quick check if a release has failed, without collecting full context."""
        ns = namespace or self.config.k8s.namespace
        release_cr = self._k8s_get_json('release', release_name, ns)
        if not release_cr:
            return None
        conditions = release_cr.get('status', {}).get('conditions', [])
        return any(
            c.get('type') in ('Released', 'ManagedPipelineProcessed', 'Validated')
            and c.get('status') == 'False'
            for c in conditions
        )

    def analyze_release(self, release_name, namespace=None, force=False):
        """Full analysis pipeline: collect context -> LLM -> parse -> save."""

        # Check for existing analysis
        if not force:
            existing = self._get_existing_analysis(release_name)
            if existing:
                logger.info("Release already analyzed (use --force to re-analyze)")
                return existing

        # Quick failure check before expensive context collection
        failed = self._is_release_failed(release_name, namespace)
        if failed is None:
            raise ValueError("Release CR not found: {}".format(release_name))
        if not failed:
            logger.info("Release has not failed — nothing to analyze")
            return {'status': 'not_failed', 'release_name': release_name}

        logger.info("Collecting release context...")
        context = self.collect_context(release_name, namespace)

        if not context.get('conditions'):
            raise ValueError("Could not fetch Release CR — no conditions found")

        logger.info("\nAnalyzing with LLM...")
        system_prompt, user_prompt = self.build_analysis_prompt(context)

        trace = self.langfuse.create_trace(
            name='release-failure-analysis',
            input_data={
                'release': release_name,
                'application': context.get('application', ''),
                'target': context.get('target', ''),
            },
            metadata={
                'snapshot': context.get('snapshot', ''),
                'failed_task': context.get('pipeline', {}).get('failed_task', ''),
            }
        )

        start_time = time.time()
        response = self.llm.create_message(
            system=system_prompt,
            user_content=user_prompt,
            tools=[RELEASE_ANALYSIS_TOOL],
        )
        duration = time.time() - start_time

        self.langfuse.record_generation(
            trace,
            name='diagnose-release-failure',
            model=self.llm.model_name(),
            prompt=user_prompt[:5000],
            completion=str(response.tool_calls),
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            duration_ms=int(duration * 1000),
        )

        analysis = self.parse_analysis_response(response)
        analysis = self._apply_confidence_cap(analysis, context)
        analysis = self._validate_fix_action_type(analysis, context)
        analysis = self._annotate_fix_status(analysis, context)

        cost_usd = (response.input_tokens * 0.000003) + (response.output_tokens * 0.000015)

        # These fields live in analysis_json, not DB columns
        analysis.pop('evidence_references', None)
        analysis.pop('source_transparency', None)
        fix_action = analysis.pop('fix_action_type', None)

        tool_calls = response.tool_calls or []
        if fix_action and isinstance(tool_calls, list):
            for tc in tool_calls:
                if isinstance(tc, dict) and tc.get('name') == 'record_release_analysis':
                    tc.setdefault('input', {})['fix_action_type'] = fix_action
                    break

        analysis_id = self.ai_repo.insert_release_analysis(
            release_name=release_name,
            model_used=self.llm.model_name(),
            langfuse_trace_id=getattr(trace, 'id', None) if trace else None,
            tokens_used=response.input_tokens + response.output_tokens,
            cost_usd=cost_usd,
            analysis_duration=duration,
            analysis_json=tool_calls,
            **analysis
        )

        pattern = self.pattern_repo.find_or_create('release', analysis['failure_category'])
        self.pattern_repo.record_occurrence(pattern['id'], analysis['confidence_score'])
        if analysis_id:
            self.pattern_repo.link_analysis(analysis_id, pattern['id'])

        self.langfuse.end_trace(trace, output=analysis)
        self.langfuse.flush()

        return analysis

    _VALID_FIX_ACTIONS = {
        'rebuild', 'file_change', 'config_change',
        'multi_step', 'investigation_needed', 'other',
    }

    _CATEGORY_FIX_HINTS = {
        'build_artifact_missing': 'rebuild',
        'unmapped_image': 'file_change',
        'rpa_mapping_typo': 'file_change',
        'missing_ec_exception': 'file_change',
        'access_denied': 'investigation_needed',
        'infrastructure': 'investigation_needed',
    }

    def _validate_fix_action_type(self, analysis, context):
        """Validate and correct the LLM's fix_action_type classification."""
        proposed = analysis.get('fix_action_type', '')

        # If LLM didn't provide one, infer from failure_category
        if not proposed or proposed not in self._VALID_FIX_ACTIONS:
            category = analysis.get('failure_category', '')
            proposed = self._CATEGORY_FIX_HINTS.get(category, 'investigation_needed')

        # Source image correction: if source_image violation and green build has source image → rebuild
        enrichment = context.get('violation_enrichment', {})
        for data in enrichment.values():
            source_status = data.get('source_image_status', {})
            v_check = source_status.get('violation_sha', {})
            g_check = source_status.get('green_build_sha', {})
            if v_check.get('exists') is False and g_check.get('exists') is True:
                proposed = 'rebuild'
            elif v_check.get('exists') is False and g_check.get('exists') is False:
                proposed = 'investigation_needed'

        analysis['fix_action_type'] = proposed
        return analysis

    def _annotate_fix_status(self, analysis, context):
        """Append fix-status notes and verification snippets after LLM analysis."""
        notes = []
        snippets = []
        enrichment = context.get('violation_enrichment', {})

        for component, data in enrichment.items():
            nudge_prs = data.get('nudge_prs', [])
            merged = [p for p in nudge_prs if p.get('state') == 'closed']
            open_prs = [p for p in nudge_prs if p.get('state') == 'open']

            if merged:
                pr = merged[0]
                pr_num = pr.get('number', '?')
                notes.append('- Nudge PR #{} for {} was recently merged — fix may already be propagating'.format(
                    pr_num, component))
                # Warn about nightly overwrite risk
                source_status = data.get('source_image_status', {})
                v_check = source_status.get('violation_sha', {})
                if v_check.get('exists') is False:
                    notes.append(
                        '  WARNING: If quay tag-latest still points to the bad build, '
                        'the nightly operator-processor may overwrite this nudge PR. '
                        'A fresh rebuild is the definitive fix.'
                    )
            elif open_prs:
                notes.append('- Nudge PR #{} for {} is open — fix is in progress'.format(
                    open_prs[0].get('number', '?'), component))

            # Check if latest build already succeeded (rebuild not needed)
            builds_with_sha = data.get('builds_with_sha', [])
            if builds_with_sha:
                latest = builds_with_sha[0]
                if latest.get('status') in ('Completed', 'Succeeded'):
                    green_sha = latest.get('image_sha', '')[:16]
                    notes.append('- {} latest build {} is green (sha: {}...) — automated nudge should pick it up'.format(
                        component, latest.get('name', '?'), green_sha))

            # Verification snippets from enrichment data
            self._build_verification_snippets(snippets, component, data, context)

        triage_items = context.get('triage_items', [])
        if triage_items:
            active = [t for t in triage_items if t.get('status') == 'active']
            if active:
                notes.append('- Triage item #{} is actively tracking this issue'.format(
                    active[0].get('id', '?')))

        suffix_parts = []
        if notes:
            suffix_parts.append('\n\nFix Status (auto-detected):\n' + '\n\n'.join(notes))
        if snippets:
            suffix_parts.append('\n\nVerification commands:\n' + '\n\n'.join(snippets))

        if suffix_parts:
            analysis['recommended_fix'] = analysis.get('recommended_fix', '') + ''.join(suffix_parts)

        return analysis

    def _build_verification_snippets(self, snippets, component, data, context):
        """Generate copy-pasteable verification commands from enrichment data."""
        violation_sha = data.get('violation_sha', '')
        green_build = data.get('latest_green_build', {})
        green_sha = green_build.get('image_sha', '')

        # Source image check for violation SHA
        if violation_sha and violation_sha.startswith('sha256:'):
            image_refs = context.get('violations_summary', [])
            image_ref = ''
            for v in image_refs:
                ref = v.get('image_ref', '')
                if component.replace('-', '') in ref.replace('-', ''):
                    image_ref = ref.split('@')[0] if '@' in ref else ref
                    break
            if image_ref:
                snippets.append(
                    '- Check source image for violation SHA:\n'
                    '  skopeo inspect --raw docker://{}@{}'.format(image_ref, violation_sha)
                )

        # Nudging yaml and PR checks
        if GITHUB_OPERATOR_OWNER and GITHUB_OPERATOR_REPO:
            comp_short = re.sub(r'-v\d+-\d+.*$', '', component)
            snippets.append(
                '- Check current nudging SHA for {}:\n'
                '  curl -sL "https://raw.githubusercontent.com/{}/{}/main/{}" | grep -A2 "{}"'.format(
                    component, GITHUB_OPERATOR_OWNER, GITHUB_OPERATOR_REPO,
                    OPERATOR_NUDGING_PATH, comp_short)
            )
            snippets.append(
                '- Check recent nudge PRs:\n'
                '  gh pr list -R {}/{} --search "{} nudge" --state all --limit 5'.format(
                    GITHUB_OPERATOR_OWNER, GITHUB_OPERATOR_REPO, comp_short)
            )

        # Green build SHA for fix reference
        if green_sha:
            snippets.append(
                '- Latest green build SHA for fix reference:\n'
                '  {} (build: {})'.format(green_sha, green_build.get('name', '?'))
            )

    def _get_existing_analysis(self, release_name):
        return self.ai_repo.get_analysis_for_release(release_name)
