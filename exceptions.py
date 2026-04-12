class WatcherError(Exception):
    """Base exception for all Watcher specific errors."""
    pass

class ConfigurationError(WatcherError):
    """Raised when the configuration is invalid and Watcher cannot start."""
    pass

class RecreationError(WatcherError):
    """Raised when container recreation fails."""
    pass
