"""Sample for tests/test_session_base_unique_001.py (not collected by the suite: no test_ prefix). Each test appends the base temp this
invocation / worker was given to the file the control names."""
import json
import os

import pytest


@pytest.mark.parametrize("index", range(6))
def test_records_its_base(tmp_path_factory, tmp_path, index):
    with open(os.environ["KRIYA_SAMPLE_SESSION_BASE_OUT"], "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"base": str(tmp_path_factory.getbasetemp()), "tmp_path": str(tmp_path), "worker": os.environ.get("PYTEST_XDIST_WORKER", "main"),
                                 "inherited_temproot": os.environ.get("PYTEST_DEBUG_TEMPROOT")}) + "\n")
