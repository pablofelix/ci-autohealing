"""Release failure analyzer using LLM provider.

Analyzes release pipeline failures by collecting context from multiple
sources (cluster, KubeArchive, GitLab, GitHub), then sending to an LLM
for root cause analysis. Follows the same pattern as BuildFailureAnalyzer
and ConformaAnalyzer.
"""

import json
import os
import re
import subprocess
import time

from logger import setup_logger
from repositories import DatabaseConnection, AIAnalysisRepository, ErrorPatternRepository
from clients.llm_provider import create_llm_provider
from clients.langfuse_tracker import LangfuseTracker
from clients.kubearchive import KubeArchiveClient
from clients.github_client import GitHubClient
from clients.gitlab_client import GitLabClient
from prompt_loader import load_prompt

logger = setup_logger(__name__)

RELENG_NAMESPACE = 'releng-tenant'

GITLAB_PROJECT = 'releng/konflux-release-data'
GITLAB_RPA_BASE = 'config/CLUSTER_SHORT/product/ReleasePlanAdmission/rhoai'
GITLAB_EC_PATHS = [
    'config/CLUSTER_SHORT/product/EnterpriseContractPolicy',
    'config/common/product/EnterpriseContractPolicy',
]

GITHUB_BUILD_CONFIG_OWNER = 'acme-org'
GITHUB_BUILD_CONFIG_REPO = 'RHOAI-Build-Config'

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
        # type: (CollectorConfig, ...) -> None
        if db is None:
            db = DatabaseConnection(config.db)

        self.config = config
        self.ai_repo = ai_repo or AIAnalysisRepository(db)
        self.pattern_repo = ErrorPatternRepository(db)

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

    def _get_kubearchive(self):
        # type: () -> KubeArchiveClient
        if self.kubearchive is None:
            self.kubearchive = KubeArchiveClient(
                api_url=self.config.k8s.kubearchive_api_url,
                namespace=RELENG_NAMESPACE,
            )
        return self.kubearchive

    def _get_github(self):
        # type: () -> GitHubClient
        if self.github is None:
            self.github = GitHubClient(token=self.config.github_token)
        return self.github

    def _get_gitlab(self):
        # type: () -> GitLabClient
        if self.gitlab is None:
            self.gitlab = GitLabClient()
        return self.gitlab

    def _oc_get_json(self, resource, name, namespace=None):
        # type: (str, str, Optional[str]) -> Optional[Dict[str, Any]]
        ns = namespace or self.config.k8s.namespace
        try:
            result = subprocess.run(
                ['oc', 'get', resource, name, '-n', ns, '-o', 'json'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                universal_newlines=True, check=True, timeout=15
            )
            return json.loads(result.stdout)
        except (subprocess.CalledProcessError, json.JSONDecodeError, subprocess.TimeoutExpired):
            return None

    def _derive_branch(self, application):
        # type: (str) -> str
        """Derive the GitHub branch name from application name.

        acme-v2-0 -> acme-3.4
        """
        match = re.match(r'rhoai-v(\d+)-(\d+)', application)
        if match:
            return 'rhoai-{}.{}'.format(match.group(1), match.group(2))
        return 'main'

    def _derive_rpa_filename(self, release_plan):
        # type: (str) -> str
        """Derive the RPA filename from the ReleasePlan name.

        rhoai-onprem-v3-4-components-prod -> rhoai-onperm-v3-4-components-prod.yaml
        Note: The RPA files use 'onperm' (typo) not 'onprem'.
        """
        return '{}.yaml'.format(release_plan)

    def collect_context(self, release_name, namespace=None):
        # type: (str, Optional[str]) -> Dict[str, Any]
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
        release_cr = self._oc_get_json('release', release_name, ns)
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

                    # Get pod logs (step-validate contains the actual errors)
                    pod_name = '{}-pod'.format(taskrun_name)
                    for step in ['step-validate', 'step-report', 'step-detailed-report']:
                        logs = ka.get_pod_logs(pod_name, container=step, namespace=RELENG_NAMESPACE)
                        if logs:
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
            snapshot_cr = self._oc_get_json('snapshot', snapshot_name, ns)
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
                        if f['name'].endswith('.yaml') and 'rhoai' in f['name']:
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

        return context

    def build_analysis_prompt(self, context):
        # type: (Dict[str, Any]) -> Tuple[str, str]
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

        # Pipeline logs
        logs = context.get('logs', {})
        if logs:
            sections.append("\n## Pipeline Logs")
            for step_name, log_content in logs.items():
                sections.append("\n### {} logs".format(step_name))
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

        # Institutional memory: include known patterns for this failure type
        pattern_section = self._format_pattern_section(context)
        if pattern_section:
            sections.append(pattern_section)

        sections.append("\nUse the record_release_analysis tool. Remember:")
        sections.append("- State ONLY what you observe in the evidence")
        sections.append("- Quote exact error messages from logs")
        sections.append("- Cite the source for every claim (log line, RPA file, bundle file)")
        sections.append("- Identify the specific team that owns the fix")

        user_prompt = '\n'.join(sections)
        return (SYSTEM_PROMPT, user_prompt)

    def _format_pattern_section(self, context):
        # type: (Dict[str, Any]) -> str
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

    def parse_analysis_response(self, llm_response):
        # type: (Any) -> Dict[str, Any]
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

    def analyze_release(self, release_name, namespace=None, force=False):
        # type: (str, Optional[str], bool) -> Dict[str, Any]
        """Full analysis pipeline: collect context -> LLM -> parse -> save."""

        # Check for existing analysis
        if not force:
            existing = self._get_existing_analysis(release_name)
            if existing:
                logger.info("Release already analyzed (use --force to re-analyze)")
                return existing

        logger.info("Collecting release context...")
        context = self.collect_context(release_name, namespace)

        if not context.get('conditions'):
            raise ValueError("Could not fetch Release CR — no conditions found")

        # Check if release actually failed
        is_failed = any(
            c.get('type') in ('Released', 'ManagedPipelineProcessed', 'Validated')
            and c.get('status') == 'False'
            for c in context.get('conditions', [])
        )
        if not is_failed:
            logger.info("Release has not failed — nothing to analyze")
            return {'status': 'not_failed', 'release_name': release_name}

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

        cost_usd = (response.input_tokens * 0.000003) + (response.output_tokens * 0.000015)

        analysis_id = self.ai_repo.insert_release_analysis(
            release_name=release_name,
            model_used=self.llm.model_name(),
            langfuse_trace_id=getattr(trace, 'id', None) if trace else None,
            tokens_used=response.input_tokens + response.output_tokens,
            cost_usd=cost_usd,
            analysis_duration=duration,
            analysis_json=response.tool_calls,
            **analysis
        )

        pattern = self.pattern_repo.find_or_create('release', analysis['failure_category'])
        self.pattern_repo.record_occurrence(pattern['id'], analysis['confidence_score'])
        if analysis_id:
            self.pattern_repo.link_analysis(analysis_id, pattern['id'])

        self.langfuse.end_trace(trace, output=analysis)
        self.langfuse.flush()

        return analysis

    def _get_existing_analysis(self, release_name):
        # type: (str) -> Optional[Dict[str, Any]]
        return self.ai_repo.get_analysis_for_release(release_name)
