"""Configuration: one schema, validation with clear errors, and file discovery."""

from annunciator.config.loader import Loaded, find_config, load_config, load_router_config, memory_dir, read_json
from annunciator.config.validate import ConfigError, validate_config, validate_router_config

__all__ = [
    "ConfigError",
    "Loaded",
    "find_config",
    "load_config",
    "load_router_config",
    "memory_dir",
    "read_json",
    "validate_config",
    "validate_router_config",
]
