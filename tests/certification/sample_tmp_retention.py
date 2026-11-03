"""CERT-TMP-GROWTH-001 sample (run in a subprocess by tests/test_cert_tmp_growth_001.py): passing and failing tests that take tmp_path and/or scratch_dir, one of which also writes a sibling
file next to its tmp_path named after it (as tests/test_d1_tool_crash.py's effects log does). The LAST test looks at the basetemp while the session is still running, because pytest removes
the dangling ``<name>current`` links only at session end."""
import os


def test_passes(tmp_path):
    (tmp_path / "f").write_text("x")
    (tmp_path.parent / f"{tmp_path.name}-sibling.log").write_text("stale if it survives")


def test_scratch_passes(scratch_dir, tmp_path):
    (scratch_dir("passed-home") / "f").write_text("x")
    (tmp_path / "f").write_text("x")


def test_fails(tmp_path):
    (tmp_path / "f").write_text("x")
    (tmp_path.parent / f"{tmp_path.name}-sibling.log").write_text("kept for diagnosis")
    raise AssertionError("fails on purpose: its directories must be kept")


def test_scratch_fails(scratch_dir):
    (scratch_dir("kept-home") / "f").write_text("x")
    raise AssertionError("fails on purpose: its scratch directory must be kept")


def test_zz_the_basetemp_holds_only_what_a_failed_test_must_keep(tmp_path_factory):
    base = str(tmp_path_factory.getbasetemp())
    entries = sorted(os.listdir(base))
    dangling = [name for name in entries if os.path.islink(os.path.join(base, name)) and not os.path.exists(os.path.join(base, name))]
    assert not dangling, f"dead links left behind by released directories: {dangling}"
    leftovers = [name for name in entries if name.startswith(("test_passes", "test_scratch_passes", "passed-home"))]
    assert not leftovers, f"a passed test left {leftovers}"
    assert "test_fails0" in entries and "kept-home0" in entries, entries
