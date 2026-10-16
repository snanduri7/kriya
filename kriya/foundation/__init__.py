"""FOUNDATION (full modularization, slice A1): the lowest layer.

Typed base errors, identifier value types, clock/deadline and cancellation
contracts, the installed build identity and the TOML reader. FOUNDATION
holds no business logic and imports nothing else under ``kriya``
(tests/architecture/test_import_direction.py). ``__version__`` is the
installed distribution's version, the same value ``kriya.__version__``
reports."""
from kriya.foundation.build_info import package_version as _package_version

__version__ = _package_version()
