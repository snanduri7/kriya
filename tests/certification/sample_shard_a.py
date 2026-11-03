"""PRD-034 shard sample A (run in subprocesses by tests/test_prd034_shards.py; its name keeps it out of ordinary collection)."""
import pytest


def test_a_one():
    pass


@pytest.mark.parametrize("value", ["x", "@y"])
def test_a_param(value):
    assert value


@pytest.mark.real_toolchain
def test_a_toolchain_tier():
    pass
