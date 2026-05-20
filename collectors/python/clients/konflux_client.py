"""Konflux K8s REST API client for EnterpriseContractPolicy and ReleasePlanAdmission.

Queries Konflux CRDs directly from the cluster via the K8s API, using
Bearer token auth from `oc whoami -t`. Replaces GitLab file fetching
as the primary source for EC policy exception data.
"""

from datetime import datetime, timezone

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

GITLAB_EC_BASE = (
    'https://GITLAB_INTERNAL_HOST/releng/konflux-release-data/-/blob/main/'
    'config/CLUSTER_SHORT/product/EnterpriseContractPolicy'
)


class KonfluxClient:
    """Read-only client for Konflux CRDs via the K8s REST API."""

    def __init__(self, namespace='releng-tenant'):
        # type: (str) -> None
        self._server = discover_openshift_api_url()
        token = get_openshift_token()
        if not token:
            raise RuntimeError('Not logged in — run oc login first')
        self._session = create_authenticated_session(token)
        self._namespace = namespace

    def _get(self, path, params=None, namespace=None, api_group=None):
        # type: (str, Optional[Dict], Optional[str], Optional[str]) -> Optional[Dict]
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
        # type: (Optional[str]) -> List[Dict[str, Any]]
        data = self._get('enterprisecontractpolicies')
        if not data:
            return []
        items = data.get('items', [])
        if name_filter:
            items = [p for p in items
                     if name_filter in p.get('metadata', {}).get('name', '')]
        return items

    def get_ec_policy(self, name):
        # type: (str) -> Optional[Dict[str, Any]]
        return self._get('enterprisecontractpolicies/{}'.format(name))

    def get_release_plan_admissions(self, name_filter=None):
        # type: (Optional[str]) -> List[Dict[str, Any]]
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
        # type: (Dict[str, Any]) -> List[Dict[str, Any]]
        policy_name = policy.get('metadata', {}).get('name', 'unknown')
        gitlab_link = '{}/{}.yaml'.format(GITLAB_EC_BASE, policy_name)
        now = datetime.now(timezone.utc)
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
                        exp = exp.replace(tzinfo=timezone.utc)
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
        # type: (str, Optional[str]) -> List[Dict[str, Any]]
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
        # type: (Dict[str, Any]) -> Dict[str, Any]
        meta = scenario.get('metadata', {})
        spec = scenario.get('spec', {})
        name = meta.get('name', '')

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
            'contexts': context_names,
        }

    @staticmethod
    def extract_rpa_bindings(rpas, policy_names=None):
        # type: (List[Dict[str, Any]], Optional[set]) -> List[Dict[str, str]]
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
