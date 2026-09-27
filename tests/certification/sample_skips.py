"""PRD-034: a sample the certification-plugin tests run in a subprocess
(its name keeps it out of ordinary collection)."""
import pytest


def test_runs():
    assert True


@pytest.mark.skipif(True, reason="docker CLI not available")
def test_needs_docker():
    raise AssertionError("never runs")


@pytest.mark.xfail(reason="known gap", strict=False)
def test_known_gap():
    raise AssertionError("expected")
