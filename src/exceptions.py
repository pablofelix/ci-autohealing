"""Custom exceptions for CI Auto-Healing collectors."""


class CollectorError(Exception):
    """Base exception for all collector errors."""
    pass


class KubeArchiveAPIError(CollectorError):
    """Error communicating with KubeArchive API."""
    pass


class DatabaseError(CollectorError):
    """Error with database operations."""
    pass


class ConfigurationError(CollectorError):
    """Error with configuration."""
    pass


class KubernetesError(CollectorError):
    """Error communicating with Kubernetes/OpenShift."""
    pass
