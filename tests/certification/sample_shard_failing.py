"""PRD-034 shard sample: one node fails on purpose (tests/test_prd034_shards.py proves a failed node makes the runner exit non-zero); its name keeps it out of ordinary collection."""
import pytest


def test_ordinary_failing():
    raise AssertionError("fails on purpose")


@pytest.mark.real_toolchain
def test_tool_tier_passing():
    pass
