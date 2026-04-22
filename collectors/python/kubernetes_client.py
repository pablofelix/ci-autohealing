"""Backward-compatible re-export. Use clients.kubernetes instead."""
from clients.kubernetes import KubernetesClient

__all__ = ['KubernetesClient']
