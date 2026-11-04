"""NOT_APPLICABLE_ON_<platform> sample (run in a subprocess by tests/test_cert_not_applicable_001.py; its name keeps it out of ordinary collection)."""
import sys

import pytest

HERE = "tests/certification/sample_not_applicable.py"


@pytest.mark.not_applicable_on(sys.platform, equivalent=f"{HERE}::test_the_invariant_is_proved_here")
@pytest.mark.skipif(True, reason="the mechanism belongs to another platform")
def test_mechanism_of_another_platform():
    raise AssertionError("never runs")


def test_the_invariant_is_proved_here():
    assert True


@pytest.mark.not_applicable_on("nosuchos", equivalent=f"{HERE}::test_the_invariant_is_proved_here")
@pytest.mark.skipif(True, reason="declared not applicable on a DIFFERENT platform")
def test_declared_for_another_platform_only():
    raise AssertionError("never runs")


@pytest.mark.not_applicable_on(sys.platform)
@pytest.mark.skipif(True, reason="a declaration that names no equivalent excuses nothing")
def test_declaration_without_an_equivalent():
    raise AssertionError("never runs")
