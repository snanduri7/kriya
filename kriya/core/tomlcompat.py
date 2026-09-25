"""The one TOML reader Kriya uses: stdlib ``tomllib`` on Python 3.11+, the
API-identical ``tomli`` backport (a declared dependency for
``python_version < "3.11"``) on 3.10. Import ``tomllib`` from here, never
directly, so every supported interpreter parses pyproject.toml the same way."""
try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 (exercised by the 3.10 CI leg)
    import tomli as tomllib

__all__ = ["tomllib"]
