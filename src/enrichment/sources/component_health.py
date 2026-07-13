"""Component health source - surfaces build success history.

Queries Tekton Results for all builds of a component to determine if it has
ever built successfully. Key signal for distinguishing structural failures
(component has never worked) from transient ones (component worked before).
"""

from enrichment.context_source import ContextSource
from logger import setup_logger

logger = setup_logger(__name__)


class ComponentHealthSource(ContextSource):
    """Surfaces build success history for a component."""

    def __init__(self, config):
        super().__init__(config)

    def source_name(self):
        return 'component_health'

    @property
    def requires_external_api(self):
        return True

    @property
    def timeout_seconds(self):
        return 15

    def fetch(self, failure):
        component = failure.get('component_name', '')
        application = failure.get('application', '') or self.config.k8s.application_name

        if not component:
            return None

        try:
            from clients.tekton_results import TektonResultsClient
            ns = self.config.k8s.namespace
            tr = TektonResultsClient(namespace=ns)
            history = tr.query_component_build_history(application, component, page_size=20)
        except Exception as e:
            logger.warning("Cannot query build history for %s: %s", component, e)
            return None

        has_ever_succeeded = False
        total_builds = 0
        successful_builds = 0
        failed_builds = 0
        consecutive_failures = 0
        consecutive_counted = False
        last_success_date = None
        last_success_sha = None

        for pr_data in history:
            conditions = pr_data.get('status', {}).get('conditions', [])
            if not conditions:
                continue

            last_cond = conditions[-1]
            succeeded = (
                last_cond.get('type') == 'Succeeded'
                and last_cond.get('status') == 'True'
            )

            total_builds += 1

            if succeeded:
                successful_builds += 1
                has_ever_succeeded = True
                consecutive_counted = True
                if last_success_date is None:
                    annotations = pr_data.get('metadata', {}).get('annotations', {})
                    last_success_date = last_cond.get('lastTransitionTime')
                    last_success_sha = (
                        annotations.get('build.appstudio.redhat.com/commit_sha')
                        or annotations.get('pipelinesascode.tekton.dev/sha', '')
                    )
            else:
                failed_builds += 1
                if not consecutive_counted:
                    consecutive_failures += 1

        logger.info(
            "Component %s health: %d builds, %d succeeded, ever_succeeded=%s",
            component, total_builds, successful_builds, has_ever_succeeded,
        )

        return {
            'component_health': {
                'has_ever_succeeded': has_ever_succeeded,
                'total_builds': total_builds,
                'successful_builds': successful_builds,
                'failed_builds': failed_builds,
                'consecutive_failures': consecutive_failures,
                'last_success_date': last_success_date,
                'last_success_sha': last_success_sha,
            }
        }
