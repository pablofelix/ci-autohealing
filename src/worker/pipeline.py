"""Worker pipeline — orchestrates CI pipeline steps in a timed loop.

Replaces cron/collect-comprehensive.sh with a long-running Python process.
Each step has its own interval, env gates, and failure handling.
"""

import os
import time

from logger import setup_logger

logger = setup_logger(__name__)


class PipelineStep:
    """A single step in the worker pipeline."""

    def __init__(self, name, run_fn, interval_seconds=1200,
                 requires_env=None, critical=False):
        self.name = name
        self.run_fn = run_fn
        self.interval_seconds = interval_seconds
        self.requires_env = requires_env or []
        self.critical = critical

        self.last_run = 0.0
        self.last_duration = 0.0
        self.last_error = None  # type: Optional[str]
        self.run_count = 0
        self.error_count = 0

    def is_due(self):
        return (time.time() - self.last_run) >= self.interval_seconds

    def env_satisfied(self):
        for var in self.requires_env:
            val = os.environ.get(var, '')
            if var == 'AUTONOMOUS_MODE':
                if val.lower() != 'true':
                    return False
            elif not val:
                return False
        return True

    def execute(self):
        start = time.time()
        try:
            result = self.run_fn()
            self.last_run = time.time()
            self.last_duration = self.last_run - start
            self.last_error = None
            self.run_count += 1
            return result
        except Exception as e:
            self.last_run = time.time()
            self.last_duration = self.last_run - start
            self.last_error = str(e)
            self.error_count += 1
            raise

    def status(self):
        return {
            'name': self.name,
            'last_run': self.last_run,
            'last_duration': round(self.last_duration, 1),
            'last_error': self.last_error,
            'run_count': self.run_count,
            'error_count': self.error_count,
            'interval_seconds': self.interval_seconds,
            'next_run_in': max(0, int(self.interval_seconds - (time.time() - self.last_run))),
            'env_satisfied': self.env_satisfied(),
        }


class WorkerPipeline:
    """Runs pipeline steps in a loop with configurable intervals."""

    def __init__(self, check_interval=30):
        self.steps = []  # type: List[PipelineStep]
        self.check_interval = check_interval
        self.running = False
        self._started_at = 0.0

    def add_step(self, step):
        self.steps.append(step)

    def stop(self):
        self.running = False

    def status(self):
        return {
            'running': self.running,
            'uptime_seconds': int(time.time() - self._started_at) if self._started_at else 0,
            'steps': [s.status() for s in self.steps],
        }

    def run(self):
        self.running = True
        self._started_at = time.time()
        logger.info("Worker pipeline started with %d steps", len(self.steps))

        for step in self.steps:
            if step.env_satisfied():
                logger.info("  [%s] interval=%ds", step.name, step.interval_seconds)
            else:
                missing = [v for v in step.requires_env if not os.environ.get(v)]
                logger.info("  [%s] SKIPPED (missing: %s)", step.name, ', '.join(missing))

        while self.running:
            for step in self.steps:
                if not self.running:
                    break
                if not step.is_due():
                    continue
                if not step.env_satisfied():
                    continue

                logger.info("[%s] Starting...", step.name)
                try:
                    result = step.execute()
                    logger.info("[%s] Completed in %.1fs", step.name, step.last_duration)
                    if isinstance(result, dict):
                        for k, v in result.items():
                            if isinstance(v, (int, float)):
                                logger.info("[%s]   %s: %s", step.name, k, v)
                except Exception as e:
                    logger.error("[%s] Failed after %.1fs: %s",
                                 step.name, step.last_duration, e)
                    if step.critical:
                        logger.error("[%s] Critical step failed, stopping pipeline", step.name)
                        self.running = False
                        raise

            if self.running:
                time.sleep(self.check_interval)

        logger.info("Worker pipeline stopped")
