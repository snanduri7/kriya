"""S7 tripwire probe (NOT a suite test: the name does not match test_*.py; tests/test_s7_containment_census.py runs it explicitly in a subprocess and expects it to FAIL):
a test that consults the canonical containment decision but has no entry in tests/containment_census.py."""


def test_consults_the_owner_without_a_classification():
    from kriya.config.config import AppConfig
    from kriya.tools.containment import decide_external_execution

    assert decide_external_execution(AppConfig().autonomy, subject="probe").contained is True
