import os, stat, sys, tempfile, shutil, subprocess, json
sys.path.insert(0, os.path.expanduser("~/WorkingDirectory/AI/ClaudeCode/Kriya-rebuild-v2/tests"))
import test_linux_oci_workspace_identity as m
from kriya.tools.containment_oci import OCIContainmentBackend

def trial(label, root_gid):
    base = tempfile.mkdtemp(prefix="kriya-cert.", dir="/tmp") if label != "default-TMPDIR" else tempfile.mkdtemp(prefix="gidexp.")
    if root_gid is not None:
        os.chown(base, -1, root_gid)
    ws = os.path.join(base, "pytest-of-x", "pytest-0", "t0", "workspace")
    os.makedirs(os.path.dirname(ws), mode=0o755)          # inherits the group of `base` (BSD semantics) unless it is set explicitly
    os.mkdir(ws, 0o700)
    from pathlib import Path
    w = Path(ws)
    s = lambda p: (os.stat(p).st_uid, os.stat(p).st_gid)
    control = w / "control-by-host-user.txt"; control.write_text("x")      # a file the HOST USER creates in the workspace
    profile = m._profile(w)
    prepared = OCIContainmentBackend().prepare(profile, ["sh", "-c", "touch made.txt && : > Hello.class"])
    user_arg = [prepared.command_prefix[i + 1] for i, a in enumerate(prepared.command_prefix) if a == "--user"]
    (w / "Hello.java").write_text('public class Hello { public static void main(String[] a) {} }\n')
    res = m._run(m._profile(w), ["sh", "-c", "javac Hello.java && touch made.txt"], w)
    out = {"label": label, "host_uid_gid": (os.getuid(), os.getgid()), "workspace_dir_uid_gid": s(w), "workspace_parent_uid_gid": s(w.parent),
           "control_file_by_host_user": s(control), "container_user_arg": user_arg, "returncode": res.returncode,
           "container_made.txt": s(w / "made.txt") if (w / "made.txt").exists() else None,
           "container_Hello.class": s(w / "Hello.class") if (w / "Hello.class").exists() else None}
    out["matches_host_control_file"] = out["container_made.txt"] == out["control_file_by_host_user"] and out["container_Hello.class"] == out["control_file_by_host_user"]
    out["matches_explicit_host_uid_gid"] = out["container_made.txt"] == out["host_uid_gid"] and out["container_Hello.class"] == out["host_uid_gid"]
    shutil.rmtree(base, ignore_errors=True)
    return out

for label, gid in (("tmp-shaped, dir group wheel(0) [certify.sh shape]", None), ("tmp-shaped, dir group explicitly 20", 20), ("default-TMPDIR", None)):
    print(json.dumps(trial(label, gid), indent=1, default=str))
