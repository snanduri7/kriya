# Kriya AI Engineering Platform
# The version is the installed distribution's own metadata (pyproject.toml is
# its one source); kriya/build_info.py adds the embedded source identity.
from kriya.build_info import package_version as _package_version

__version__ = _package_version()
