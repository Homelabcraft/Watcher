class WatcherError(Exception):
    """Base exception for all Watcher specific errors."""

class ConfigurationError(WatcherError):
    """Raised when the configuration is invalid and Watcher cannot start."""

class RecreationError(WatcherError):
    """Raised when container recreation fails."""

class ImageRecreationError(RecreationError):
    """A known replacement-image incompatibility, not a daemon or state error."""

class UpdateWindowClosed(WatcherError):
    """The window closed during preparation, before stopping the original."""

class StateStoreError(WatcherError):
    """Raised when state persistence fails."""
