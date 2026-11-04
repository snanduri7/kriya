"""EXTERNAL_PRIVATE_FIXTURE_REQUIRED sample (run in a subprocess by tests/test_cert_private_fixture_001.py; its name keeps it out of ordinary collection)."""
import pytest


@pytest.mark.external_private_fixture("SAMPLE-GATE")
@pytest.mark.skipif(True, reason="the fixture is not available on this machine")
def test_needs_the_private_fixture():
    raise AssertionError("never runs")


@pytest.mark.skipif(True, reason="an ordinary skip excuses nothing")
def test_ordinary_skip():
    raise AssertionError("never runs")


@pytest.mark.external_private_fixture()
@pytest.mark.skipif(True, reason="a marker without a gate id excuses nothing")
def test_marker_without_a_gate():
    raise AssertionError("never runs")
