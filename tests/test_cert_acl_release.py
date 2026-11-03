"""CERT-D2-ACL-LEFTOVER-001 (run 4): the helper releases the ACL a test's own directories carry, and nothing outside them."""
import getpass
import shutil
import subprocess
import sys

import pytest
from _acl_release import release_acl_protected_tree


def test_a_path_outside_or_equal_to_the_boundary_is_refused_so_the_helper_can_never_clear_arbitrary_protections(tmp_path):
    inside = tmp_path / "ws"
    inside.mkdir()
    outside = tmp_path.parent / (tmp_path.name + "-elsewhere")
    outside.mkdir()
    with pytest.raises(ValueError):
        release_acl_protected_tree(outside, tmp_path)
    with pytest.raises(ValueError):
        release_acl_protected_tree(tmp_path, tmp_path)
    with pytest.raises(ValueError):
        release_acl_protected_tree(tmp_path / ".." / "x", tmp_path)
    assert release_acl_protected_tree(tmp_path / "gone", tmp_path) is False  # nothing there: nothing to do
    shutil.rmtree(outside)


def test_a_deny_delete_tree_cannot_be_removed_until_released_and_a_sibling_protection_is_left_alone(tmp_path):
    mine, theirs = tmp_path / "mine" / "leaf", tmp_path / "theirs" / "leaf"
    mine.mkdir(parents=True)
    theirs.mkdir(parents=True)
    if sys.platform != "darwin":  # the mechanism (macOS ACLs left by Docker Desktop) does not exist elsewhere: the helper is a documented no-op
        assert release_acl_protected_tree(tmp_path / "mine", tmp_path) is False
        return
    for leaf in (mine, theirs):
        subprocess.run(["chmod", "+a", f"user:{getpass.getuser()} deny delete", str(leaf)], check=True)
    with pytest.raises(OSError):
        shutil.rmtree(tmp_path / "mine")  # the original symptom: 'Permission denied' on the protected leaf
    assert release_acl_protected_tree(tmp_path / "mine", tmp_path) is True
    shutil.rmtree(tmp_path / "mine")
    assert "deny delete" in subprocess.run(["ls", "-lde", str(theirs)], capture_output=True, text=True, check=True).stdout  # only what the test registered was cleared
    subprocess.run(["chmod", "-N", str(theirs)], check=True)
