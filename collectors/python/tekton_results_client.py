"""Backward-compatible re-export. Use clients.tekton_results instead."""
from clients.tekton_results import TektonResultsClient

__all__ = ['TektonResultsClient']
