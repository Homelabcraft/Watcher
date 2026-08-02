class WatcherError(Exception):
    """Base exception for all Watcher specific errors."""

class ConfigurationError(WatcherError):
    """Raised when the configuration is invalid and Watcher cannot start."""

class RecreationError(WatcherError):
    """Raised when container recreation fails."""

class StateStoreError(WatcherError):
    """Raised when state persistence fails."""
