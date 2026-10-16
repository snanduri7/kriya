"""Import facade for ``kriya.foundation.build_info`` (KRIYA-VERSION-001).

Re-export only; removed in slice P1. A re-export does not redirect
``mock.patch``/``monkeypatch``: patch ``kriya.foundation.build_info.<name>``."""
from kriya.foundation.build_info import BUILD_INFO_FILE as BUILD_INFO_FILE
from kriya.foundation.build_info import BUILD_INFO_SCHEMA as BUILD_INFO_SCHEMA
from kriya.foundation.build_info import EMBEDDED as EMBEDDED
from kriya.foundation.build_info import INVALID as INVALID
from kriya.foundation.build_info import PACKAGE_DIR as PACKAGE_DIR
from kriya.foundation.build_info import PRODUCT as PRODUCT
from kriya.foundation.build_info import UNAVAILABLE as UNAVAILABLE
from kriya.foundation.build_info import UNKNOWN as UNKNOWN
from kriya.foundation.build_info import embedded_build_info as embedded_build_info
from kriya.foundation.build_info import package_version as package_version
from kriya.foundation.build_info import version_line as version_line
from kriya.foundation.build_info import version_report as version_report
