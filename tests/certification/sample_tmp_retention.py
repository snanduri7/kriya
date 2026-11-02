"""CERT-TMP-GROWTH-001 sample (run in a subprocess by tests/test_cert_tmp_growth_001.py): one passing and one failing test that each take tmp_path and ALSO write a sibling
file next to it named after it (as tests/test_d1_tool_crash.py's effects log does)."""


def test_passes(tmp_path):
    (tmp_path / "f").write_text("x")
    (tmp_path.parent / f"{tmp_path.name}-sibling.log").write_text("stale if it survives")


def test_fails(tmp_path):
    (tmp_path / "f").write_text("x")
    (tmp_path.parent / f"{tmp_path.name}-sibling.log").write_text("kept for diagnosis")
    raise AssertionError("fails on purpose: its directories must be kept")
