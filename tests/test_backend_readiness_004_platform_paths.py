"""PLAT-PATH-CONTAINMENT-001 (PLAT-017/018, BACKEND-READINESS-004).

PLAT-018: every hand-written lexical containment check (startswith(root + os.sep), commonpath, normcase) outside
kriya/platform now goes through filesystem_semantics.path_relation / path_identity (filesystem identity: case,
normalization and symlink aliases of the root are recognized); an architecture guard forbids the idiom from
returning. The four remaining lexical checks operate on model-supplied RELATIVE strings ('..' escapes) and are
listed with that reason. PLAT-017: workspace identity derives from the real path spelled as the filesystem stores
it, so a case variant of the same directory is the same workspace; state written under the legacy (normcase) id
stays readable.
"""
import os
import pathlib
import re

import pytest

from kriya.context import workspace_identity as wi
from kriya.platform import filesystem_semantics as fs
from kriya.platform.filesystem_semantics import PathRelation, canonical_spelling, path_relation

ROOT = pathlib.Path(__file__).resolve().parents[1]
_IDIOM = re.compile(r"startswith\([^)]*os\.sep|os\.path\.commonpath\(|os\.path\.normcase\(")
# Lexical by design: '..' escape checks on model-supplied RELATIVE path strings, and the legacy identity kept readable.
_ALLOWED = {
    "kriya/workflow/acceptance_oracle.py", "kriya/workflow/retry_strategy.py",
    "kriya/workflow/file_resolution.py", "kriya/context/workspace_identity.py",
}


def test_01_no_lexical_containment_idiom_outside_the_platform_package():
    offenders = {}
    for path in sorted((ROOT / "kriya").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith("kriya/platform/") or rel in _ALLOWED:
            continue
        hits = [n for n, line in enumerate(path.read_text().splitlines(), 1)
                if _IDIOM.search(line) and not line.lstrip().startswith("#")]
        if hits:
            offenders[rel] = hits
    assert offenders == {}, f"lexical containment idiom outside kriya/platform (use path_relation / path_identity): {offenders}"
    # the allow-listed sites are exactly the relative-string escape checks (plus the legacy identity)
    for rel in _ALLOWED - {"kriya/context/workspace_identity.py"}:
        text = (ROOT / rel).read_text()
        assert "os.pardir" in text or '".."' in text or "f\"..{os.sep}\"" in text, rel


def _case_variant(path: pathlib.Path):
    name = path.name
    variant = path.with_name(name.upper() if name != name.upper() else name.lower())
    return variant if variant != path and os.path.exists(variant) else None  # None: a case-sensitive filesystem


def test_02_a_case_variant_of_the_workspace_is_the_same_workspace_and_legacy_ids_stay_readable(tmp_path):
    workspace = tmp_path / "MixedCase"
    workspace.mkdir()
    variant = _case_variant(workspace)
    assert canonical_spelling(str(workspace)) == str(workspace.resolve())
    if variant is not None:  # macOS APFS default, Windows
        assert canonical_spelling(str(variant)) == canonical_spelling(str(workspace))
        assert wi.workspace_identity(str(variant)) == wi.workspace_identity(str(workspace))
        assert wi.legacy_workspace_identity(str(variant)) != wi.legacy_workspace_identity(str(workspace)) or os.name == "nt"
        assert path_relation(str(workspace), str(variant / "a")) is PathRelation.WITHIN
    else:  # a case-sensitive filesystem: different names are different directories, nothing is folded
        assert canonical_spelling(str(tmp_path / "mixedcase")) == str((tmp_path / "mixedcase").resolve())
    assert wi.ownership_metadata(str(workspace)) == {"workspace_id": wi.workspace_identity(str(workspace)), "version": "2"}
    # state written under the legacy id (version 1) still validates for the same workspace; another workspace does not
    legacy = {"_workspace": {"workspace_id": wi.legacy_workspace_identity(str(workspace)), "version": "1"}}
    wi.validate_ownership(str(workspace), legacy, "state.json")
    current = {"_workspace": wi.ownership_metadata(str(workspace))}
    wi.validate_ownership(str(workspace), current, "state.json")
    other = tmp_path / "Other"
    other.mkdir()
    with pytest.raises(wi.WorkspaceOwnershipError):
        wi.validate_ownership(str(other), current, "state.json")
    assert wi.workspace_identities(str(workspace)) >= {wi.workspace_identity(str(workspace)), wi.legacy_workspace_identity(str(workspace))}
    # a component that does not exist keeps its spelling; symlinks resolve
    assert canonical_spelling(str(workspace / "NoSuch" / "Child")).endswith(os.path.join("NoSuch", "Child"))


# ---- HOSTED-CANONICAL-SPELLING-001: a differently cased name is folded into an existing entry only where the FILESYSTEM resolves both to one object ----

def test_a_filesystem_that_resolves_both_spellings_folds_them_to_one_identity(tmp_path, monkeypatch):
    """Case-insensitive semantics, proved on any host: the (emulated) filesystem answers for the variant with the existing directory's stat."""
    workspace = tmp_path / "MixedCase"
    workspace.mkdir()
    variant = str(tmp_path / "mixedcase")
    real_stat = fs._stat  # pylint: disable=protected-access
    monkeypatch.setattr(fs, "_stat", lambda path: real_stat(str(workspace)) if os.path.basename(path) == "mixedcase" else real_stat(path))
    assert canonical_spelling(variant) == canonical_spelling(str(workspace)) == str(workspace.resolve())
    assert wi.workspace_identity(variant) == wi.workspace_identity(str(workspace))


def test_a_filesystem_that_does_not_resolve_the_variant_keeps_it_distinct(tmp_path, monkeypatch):
    """Case-sensitive semantics, proved on any host: the (emulated) filesystem has no entry named ``mixedcase``, so it keeps its own spelling and identity even though a
    differently cased sibling exists - unconditional folding would give two distinct paths one persisted identity."""
    workspace = tmp_path / "MixedCase"
    workspace.mkdir()
    variant = str(tmp_path / "mixedcase")
    real_stat = fs._stat  # pylint: disable=protected-access
    monkeypatch.setattr(fs, "_stat", lambda path: (None, True) if path == variant else real_stat(path))
    assert canonical_spelling(variant) == variant
    assert wi.workspace_identity(variant) != wi.workspace_identity(str(workspace))


def test_directories_differing_only_in_case_are_distinct_where_the_disk_allows_both_and_one_where_it_does_not(tmp_path):
    """The real disk decides (no OS-name check): a case-sensitive disk holds Foo and foo as two directories with two identities; a case-insensitive one cannot hold both
    and resolves either spelling to one identity."""
    upper, lower = tmp_path / "Foo", tmp_path / "foo"
    upper.mkdir()
    if _case_variant(upper) is not None:
        assert canonical_spelling(str(lower)) == canonical_spelling(str(upper))
        assert wi.workspace_identity(str(lower)) == wi.workspace_identity(str(upper))
        return
    lower.mkdir()
    assert canonical_spelling(str(upper)) == str(upper.resolve()) and canonical_spelling(str(lower)) == str(lower.resolve())
    assert wi.workspace_identity(str(upper)) != wi.workspace_identity(str(lower))


def test_03_a_legacy_id_written_under_another_spelling_still_validates(tmp_path, monkeypatch):
    """m71: on any filesystem - the legacy id was computed from the normcase real path; the current id from the
    canonical spelling. When they differ (a case variant, or here: the canonical form is made to differ) a legacy
    record must still be accepted for the same workspace."""
    workspace = tmp_path / "Ws"
    workspace.mkdir()
    legacy = {"_workspace": {"workspace_id": wi.legacy_workspace_identity(str(workspace)), "version": "1"}}
    monkeypatch.setattr(wi, "canonical_workspace", lambda path: str(workspace) + "-as-the-filesystem-spells-it")
    assert wi.workspace_identity(str(workspace)) != wi.legacy_workspace_identity(str(workspace))
    wi.validate_ownership(str(workspace), legacy, "state.json")  # legacy readable
    with pytest.raises(wi.WorkspaceOwnershipError):
        wi.validate_ownership(str(workspace), {"_workspace": {"workspace_id": "0" * 64, "version": "2"}}, "state.json")
