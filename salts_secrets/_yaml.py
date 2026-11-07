"""PyYAML when installed, else Hermes' own ruamel-based ``hermes_yaml``: the official Hermes image and its
test environment ship no PyYAML, so ``import yaml`` would break the permission sync inside the container."""

try:
    from yaml import YAMLError, safe_dump, safe_load
except ImportError:
    from hermes_yaml import YAMLError, safe_dump, safe_load

__all__ = ["YAMLError", "safe_dump", "safe_load"]
