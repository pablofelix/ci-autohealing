#!/usr/bin/env python3
"""Fetch logs from archived PipelineRuns using KubeArchive API.

This script specifically targets PipelineRuns that:
- Are stored in the database
- Don't have logs yet
- Have been archived (no longer in active Kubernetes cluster)

Usage:
    python3 fetch_archived_logs.py [--limit N]
"""

import sys
import time
from typing import List, Tuple

from config import CollectorConfig
from database import Database
from kubearchive_client import KubeArchiveClient


class ArchivedLogsCollector:
    """Fetches logs from archived PipelineRuns via KubeArchive."""

    def __init__(self, config: CollectorConfig):
        """Initialize collector.

        Args:
            config: Collector configuration.
        """
        self.config = config
        self.db = Database(config.db)
        self.kubearchive = KubeArchiveClient(
            api_url=config.k8s.kubearchive_api_url,
            namespace=config.k8s.namespace
        )

    def fetch_and_save_logs(self, pr_name: str) -> bool:
        """Fetch logs for a PipelineRun and save to database.

        Args:
            pr_name: PipelineRun name.

        Returns:
            True if logs were fetched and saved, False otherwise.
        """
        print(f"  Fetching logs for {pr_name}...", end=' ', flush=True)

        # Fetch logs from KubeArchive
        logs = self.kubearchive.get_pipelinerun_logs(
            pr_name,
            namespace=self.config.k8s.namespace,
            max_log_size=100000  # 100KB limit
        )

        if not logs:
            print("✗ Not available")
            return False

        # Save to database
        success = self.db.update_pipelinerun_logs(pr_name, logs)
        if success:
            print(f"✓ Saved ({len(logs)} chars)")
        else:
            print("✗ DB update failed")

        return success

    def run(self, limit: int = 10) -> Tuple[int, int]:
        """Fetch logs for all PipelineRuns without logs.

        Args:
            limit: Maximum number of PipelineRuns to process.

        Returns:
            Tuple of (total_processed, successful).
        """
        print("========================================")
        print("Fetching Archived Logs via KubeArchive")
        print("========================================")
        print()

        # Get PipelineRuns without logs
        prs_without_logs = self.db.get_pipelineruns_without_logs(limit)

        if not prs_without_logs:
            print("No PipelineRuns without logs found")
            return 0, 0

        print(f"Found {len(prs_without_logs)} PipelineRuns without logs")
        print()

        # Process each PipelineRun
        successful = 0
        for i, (pr_name, pr_uid) in enumerate(prs_without_logs, 1):
            print(f"[{i}/{len(prs_without_logs)}]", end=' ')
            if self.fetch_and_save_logs(pr_name):
                successful += 1

        print()
        print("========================================")
        print("Complete")
        print("========================================")
        print(f"Processed: {len(prs_without_logs)}")
        print(f"Successful: {successful}")
        print()

        return len(prs_without_logs), successful


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description='Fetch logs from archived PipelineRuns'
    )
    parser.add_argument(
        '--limit',
        type=int,
        default=10,
        help='Maximum number of PipelineRuns to process (default: 10)'
    )
    args = parser.parse_args()

    # Load configuration
    config = CollectorConfig.from_env()

    # Run collector
    collector = ArchivedLogsCollector(config)
    total, successful = collector.run(limit=args.limit)

    return 0 if successful > 0 else 1


if __name__ == '__main__':
    sys.exit(main())
