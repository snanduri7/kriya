"""KNOWN-TARGET-MULTI-TARGET-STARVATION-001: replay the two preserved CAGC-0
A/B failures through build_known_target_context and
_target_package_with_window_reserve's own two-build rule, on the preserved
workspaces' real bytes at the task base (read-only: files are read from
`git show <base>:<path>`, the workspace is never touched).

Measured constants of both runs (context.known_target_package /
model.transition events): allocation window 8491 (32768 context, 16384
output); window reserve = int(8491 * 0.12) = 1018; known-target limit floor
= min(1000, int(8491 * 0.15)) = 1000. The limit and T0 budgets are swept
over every value consistent with the run (the limit is at the floor or
above; the T0 room is the 0.60 pool minus design + plan).

usage: replay.py <kriya checkout to import> <A/B root>"""
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, sys.argv[1])
from kriya.workflow.context_budget import build_known_target_context  # noqa: E402

AB = sys.argv[2]
WINDOW, RESERVE = 8491, 1018
CASES = {
    "spring-xml-pettypes-cache.r3": (
        ["src/main/java/org/springframework/samples/petclinic/service/ClinicServiceImpl.java",
         "src/main/resources/spring/tools-config.xml"],
        {"src/main/java/org/springframework/samples/petclinic/service/ClinicServiceImpl.java": [
            "ClinicServiceImpl.findPetTypes", "ClinicServiceImpl.findVets", "ClinicServiceImpl.findPetById",
            "ClinicServiceImpl.findOwnerById", "ClinicServiceImpl.savePet"]}),
    "python-error-invalidurl.r3": (
        ["httpx/_exceptions.py", "httpx/_urls.py"],
        {"httpx/_exceptions.py": ["InvalidURL.__init__", "HTTPError.__init__", "RequestError.__init__"]}),
}


def materialize(task, paths, root):
    ws = os.path.join(AB, "B", "ws", task)
    base = open(os.path.join(AB, "B", task + ".base")).read().strip()
    for path in paths:
        data = subprocess.run(["git", "-C", ws, "show", f"{base}:{path}"], check=True, capture_output=True).stdout
        os.makedirs(os.path.dirname(os.path.join(root, path)), exist_ok=True)
        with open(os.path.join(root, path), "wb") as handle:
            handle.write(data)


def package(root, paths, hints, limit, exact):
    def build(lim):
        return build_known_target_context(paths, root, None, lim, member_hints=hints, exact_member_budget=exact)[1]
    first = build(limit)
    whole = {i.path for i in first.relevant_files if i.tier == "full" and i.is_exact}
    return first if set(paths) <= whole else build(max(0, limit - RESERVE))


def shape(pkg):
    return {"items": sorted({(i.path.split("/")[-1], i.tier) for i in pkg.relevant_files}),
            "omitted": sorted({(o["path"].split("/")[-1], o["reason"]) for o in pkg.omitted})}


results = {}
for run, (paths, hints) in CASES.items():
    task = run.rsplit(".", 1)[0]
    with tempfile.TemporaryDirectory() as root:
        materialize(task, paths, root)
        shapes = {}
        for limit in range(1000, 1700, 50):
            for exact in range(2000, int(WINDOW * 0.60) + 1, 500):
                key = json.dumps(shape(package(root, paths, hints, limit, exact)), sort_keys=True)
                shapes.setdefault(key, []).append((limit, exact))
        results[run] = {key: f"{len(v)} (limit, T0) combinations, e.g. {v[0]}" for key, v in shapes.items()}
print(json.dumps(results, indent=1))
