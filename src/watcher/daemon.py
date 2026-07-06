"""Watch daemon — event-driven CI monitoring via K8s Watch API.

Replaces cron-based polling (steps 1-5, 9) with real-time event streams.
Watches PipelineRuns and Components per application, with a periodic
reconciliation loop as safety net.
"""

import asyncio
import logging

from kubernetes import client, watch

from openshift_auth import _ensure_k8s_config

logger = logging.getLogger(__name__)

PIPELINERUN_GROUP = 'tekton.dev'
PIPELINERUN_VERSION = 'v1'
PIPELINERUN_PLURAL = 'pipelineruns'

COMPONENT_GROUP = 'appstudio.redhat.com'
COMPONENT_VERSION = 'v1alpha1'
COMPONENT_PLURAL = 'components'


class WatchDaemon:
    """Orchestrates K8s watch streams across multiple applications."""

    def __init__(self, config, db_pool=None, handler_factory=None):
        self.config = config
        self.watcher_config = config.watcher
        self.namespace = config.k8s.namespace
        self.db_pool = db_pool
        self.handler_factory = handler_factory or self._default_handler_factory
        self._tasks = []
        self._running = False
        self._resource_versions = {}

    async def start(self):
        self._running = True
        wc = self.watcher_config
        apps = wc.applications

        if not apps:
            logger.error("No applications configured for watching. "
                         "Set WATCH_APPLICATIONS or APPLICATION_NAME.")
            return

        if wc.disabled:
            logger.info("Disabled watchers: %s", ', '.join(sorted(wc.disabled)))

        logger.info("Starting watch daemon for %d application(s): %s",
                     len(apps), ', '.join(apps))

        for app in apps:
            build_handler, component_handler = self.handler_factory(app)

            if wc.is_enabled('builds'):
                build_selector = (
                    'appstudio.openshift.io/application={},'
                    'pipelines.appstudio.openshift.io/type=build'
                ).format(app)
                self._tasks.append(asyncio.create_task(
                    self._run_watch(app, 'build-pipelineruns',
                                    PIPELINERUN_GROUP, PIPELINERUN_VERSION,
                                    PIPELINERUN_PLURAL, build_selector,
                                    build_handler),
                    name='watch-build-{}'.format(app),
                ))

            if wc.is_enabled('tests'):
                test_selector = (
                    'appstudio.openshift.io/application={},'
                    'pipelines.appstudio.openshift.io/type=test'
                ).format(app)
                self._tasks.append(asyncio.create_task(
                    self._run_watch(app, 'test-pipelineruns',
                                    PIPELINERUN_GROUP, PIPELINERUN_VERSION,
                                    PIPELINERUN_PLURAL, test_selector,
                                    build_handler),
                    name='watch-test-{}'.format(app),
                ))

            if wc.is_enabled('components'):
                component_selector = 'appstudio.openshift.io/application={}'.format(app)
                self._tasks.append(asyncio.create_task(
                    self._run_watch(app, 'components',
                                    COMPONENT_GROUP, COMPONENT_VERSION,
                                    COMPONENT_PLURAL, component_selector,
                                    component_handler),
                    name='watch-components-{}'.format(app),
                ))

        if wc.is_enabled('jira'):
            self._tasks.append(asyncio.create_task(
                self._jira_poll_loop(),
                name='jira-poller',
            ))

        self._tasks.append(asyncio.create_task(
            self._reconciliation_loop(),
            name='reconciliation',
        ))

        logger.info("All watch streams started. %d tasks running.", len(self._tasks))

        try:
            await asyncio.gather(*self._tasks)
        except asyncio.CancelledError:
            logger.info("Daemon tasks cancelled.")

    async def stop(self):
        self._running = False
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        logger.info("Watch daemon stopped.")

    async def _run_watch(self, app, resource_key, group, version, plural,
                         label_selector, handler):
        rv_key = '{}/{}'.format(app, resource_key)
        consecutive_failures = 0
        max_retries = self.watcher_config.max_retries

        while self._running:
            try:
                resource_version = self._resource_versions.get(rv_key)
                await asyncio.to_thread(
                    self._watch_stream,
                    group, version, plural, label_selector,
                    handler, rv_key, resource_version,
                )
                consecutive_failures = 0
            except _GoneError:
                logger.info("[%s] Watch %s got 410 Gone, relisting.", app, resource_key)
                self._resource_versions.pop(rv_key, None)
                consecutive_failures = 0
            except Exception as e:
                consecutive_failures += 1
                if consecutive_failures >= max_retries:
                    logger.warning(
                        "[%s] Watch %s failed %d times, falling back to poll.",
                        app, resource_key, consecutive_failures,
                    )
                    await self._fallback_poll(app)
                    consecutive_failures = 0
                    self._resource_versions.pop(rv_key, None)
                else:
                    backoff = min(2 ** consecutive_failures, 60)
                    logger.warning(
                        "[%s] Watch %s error (attempt %d/%d), retrying in %ds: %s",
                        app, resource_key, consecutive_failures, max_retries,
                        backoff, e,
                    )
                    await asyncio.sleep(backoff)

    def _watch_stream(self, group, version, plural, label_selector,
                      handler, rv_key, resource_version):
        _ensure_k8s_config()
        api = client.CustomObjectsApi()
        w = watch.Watch()

        kwargs = {
            'group': group,
            'version': version,
            'namespace': self.namespace,
            'plural': plural,
            'label_selector': label_selector,
            'timeout_seconds': 300,
        }
        if resource_version:
            kwargs['resource_version'] = resource_version

        try:
            for event in w.stream(api.list_namespaced_custom_object, **kwargs):
                if not self._running:
                    w.stop()
                    return

                obj = event.get('object', {})
                new_rv = obj.get('metadata', {}).get('resourceVersion')
                if new_rv:
                    self._resource_versions[rv_key] = new_rv

                raw_type = event.get('type', '')
                if raw_type == 'ERROR':
                    code = obj.get('code', 0)
                    if code == 410:
                        raise _GoneError()
                    raise RuntimeError("Watch error: {}".format(obj.get('message', raw_type)))

                try:
                    handler.handle(event)
                except Exception as e:
                    name = obj.get('metadata', {}).get('name', '?')
                    logger.error("Handler error for %s: %s", name, e, exc_info=True)

        except _GoneError:
            raise
        finally:
            w.stop()

    async def _fallback_poll(self, app):
        """Full poll for one application — mirrors cron steps 1-2."""
        logger.info("[%s] Running fallback full poll...", app)
        try:
            from dataclasses import replace

            from collectors.status_synchronizer import get_failing_build_components
            app_config = replace(self.config,
                                 k8s=replace(self.config.k8s, application_name=app))
            await asyncio.to_thread(get_failing_build_components, app_config)
            logger.info("[%s] Fallback poll complete.", app)
        except Exception as e:
            logger.error("[%s] Fallback poll failed: %s", app, e)

    async def _jira_poll_loop(self):
        interval = self.watcher_config.jira_poll_interval
        while self._running:
            try:
                await asyncio.to_thread(self._run_jira_poll)
            except Exception as e:
                logger.error("Jira poll failed: %s", e)
            if not self._running:
                break
            await asyncio.sleep(interval)

    def _run_jira_poll(self):
        if not self.config.jira or not self.config.llm:
            logger.debug("Jira or LLM not configured, skipping Jira poll.")
            return
        from poll_jira_comments import JiraCommentPoller
        poller = JiraCommentPoller(self.config)
        stats = poller.run()
        logger.info("Jira poll: %d tickets, %d new comments, %d drafted",
                     stats['tickets'], stats['new_comments'], stats['drafted'])

    async def _reconciliation_loop(self):
        interval = self.watcher_config.reconcile_interval
        logger.info("Running initial reconciliation to catch missed events...")
        for app in self.watcher_config.applications:
            await self._fallback_poll(app)
        while self._running:
            await asyncio.sleep(interval)
            if not self._running:
                break
            logger.info("Running reconciliation across all applications...")
            for app in self.watcher_config.applications:
                await self._fallback_poll(app)

    def _default_handler_factory(self, app):
        from repositories import BuildFailureRepository, DatabaseConnection
        from watcher.handlers import BuildEventHandler, ComponentEventHandler

        db = self.db_pool or DatabaseConnection(self.config.db.connection_string)
        build_repo = BuildFailureRepository(db)

        pipeline_client = None
        try:
            from clients import UnifiedPipelineClient
            pipeline_client = UnifiedPipelineClient(self.namespace)
        except Exception:
            logger.warning("[%s] Could not create pipeline client for log fetching.", app)

        analyzer = None
        if self.watcher_config.auto_analyze and self.config.llm:
            try:
                from analyzers.build_failure_analyzer import BuildFailureAnalyzer
                analyzer = BuildFailureAnalyzer(self.config)
            except Exception:
                logger.warning("[%s] Could not create analyzer.", app)

        build_handler = BuildEventHandler(
            application=app,
            namespace=self.namespace,
            build_repo=build_repo,
            pipeline_client=pipeline_client,
            analyzer=analyzer,
            auto_analyze=self.watcher_config.auto_analyze,
        )

        component_handler = ComponentEventHandler(
            application=app,
            namespace=self.namespace,
        )

        return build_handler, component_handler


class _GoneError(Exception):
    """Raised when a watch stream receives a 410 Gone response."""
