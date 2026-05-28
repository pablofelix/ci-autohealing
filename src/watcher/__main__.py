"""Entry point for the watch daemon: python -m watcher"""

import asyncio
import logging
import signal
import sys

from config import CollectorConfig
from watcher.daemon import WatchDaemon


def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)-8s [%(name)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )
    logger = logging.getLogger('watcher')

    config = CollectorConfig.from_env()

    if not config.watcher or not config.watcher.applications:
        logger.error(
            "No applications configured. Set WATCH_APPLICATIONS or APPLICATION_NAME in .env"
        )
        sys.exit(1)

    daemon = WatchDaemon(config)

    loop = asyncio.new_event_loop()

    def shutdown(sig):
        logger.info("Received %s, shutting down...", signal.Signals(sig).name)
        loop.create_task(daemon.stop())

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown, sig)

    try:
        loop.run_until_complete(daemon.start())
    except KeyboardInterrupt:
        loop.run_until_complete(daemon.stop())
    finally:
        loop.close()
        logger.info("Daemon exited.")


if __name__ == '__main__':
    main()
