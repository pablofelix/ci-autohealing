#!/usr/bin/env python3
"""Benchmark KubeArchive API vs K8s API para PipelineRuns y logs.

Compara:
- Velocidad de query de PipelineRuns
- Disponibilidad de logs
- Calidad de datos
"""

import time
import subprocess
import json

from config import CollectorConfig
from clients.kubearchive import KubeArchiveClient
from clients.kubernetes import KubernetesClient
from logger import setup_logger

logger = setup_logger(__name__)


def benchmark_pipelinerun_query(component_name, namespace):
    # type: (str, str) -> Dict[str, Any]
    """Compare query speed for a specific component."""
    label = f'appstudio.openshift.io/component={component_name}'

    # 1. KubeArchive API
    config = CollectorConfig.from_env()
    kubearchive = KubeArchiveClient(
        api_url=config.k8s.kubearchive_api_url,
        namespace=namespace
    )

    start = time.time()
    url = f"{kubearchive.api_url}/apis/tekton.dev/v1/namespaces/{namespace}/pipelineruns"
    params = {'labelSelector': label, 'limit': 50}

    try:
        resp = kubearchive.session.get(url, params=params, timeout=30)
        resp.raise_for_status()
        ka_data = resp.json()
        ka_count = len(ka_data.get('items', []))
        ka_time = time.time() - start
    except Exception as e:
        ka_count = 0
        ka_time = -1
        ka_data = {}
        logger.error("KubeArchive failed: %s", e)

    # 2. K8s API (via oc)
    start = time.time()
    try:
        result = subprocess.run(
            ['oc', 'get', 'pipelinerun', '-n', namespace,
             '-l', label, '-o', 'json'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=15
        )
        if result.returncode == 0:
            k8s_data = json.loads(result.stdout)
            k8s_count = len(k8s_data.get('items', []))
            k8s_time = time.time() - start
        else:
            k8s_count = 0
            k8s_time = -1
            k8s_data = {}
    except Exception as e:
        k8s_count = 0
        k8s_time = -1
        k8s_data = {}
        logger.error("K8s API failed: %s", e)

    return {
        'component': component_name,
        'kubearchive': {
            'count': ka_count,
            'time_ms': int(ka_time * 1000) if ka_time > 0 else -1,
            'has_data': ka_count > 0,
        },
        'k8s_api': {
            'count': k8s_count,
            'time_ms': int(k8s_time * 1000) if k8s_time > 0 else -1,
            'has_data': k8s_count > 0,
        },
        'winner': 'kubearchive' if ka_time < k8s_time and ka_time > 0 else 'k8s_api',
    }


def benchmark_logs_availability(pr_name, namespace):
    # type: (str, str) -> Dict[str, Any]
    """Compare log availability for a specific PipelineRun."""
    config = CollectorConfig.from_env()
    kubearchive = KubeArchiveClient(
        api_url=config.k8s.kubearchive_api_url,
        namespace=namespace
    )
    k8s = KubernetesClient(namespace=namespace)

    # Get PipelineRun to find TaskRuns
    pr_ka = kubearchive.get_pipelinerun(pr_name, namespace)
    k8s.get_pipelinerun(pr_name, namespace)

    # Find a TaskRun to test logs
    taskrun_name = None
    pod_name = None

    if pr_ka:
        for ref in pr_ka.get('status', {}).get('childReferences', []):
            if ref.get('kind') == 'TaskRun':
                taskrun_name = ref.get('name')
                tr = kubearchive.get_taskrun(taskrun_name, namespace)
                if tr:
                    pod_name = tr.get('status', {}).get('podName')
                    break

    if not pod_name:
        return {
            'pr_name': pr_name,
            'error': 'No pod found for testing',
        }

    # Test log fetching
    start = time.time()
    ka_logs = kubearchive.get_pod_logs(pod_name, namespace=namespace, tail_lines=100)
    ka_time = time.time() - start

    start = time.time()
    k8s_logs = k8s.get_pod_logs(pod_name, namespace=namespace, tail_lines=100)
    k8s_time = time.time() - start

    return {
        'pr_name': pr_name,
        'pod_name': pod_name,
        'kubearchive': {
            'has_logs': ka_logs is not None,
            'log_size': len(ka_logs) if ka_logs else 0,
            'time_ms': int(ka_time * 1000),
        },
        'k8s_api': {
            'has_logs': k8s_logs is not None,
            'log_size': len(k8s_logs) if k8s_logs else 0,
            'time_ms': int(k8s_time * 1000),
        },
        'winner': 'kubearchive' if ka_time < k8s_time and ka_logs else 'k8s_api',
    }


if __name__ == '__main__':
    import sys

    if len(sys.argv) < 2:
        print("Usage: python3 benchmark_api_speed.py <component-name> [pr-name]")
        print("\nExamples:")
        print("  python3 benchmark_api_speed.py odh-trustyai-nemo-guardrails-server-v3-4-ea-1")
        print("  python3 benchmark_api_speed.py odh-trustyai-nemo-guardrails-server-v3-4-ea-1 odh-trustyai-nemo-guardrails-server-v3-4-ea-1-on-push-nkwtw")
        sys.exit(1)

    component = sys.argv[1]
    pr_name = sys.argv[2] if len(sys.argv) > 2 else None

    config = CollectorConfig.from_env()
    namespace = config.k8s.namespace

    print("=" * 70)
    print("API Speed Benchmark: KubeArchive vs K8s API")
    print("=" * 70)
    print()

    # Benchmark 1: PipelineRun query
    print(f"[1] Querying PipelineRuns for component: {component}")
    print("-" * 70)
    result = benchmark_pipelinerun_query(component, namespace)

    print(f"Component: {result['component']}")
    print()
    print("KubeArchive:")
    print(f"  - Found: {result['kubearchive']['count']} PipelineRuns")
    print(f"  - Time: {result['kubearchive']['time_ms']} ms")
    print()
    print("K8s API (oc):")
    print(f"  - Found: {result['k8s_api']['count']} PipelineRuns")
    print(f"  - Time: {result['k8s_api']['time_ms']} ms")
    print()
    print(f"Winner: {result['winner']}")
    print()

    # Benchmark 2: Log availability
    if pr_name:
        print("=" * 70)
        print(f"[2] Testing log availability for: {pr_name}")
        print("-" * 70)
        log_result = benchmark_logs_availability(pr_name, namespace)

        if 'error' in log_result:
            print(f"Error: {log_result['error']}")
        else:
            print(f"PipelineRun: {log_result['pr_name']}")
            print(f"Pod: {log_result['pod_name']}")
            print()
            print("KubeArchive:")
            print(f"  - Has logs: {log_result['kubearchive']['has_logs']}")
            print(f"  - Size: {log_result['kubearchive']['log_size']} chars")
            print(f"  - Time: {log_result['kubearchive']['time_ms']} ms")
            print()
            print("K8s API (oc):")
            print(f"  - Has logs: {log_result['k8s_api']['has_logs']}")
            print(f"  - Size: {log_result['k8s_api']['log_size']} chars")
            print(f"  - Time: {log_result['k8s_api']['time_ms']} ms")
            print()
            print(f"Winner: {log_result['winner']}")

    print()
    print("=" * 70)
    print("Summary:")
    print("=" * 70)
    print("- KubeArchive: Historical data, may be faster for old PipelineRuns")
    print("- K8s API: Live data, may be faster for recent PipelineRuns")
    print("- Current system uses BOTH and deduplicates by UID")
    print()
