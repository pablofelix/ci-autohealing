"""Backward-compatible re-export. Use repositories.connection instead."""
from repositories.connection import DatabaseConnection as Database

__all__ = ['Database']
