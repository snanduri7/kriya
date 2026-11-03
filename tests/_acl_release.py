"""Release the macOS ACL that Docker Desktop leaves on bind-mount target directories (CERT-D2-ACL-LEFTOVER-001).

MEASURED (no Kriya code involved): a container that renames a directory whose children are bind-mount targets leaves ``user:<owner> deny delete`` on exactly those children
on the host, so neither pytest's tmp retention nor certify.sh can remove the tree. The negative controls in tests/test_d2_authority_namespace.py and
tests/test_slice_a_real_oci_boundary.py rename ``.kriya`` on purpose, so each releases only the directories IT moved, nothing broader.
"""
import subprocess
import sys
from pathlib import Path


def release_acl_protected_tree(path, boundary):
    """Clear the ACLs below ``path`` (a directory the calling test made), refusing anything not strictly inside ``boundary`` (the test's own tmp_path).

    Returns True when something was cleared. A no-op where the mechanism does not exist (not macOS) or the directory is already gone.
    """
    target, root = Path(path).resolve(), Path(boundary).resolve()
    if root not in target.parents:
        raise ValueError(f"{target} is not strictly inside {root}: this helper only releases what a test itself created")
    if sys.platform != "darwin" or not target.is_dir():
        return False
    subprocess.run(["chmod", "-R", "-P", "-N", str(target)], check=True, capture_output=True)
    return True
