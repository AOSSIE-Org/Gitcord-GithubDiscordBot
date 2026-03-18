class ConfigError(RuntimeError):
    """Configuration validation or loading error."""


class GitcordPermissionError(RuntimeError):
    """Raised when required permissions are not available."""


class AdapterError(RuntimeError):
    """Raised for adapter initialization failures."""
