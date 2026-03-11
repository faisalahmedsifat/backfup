import os
import re

_ENV_REF_PATTERN = re.compile(r'^ENV\("(.+)"\)$')


def encode_env_ref(var_name: str) -> str:
    """Encode an environment variable name as a config-safe reference string."""
    return f'ENV("{var_name}")'


def is_env_ref(value: str) -> bool:
    """Return True if the value is an encoded environment variable reference."""
    return bool(_ENV_REF_PATTERN.match(value))


def resolve_credential(value: str) -> str:
    """
    Resolve a credential value from config.

    - If it's an ENV("VAR") reference, reads and returns the environment variable.
    - Otherwise returns the value as-is (direct credential).

    Raises KeyError if the referenced environment variable is not set.
    """
    match = _ENV_REF_PATTERN.match(value)
    if match:
        var_name = match.group(1)
        if var_name not in os.environ:
            raise KeyError(
                f"Environment variable '{var_name}' is referenced in the config "
                f"but is not set in the current environment."
            )
        return os.environ[var_name]
    return value