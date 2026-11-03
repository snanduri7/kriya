"""PRD-034 shard sample (a test that outlives its runner unless the runner terminates it); its name keeps it out of ordinary collection."""
import time

import pytest


def test_slow():
    time.sleep(120)


@pytest.mark.real_toolchain
def test_tool_tier():
    pass
