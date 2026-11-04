"""The one TOML reader Kriya uses: stdlib ``tomllib`` (Python 3.11+; Kriya requires
3.12+, so the ``tomli`` fallback below is unreachable on a supported interpreter and
``tomli`` is no longer a dependency). Import ``tomllib`` from here, never
directly, so every interpreter parses pyproject.toml the same way.
FOUNDATION-owned (full modularization, slice A1); ``kriya/core/tomlcompat.py``
is its import facade."""
try:
    import tomllib
except ModuleNotFoundError:  # unreachable on a supported interpreter (requires-python >=3.12)
    import tomli as tomllib

__all__ = ["tomllib"]
