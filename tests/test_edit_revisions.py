import pytest

from kriya.workflow.edit_safety import (
    FileRevisionConflict, apply_anchored_edits, commit_revision_grounded_file,
    content_revision,
)


def test_revision_grounded_commit_rejects_stale_base_without_overwriting(tmp_path):
    target = tmp_path / "App.java"
    original = "class App { int value = 1; }\n"
    target.write_text(original, encoding="utf-8")
    expected = content_revision(original)
    target.write_text("class App { int value = 2; }\n", encoding="utf-8")

    with pytest.raises(FileRevisionConflict):
        commit_revision_grounded_file(
            str(target), "class App { int value = 3; }\n", expected,
        )

    assert target.read_text(encoding="utf-8") == "class App { int value = 2; }\n"


def test_multiple_edits_are_staged_before_atomic_commit(tmp_path):
    target = tmp_path / "pricing.py"
    original = "rate = 1\nfee = 2\n"
    target.write_text(original, encoding="utf-8")
    # Depending on which deterministic guard sees the fabricated second
    # anchor first, it is rejected as ungrounded or as a zero-match edit. The
    # transaction guarantee under test is that edit #1 never reaches disk.
    with pytest.raises(ValueError, match="edit #2"):
        candidate = apply_anchored_edits(original, [
            {"search": "rate = 1", "replace": "rate = 3"},
            {"search": "missing = 2", "replace": "fee = 4"},
        ], original)
        commit_revision_grounded_file(str(target), candidate, content_revision(original))

    assert target.read_text(encoding="utf-8") == original
