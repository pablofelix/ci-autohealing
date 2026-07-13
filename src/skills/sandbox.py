"""Container sandbox for high-risk skill execution via K8s Jobs."""

import logging
import os
import time
from datetime import UTC, datetime

from skills.models import ExecutionResult

logger = logging.getLogger(__name__)

_DEFAULT_IMAGE = os.environ.get(
    'IC_SANDBOX_IMAGE',
    'quay.io/pestevez_rhoai-dev/ci-autohealing:latest',
)
_DEFAULT_NAMESPACE = os.environ.get(
    'IC_SANDBOX_NAMESPACE',
    'rhai-devtestops--ci-autohealing',
)


class ContainerSandbox:
    """Run skill code blocks in an ephemeral K8s Job."""

    def __init__(self, namespace=None, image=None, timeout=300):
        self.namespace = namespace or _DEFAULT_NAMESPACE
        self.image = image or _DEFAULT_IMAGE
        self.timeout = timeout

    def _job_name(self, skill_name):
        safe = skill_name.replace('/', '-').replace('_', '-')[:40]
        ts = int(time.time()) % 100000
        return 'ic-skill-{}-{}'.format(safe, ts)

    def run(self, skill, code_blocks, params=None, triggered_by='cli'):
        started = datetime.now(UTC).isoformat()
        job_name = self._job_name(skill.name)

        script = '#!/bin/bash\nset -euo pipefail\n\n'
        for i, block in enumerate(code_blocks):
            script += '# --- step {} ---\n'.format(i + 1)
            if block.get('lang') in ('python', 'python3'):
                escaped = block['code'].replace("'", "'\\''")
                script += "python3 -c '{}'\n\n".format(escaped)
            else:
                script += block['code'] + '\n\n'

        try:
            from kubernetes import client, config
        except ImportError:
            return ExecutionResult(
                skill_name=skill.qualified_name,
                status='failed',
                stderr='kubernetes package not installed (run pip install ic-tool[full])',
                risk_level='high',
                started_at=started,
                triggered_by=triggered_by,
            )

        try:
            config.load_incluster_config()
        except config.ConfigException:
            try:
                config.load_kube_config()
            except Exception:
                return ExecutionResult(
                    skill_name=skill.qualified_name,
                    status='failed',
                    stderr='No kubeconfig available for sandbox execution',
                    risk_level='high',
                    started_at=started,
                    triggered_by=triggered_by,
                )

        batch_v1 = client.BatchV1Api()
        core_v1 = client.CoreV1Api()

        env_list = [client.V1EnvVar(name=k, value=str(v))
                    for k, v in (params or {}).items()]

        job_spec = client.V1Job(
            metadata=client.V1ObjectMeta(
                name=job_name,
                namespace=self.namespace,
                labels={
                    'app.kubernetes.io/name': 'skill-sandbox',
                    'app.kubernetes.io/part-of': 'ci-autohealing',
                    'ic/skill': skill.name[:63],
                },
            ),
            spec=client.V1JobSpec(
                backoff_limit=0,
                active_deadline_seconds=self.timeout,
                ttl_seconds_after_finished=300,
                template=client.V1PodTemplateSpec(
                    spec=client.V1PodSpec(
                        restart_policy='Never',
                        security_context=client.V1PodSecurityContext(
                            run_as_non_root=True,
                        ),
                        containers=[
                            client.V1Container(
                                name='skill',
                                image=self.image,
                                command=['bash', '-c', script],
                                env=env_list,
                                resources=client.V1ResourceRequirements(
                                    requests={'memory': '128Mi', 'cpu': '100m'},
                                    limits={'memory': '512Mi', 'cpu': '500m'},
                                ),
                                security_context=client.V1SecurityContext(
                                    allow_privilege_escalation=False,
                                ),
                            ),
                        ],
                    ),
                ),
            ),
        )

        logger.info("Creating sandbox Job %s in %s", job_name, self.namespace)

        try:
            batch_v1.create_namespaced_job(self.namespace, job_spec)
        except Exception as e:
            return ExecutionResult(
                skill_name=skill.qualified_name,
                status='failed',
                stderr='Failed to create sandbox Job: {}'.format(e),
                risk_level='high',
                started_at=started,
                triggered_by=triggered_by,
            )

        t0 = time.time()
        status = 'failed'
        exit_code = -1
        stdout = ''
        stderr = ''

        try:
            while time.time() - t0 < self.timeout + 30:
                job = batch_v1.read_namespaced_job_status(job_name, self.namespace)
                if job.status.succeeded:
                    status = 'success'
                    exit_code = 0
                    break
                if job.status.failed:
                    status = 'failed'
                    exit_code = 1
                    break
                time.sleep(5)

            pods = core_v1.list_namespaced_pod(
                self.namespace,
                label_selector='job-name={}'.format(job_name),
            )
            if pods.items:
                pod_name = pods.items[0].metadata.name
                try:
                    stdout = core_v1.read_namespaced_pod_log(
                        pod_name, self.namespace,
                        container='skill',
                    )
                except Exception as e:
                    stderr = 'Failed to read pod logs: {}'.format(e)

        except Exception as e:
            stderr = 'Error waiting for sandbox Job: {}'.format(e)

        finally:
            try:
                batch_v1.delete_namespaced_job(
                    job_name, self.namespace,
                    propagation_policy='Background',
                )
                logger.info("Deleted sandbox Job %s", job_name)
            except Exception:
                logger.warning("Failed to delete sandbox Job %s", job_name)

        return ExecutionResult(
            skill_name=skill.qualified_name,
            status=status,
            exit_code=exit_code,
            stdout=stdout[:50000],
            stderr=stderr[:10000],
            duration_seconds=time.time() - t0,
            risk_level='high',
            started_at=started,
            steps_executed=len(code_blocks) if status == 'success' else 0,
            steps_total=len(code_blocks),
            triggered_by=triggered_by,
        )
