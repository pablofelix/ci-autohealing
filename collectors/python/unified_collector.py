"""Backward-compatible re-export. Use clients.unified instead."""
from clients.unified import UnifiedPipelineClient as UnifiedCollector

__all__ = ['UnifiedCollector']
