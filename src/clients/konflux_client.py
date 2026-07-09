"""Konflux K8s REST API client for EnterpriseContractPolicy and ReleasePlanAdmission.

Queries Konflux CRDs directly from the cluster via the K8s API, using
Bearer token auth from `oc whoami -t`. Replaces GitLab file fetching
as the primary source for EC policy exception data.
"""

import os
from datetime import UTC, datetime, timedelta

import requests

from logger import setup_logger
from openshift_auth import (
    create_authenticated_session,
    discover_openshift_api_url,
    get_openshift_token,
)

logger = setup_logger(__name__)

API_GROUP = 'apis/appstudio.redhat.com/v1alpha1'
ITS_API_GROUP = 'apis/appstudio.redhat.com/v1beta2'

GITLAB_EC_BASE = os.environ.get('GITLAB_EC_POLICY_URL', '')

# ── Snapshot trigger-type detection ──────────────────────────────────────────
# Pipelines-as-Code labels that identify the pipeline template a build used.
# Update here if Konflux changes the label name or pipeline-name conventions.
#
# The label value is the PipelineRun template name, e.g.:
#   rhoai-fbc-fragment-v3-5-on-schedule  → scheduled (nightly, FIPS runs)
#   rhoai-fbc-fragment-v3-5-on-push      → push (no FIPS)
#
# See: docs/adr/007-its-scoping-false-positives.md
_PAC_ORIGINAL_PRNAME_LABEL = 'pac.test.appstudio.openshift.io/original-prname'
_SCHEDULE_SUFFIX = '-on-schedule'
_PUSH_SUFFIX = '-on-push'


class KonfluxClient:
    """Read-only client for Konflux CRDs via the K8s REST API."""

    def __init__(self, namespace=None):
        self._server = discover_openshift_api_url()
        token = get_openshift_token()
        if not token:
            raise RuntimeError('Not logged in — run oc login first')
        self._session = create_authenticated_session(token)
        self._namespace = namespace

    def _get(self, path, params=None, namespace=None, api_group=None):
        ns = namespace or self._namespace
        group = api_group or API_GROUP
        url = '{}/{}/namespaces/{}/{}'.format(
            self._server, group, ns, path
        )
        try:
            resp = self._session.get(url, params=params, timeout=15, verify=True)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.warning("Konflux API %s: %s", path, str(e)[:200])
            return None

    def get_ec_policies(self, name_filter=None):
        data = self._get('enterprisecontractpolicies')
        if not data:
            return []
        items = data.get('items', [])
        if name_filter:
            items = [p for p in items
                     if name_filter in p.get('metadata', {}).get('name', '')]
        return items

    def get_ec_policy(self, name):
        return self._get('enterprisecontractpolicies/{}'.format(name))

    def get_release_plan_admissions(self, name_filter=None):
        data = self._get('releaseplanadmissions')
        if not data:
            return []
        items = data.get('items', [])
        if name_filter:
            items = [r for r in items
                     if name_filter in r.get('metadata', {}).get('name', '')]
        return items

    @staticmethod
    def extract_exceptions(policy):
        policy_name = policy.get('metadata', {}).get('name', 'unknown')
        gitlab_link = '{}/{}.yaml'.format(GITLAB_EC_BASE, policy_name)
        now = datetime.now(UTC)
        results = []
        for src in policy.get('spec', {}).get('sources', []):
            for exc_value in src.get('config', {}).get('exclude', []):
                results.append({
                    'value': exc_value,
                    'effectiveUntil': None,
                    'reference': 'config.exclude (permanent)',
                    'imageUrl': '',
                    'source_policy': policy_name,
                    'gitlab_link': gitlab_link,
                    'days_left': None,
                    'permanent': True,
                })
            for exc in src.get('volatileConfig', {}).get('exclude', []):
                eu = exc.get('effectiveUntil')
                days_left = None
                if eu:
                    try:
                        exp = datetime.strptime(str(eu)[:19], '%Y-%m-%dT%H:%M:%S')
                        exp = exp.replace(tzinfo=UTC)
                        days_left = (exp - now).days
                    except ValueError:
                        pass
                results.append({
                    'value': exc.get('value', ''),
                    'effectiveUntil': str(eu) if eu else None,
                    'reference': exc.get('reference', ''),
                    'imageUrl': exc.get('imageUrl', ''),
                    'source_policy': policy_name,
                    'gitlab_link': gitlab_link,
                    'days_left': days_left,
                    'permanent': False,
                })
        return results

    def get_integration_test_scenarios(self, namespace, app_filter=None):
        data = self._get(
            'integrationtestscenarios',
            namespace=namespace,
            api_group=ITS_API_GROUP,
        )
        if not data:
            return []
        items = data.get('items', [])
        if app_filter:
            items = [s for s in items
                     if app_filter == s.get('spec', {}).get('application', '')]
        return items

    @staticmethod
    def extract_its_metadata(scenario):
        meta = scenario.get('metadata', {})
        spec = scenario.get('spec', {})
        name = meta.get('name', '')

        labels = meta.get('labels', {})
        is_optional = labels.get('test.appstudio.openshift.io/optional', 'false').lower() == 'true'

        contexts = spec.get('contexts', [])
        context_names = [c.get('name', '') for c in contexts]
        is_disabled = 'disabled' in context_names

        params = spec.get('params', [])
        policy_ref = ''
        for p in params:
            if p.get('name') == 'POLICY_CONFIGURATION':
                policy_ref = p.get('value', '')
                break

        pipeline = spec.get('resolverRef', {}).get('params', [])
        pipeline_url = ''
        pipeline_path = ''
        for p in pipeline:
            if p.get('name') == 'url':
                pipeline_url = p.get('value', '')
            elif p.get('name') == 'pathInRepo':
                pipeline_path = p.get('value', '')

        is_conforma = (
            'conforma' in name
            or 'enterprise-contract' in pipeline_path
        )

        return {
            'name': name,
            'application': spec.get('application', ''),
            'policy_ref': policy_ref,
            'pipeline_url': pipeline_url,
            'pipeline_path': pipeline_path,
            'is_disabled': is_disabled,
            'is_future': '-future' in name,
            'is_conforma': is_conforma,
            'is_optional': is_optional,
            'contexts': context_names,
        }

    def get_snapshot(self, snapshot_name, namespace=None):
        """Fetch a single Snapshot CR by name.

        Returns the raw snapshot dict, or None if not found / unreachable.
        Used by the conforma collector to determine trigger_type at ingest time.
        """
        try:
            data = self._get(
                'snapshots/{}'.format(snapshot_name),
                namespace=namespace or self.namespace,
            )
            return data if data and 'metadata' in data else None
        except Exception:
            return None

    @staticmethod
    def extract_trigger_type(snapshot):
        """Determine the build trigger type from a Snapshot CR.

        Reads the Pipelines-as-Code ``original-prname`` label, which records
        which pipeline template fired the build:
          - ``*-on-schedule`` → 'scheduled'  (nightly; FIPS check runs)
          - ``*-on-push``     → 'push'        (regular commit; FIPS skipped)
          - anything else     → 'other'

        For FBC fragment components, IC prefers 'scheduled' conforma results
        when displaying violations, because only the scheduled pipeline runs the
        FIPS check — the release-authoritative result.

        >>> KonfluxClient.extract_trigger_type({'metadata': {'labels': {
        ...     'pac.test.appstudio.openshift.io/original-prname':
        ...         'rhoai-fbc-fragment-v3-5-on-schedule'}}})
        'scheduled'
        >>> KonfluxClient.extract_trigger_type({'metadata': {'labels': {
        ...     'pac.test.appstudio.openshift.io/original-prname':
        ...         'rhoai-fbc-fragment-v3-5-on-push'}}})
        'push'
        >>> KonfluxClient.extract_trigger_type({})
        'push'
        """
        if not snapshot:
            return 'push'
        labels = snapshot.get('metadata', {}).get('labels', {})
        original_prname = labels.get(_PAC_ORIGINAL_PRNAME_LABEL, '')
        if original_prname.endswith(_SCHEDULE_SUFFIX):
            return 'scheduled'
        if original_prname.endswith(_PUSH_SUFFIX):
            return 'push'
        # Unknown suffix — fall back to 'push' so we never incorrectly prefer
        # an unrecognised trigger over a known-good scheduled result.
        return 'other' if original_prname else 'push'

    def get_snapshots(self, app_filter=None, limit=5):
        params = {}
        if app_filter:
            params['labelSelector'] = 'appstudio.openshift.io/application={}'.format(app_filter)
        data = self._get('snapshots', params=params)
        if not data:
            return []
        items = data.get('items', [])
        items.sort(key=lambda s: s.get('metadata', {}).get('creationTimestamp', ''), reverse=True)
        return items[:limit]

    @staticmethod
    def extract_snapshot_status(snapshot):
        meta = snapshot.get('metadata', {})
        labels = meta.get('labels', {})
        spec = snapshot.get('spec', {})
        conditions = snapshot.get('status', {}).get('conditions', [])
        components = spec.get('components', [])

        event_type = labels.get('test.appstudio.openshift.io/type', 'push')
        is_override = event_type == 'override'

        test_results = {}
        warnings = []
        latest_transition = ''
        for cond in conditions:
            ctype = cond.get('type', '')
            if ctype.startswith('AppStudioTestSucceeded') or 'Test' in ctype:
                status = cond.get('status', 'Unknown')
                message = cond.get('message', '')[:300]
                test_results[cond.get('reason', ctype)] = {
                    'status': status,
                    'message': message,
                }
                if 'warning' in message.lower():
                    warnings.append(cond.get('reason', ctype))
            transition = cond.get('lastTransitionTime', '')
            if transition > latest_transition:
                latest_transition = transition

        return {
            'name': meta.get('name', ''),
            'application': spec.get('application', ''),
            'created': meta.get('creationTimestamp', ''),
            'event_type': event_type,
            'is_override': is_override,
            'component_count': len(components),
            'components': [c.get('name', '') for c in components],
            'test_results': test_results,
            'warnings': warnings,
            'latest_transition': latest_transition,
        }

    def get_releases(self, app_filter=None, limit=5):
        params = {}
        if app_filter:
            params['labelSelector'] = 'appstudio.openshift.io/application={}'.format(app_filter)
        data = self._get('releases', params=params)
        if not data:
            return []
        items = data.get('items', [])
        items.sort(key=lambda r: r.get('metadata', {}).get('creationTimestamp', ''), reverse=True)
        return items[:limit]

    @staticmethod
    def extract_release_status(release):
        meta = release.get('metadata', {})
        spec = release.get('spec', {})
        status = release.get('status', {})
        conditions = status.get('conditions', [])
        last_cond = conditions[-1] if conditions else {}
        processing = status.get('managedProcessing', {})
        return {
            'name': meta.get('name', ''),
            'snapshot': spec.get('snapshot', ''),
            'release_plan': spec.get('releasePlan', ''),
            'created': meta.get('creationTimestamp', ''),
            'phase': last_cond.get('type', 'Unknown'),
            'status': last_cond.get('status', 'Unknown'),
            'message': last_cond.get('message', '')[:300],
            'pipeline_run': processing.get('pipelineRun', ''),
            'start_time': processing.get('startTime', ''),
            'completion_time': processing.get('completionTime', ''),
            'automated': status.get('automated', False),
            'post_validation_failed': status.get('validation', {}).get('failedPostValidation', False),
        }

    @staticmethod
    def extract_rpa_bindings(rpas, policy_names=None):
        results = []
        for rpa in rpas:
            meta = rpa.get('metadata', {})
            spec = rpa.get('spec', {})
            policy = spec.get('policy', '')
            if policy_names and policy not in policy_names:
                continue
            apps = spec.get('applications', [])
            app = ', '.join(apps) if apps else spec.get('application', '')
            target = 'prod' if 'prod' in meta.get('name', '') else 'stage'
            results.append({
                'rpa_name': meta.get('name', ''),
                'application': app,
                'policy': policy,
                'target': target,
            })
        return results

    def get_dependency_updates(self, component_filter=None, hours=48):
        data = self._get('dependencyupdatechecks')
        if not data:
            return []
        items = data.get('items', [])
        now = datetime.now(UTC)
        cutoff = now - timedelta(hours=hours)
        results = []
        for item in items:
            meta = item.get('metadata', {})
            ts = meta.get('creationTimestamp', '')
            if ts:
                try:
                    created = datetime.strptime(ts[:19], '%Y-%m-%dT%H:%M:%S')
                    created = created.replace(tzinfo=UTC)
                    if created < cutoff:
                        continue
                except ValueError:
                    pass
            update = self.extract_dependency_update(item)
            if component_filter and update.get('component') != component_filter:
                continue
            results.append(update)
        results.sort(key=lambda u: u.get('created', ''), reverse=True)
        return results

    @staticmethod
    def extract_dependency_update(duc):
        meta = duc.get('metadata', {})
        spec = duc.get('spec', {})
        status = duc.get('status', {})
        labels = meta.get('labels', {})
        return {
            'name': meta.get('name', ''),
            'component': labels.get('appstudio.openshift.io/component', ''),
            'package': spec.get('packageName', ''),
            'from_version': spec.get('currentVersion', ''),
            'to_version': spec.get('newVersion', ''),
            'pr_url': status.get('pullRequestUrl', ''),
            'merged': status.get('merged', False),
            'created': meta.get('creationTimestamp', ''),
        }
