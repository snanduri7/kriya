"""CERT-TMP-GROWTH-001 sample (run in a subprocess by tests/test_cert_tmp_growth_001.py; its name keeps it out of ordinary collection):
a test mandatory evidence of ANOTHER platform, and one mandatory on THIS platform whose skip is therefore unexpected."""
import sys

import pytest


@pytest.mark.requires_platform("nosuchos")
@pytest.mark.skipif(True, reason="MANDATORY nosuchos-HOST MEASUREMENT: needs a nosuchos host")
def test_far_platform():
    raise AssertionError("never runs here")


@pytest.mark.requires_platform(sys.platform)
@pytest.mark.skipif(True, reason="needs a host this run cannot supply")
def test_this_platform():
    raise AssertionError("never runs")
