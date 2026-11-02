"""CERT-TMP-GROWTH-001 sample (run in a subprocess by tests/test_cert_tmp_growth_001.py): one passing and one failing test that each take tmp_path."""


def test_passes(tmp_path):
    (tmp_path / "f").write_text("x")


def test_fails(tmp_path):
    (tmp_path / "f").write_text("x")
    raise AssertionError("fails on purpose: its directories must be kept")
