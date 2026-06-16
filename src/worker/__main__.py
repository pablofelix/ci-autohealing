"""Entry point for the worker process: python -m worker

Runs the CI pipeline (collect → enrich → analyze → fix → verify)
in a timed loop, replacing cron/collect-comprehensive.sh.
"""

import logging
import os
import signal
import sys

from config import CollectorConfig
from worker.pipeline import PipelineStep, WorkerPipeline
from worker.health import start_health_server


def _build_pipeline(config):
    collect_interval = int(os.environ.get('WORKER_COLLECT_INTERVAL', '1200'))
    analyze_interval = int(os.environ.get('WORKER_ANALYZE_INTERVAL', '1200'))
    jira_interval = int(os.environ.get('WORKER_JIRA_INTERVAL', '600'))

    pipeline = WorkerPipeline(check_interval=30)

    def step_collect():
        from collectors.build_failure_collector import BuildFailureCollector
        collector = BuildFailureCollector(config)
        return collector.run()

    def step_sync_status():
        from collectors.status_synchronizer import StatusSynchronizer
        sync = StatusSynchronizer(config)
        return sync.run()

    def step_verify_fixes():
        import fixers.verify_fixes as mod
        return mod.main()

    def step_check_conforma():
        import check_conforma_status as mod
        return mod.main()

    def step_collect_conforma():
        from collectors.conforma_violation_collector import ConformaViolationCollector
        collector = ConformaViolationCollector(config)
        return collector.run()

    def step_enrich_context():
        import collect_commit_context as mod
        return mod.main()

    def step_analyze():
        from services.batch_analysis_service import BatchAnalysisService
        service = BatchAnalysisService(config, all_apps=True)
        result = service.run_batch()
        return {
            'analyzed': result.total_analyzed,
            'pending': result.build_pending + result.conforma_pending,
        }

    def step_auto_fix():
        import fixers.auto_fix as mod
        return mod.main()

    def step_doc_context():
        import collect_doc_context as mod
        return mod.main()

    def step_jira_poll():
        import poll_jira_comments as mod
        return mod.main()

    pipeline.add_step(PipelineStep(
        'collect', step_collect, collect_interval, critical=True))
    pipeline.add_step(PipelineStep(
        'sync_status', step_sync_status, collect_interval, critical=True))
    pipeline.add_step(PipelineStep(
        'verify_fixes', step_verify_fixes, collect_interval,
        requires_env=['GITHUB_TOKEN']))
    pipeline.add_step(PipelineStep(
        'check_conforma', step_check_conforma, collect_interval))
    pipeline.add_step(PipelineStep(
        'collect_conforma', step_collect_conforma, collect_interval))
    pipeline.add_step(PipelineStep(
        'enrich_context', step_enrich_context, collect_interval,
        requires_env=['GITHUB_TOKEN']))
    pipeline.add_step(PipelineStep(
        'analyze', step_analyze, analyze_interval,
        requires_env=['LLM_PROVIDER']))
    pipeline.add_step(PipelineStep(
        'auto_fix', step_auto_fix, collect_interval,
        requires_env=['GITHUB_TOKEN', 'AUTONOMOUS_MODE']))

    def step_skill_execute():
        from skills.db_registry import get_registry
        from skills.executor import SkillExecutor
        registry = get_registry()
        skills = registry.list_skills(tag='auto-heal')
        if not skills:
            return {'executed': 0, 'skipped': 'no auto-heal skills'}
        results = []
        for skill in skills:
            if skill.status != 'active':
                continue
            executor = SkillExecutor(skill, triggered_by='worker')
            risk = executor.classify()
            if risk == 'high':
                continue
            result = executor.execute()
            try:
                registry.record_run(result)
            except Exception:
                pass
            results.append({'skill': skill.name, 'status': result.status})
        return {'executed': len(results), 'results': results}

    skill_interval = int(os.environ.get('WORKER_SKILL_INTERVAL', '3600'))
    pipeline.add_step(PipelineStep(
        'skill_execute', step_skill_execute, skill_interval,
        requires_env=['AUTONOMOUS_MODE']))
    pipeline.add_step(PipelineStep(
        'doc_context', step_doc_context, 3600))
    pipeline.add_step(PipelineStep(
        'jira_poll', step_jira_poll, jira_interval,
        requires_env=['JIRA_TOKEN', 'LLM_PROVIDER']))

    return pipeline


def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)-8s [%(name)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )
    logger = logging.getLogger('worker')

    config = CollectorConfig.from_env()
    pipeline = _build_pipeline(config)

    health_port = int(os.environ.get('WORKER_HEALTH_PORT', '8001'))
    start_health_server(pipeline, port=health_port)

    def shutdown(signum, frame):
        logger.info("Received %s, stopping pipeline...", signal.Signals(signum).name)
        pipeline.stop()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    logger.info("Worker starting (health: :%d)", health_port)
    try:
        pipeline.run()
    except Exception:
        logger.exception("Worker pipeline crashed")
        sys.exit(1)

    logger.info("Worker exited cleanly")


if __name__ == '__main__':
    main()
