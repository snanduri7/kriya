"""P3-D mutation campaign (FS-1C1 method: one exact replacement, or a list of them, per mutant; KILLED iff the test
set fails). Each mutant runs in its own fresh clone of the commit under test (never the working tree - rule 19).

usage: python p3d_mutation_campaign.py <commit>    -> p3d_mutation_results.json (overwritten per run; the run log is kept)
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

SRC = os.path.expanduser("~/kriya-wt/p3d")
PYTEST = os.path.expanduser("~/WorkingDirectory/AI/ClaudeCode/Kriya-By-ClaudeCode/.venv/bin/pytest")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "p3d_mutation_results.json")
TESTS = ["tests/test_p3d_structural_insertion.py", "tests/test_p3a_sent_request_anchor_authority.py",
         "tests/test_context_edit_protocol_001.py"]
IL = "kriya/workflow/insertion_locus.py"
EC = "kriya/workflow/edit_capability.py"
AT = "kriya/workflow/attempt.py"
M = [
    # owner targets
    ("raw-final-brace-search", IL, "    gap_start = _char_offset(data, start_byte)\n",
     "    gap_start = content.rstrip().rindex(\"}\")\n"),
    ("ignore-owner-identity", IL,
     "    owner = next((symbol for symbol in old.symbols if symbol.symbol_id == locus.owner_symbol_id), None)",
     "    owner = next((symbol for symbol in old.symbols if symbol.is_type), None)"),
    ("ignore-revision-mismatch", IL, "    if content_revision(before) != locus.revision:", "    if False:"),
    ("ignore-context-digest-mismatch", IL,
     "    if \"\".join(lines[locus.start_line - 1:locus.end_line]) != locus.carrier:", "    if False:"),
    ("allow-neighbouring-replacement", IL,
     "    if offsets is None or offsets[1] < locus.gap_start or offsets[0] > locus.gap_end:",
     "    if offsets is not None and (offsets[1] < locus.gap_start or offsets[0] > locus.gap_end):"),
    ("accept-locus-not-in-final-request", IL, "    if not any(locus.carrier in prompt for prompt in sent):",
     "    if False:"),
    ("insertion-in-a-different-class", IL,
     "    escaped = sorted(key for kind, key in new_keys - old_keys if not key.startswith(owner_prefix))",
     "    escaped = []"),
    ("reuse-a-stale-locus", EC, "    if insertion is not None and insertion.revision != revision:", "    if False:"),
    ("full-file-authority-from-insertion", EC,
     "(FULL_FILE_REPLACEMENT, self.full_file)) if ok)",
     "(FULL_FILE_REPLACEMENT, self.full_file or self.insertion is not None)) if ok)"),
    ("fallback-to-full-file-context", IL, "    first = min(first_gap_line, last - MIN_CARRIER_LINES + 1)",
     "    first = 1"),
    # P3-D's own decision points
    ("ambiguous-owner-takes-first", IL,
     "            return None, \"the owner type is ambiguous: \" + \", \".join(sorted(s.lookup_key for s in candidates))",
     "            owner = candidates[0]\n            break"),
    ("enum-owner-accepted", IL, "    if owner.kind not in SUPPORTED_OWNER_KINDS or owner.body is None:",
     "    if owner.body is None:"),
    ("no-new-member-required", IL, "    if not planned:\n        return None, \"the goal names no new member of the owner\"",
     "    if False:\n        return None, \"the goal names no new member of the owner\""),
    ("grounded-member-tier-ignored", IL, "    if grounded_members:\n", "    if False:\n"),
    ("insertion-edit-not-verified", AT,
     "            _authorize_structural_insertion(state, capability, path, index, edit, current)\n", ""),
    ("any-anchor-is-insertion-only", EC,
     "        if self.insertion is not None and norm_search in normalize_whitespace(self.insertion.carrier):",
     "        if self.insertion is not None:"),
    ("declaration-loss-accepted", IL, "    if not old_keys <= new_keys or escaped or not new_keys - old_keys:",
     "    if escaped or not new_keys - old_keys:"),
    ("no-declaration-insertion-accepted", IL, "    if not old_keys <= new_keys or escaped or not new_keys - old_keys:",
     "    if not old_keys <= new_keys or escaped:"),
    ("unparsable-result-accepted", IL, "    if new.state is not ParseState.PARSED:\n        return f\"{INSERTION_STRUCTURE_CHANGED}",
     "    if False:\n        return f\"{INSERTION_STRUCTURE_CHANGED}"),
    ("python-supported", IL,
     "    if capability_status(path, Capability.STRUCTURAL_INSERTION) is CapabilityStatus.UNSUPPORTED:", "    if False:"),
]


def clone(commit):
    root = tempfile.mkdtemp(prefix="p3d-mut-")
    subprocess.run(["git", "clone", "-q", "--no-checkout", SRC, root], check=True)
    subprocess.run(["git", "checkout", "-q", commit], cwd=root, check=True)
    return root


def run_tests(repo):
    proc = subprocess.run(f"ulimit -n 256; {PYTEST} -q -x -p no:cacheprovider -n 8 " + " ".join(TESTS), shell=True,
                          cwd=repo, env=dict(os.environ, PYTHONPATH=repo), capture_output=True, text=True)
    return proc.returncode, (proc.stdout.strip().splitlines() or [""])[-1]


def main(commit):
    results = {"commit": commit, "tests": TESTS, "mutants": []}
    base = clone(commit)
    try:
        code, tail = run_tests(base)
        print("baseline", tail, flush=True)
        if code != 0:
            raise SystemExit(f"baseline not green: {tail}")
        results["baseline"] = tail
    finally:
        shutil.rmtree(base, ignore_errors=True)
    for name, path, old, new in M:
        repo = clone(commit)
        try:
            target = os.path.join(repo, path)
            source = open(target).read()
            pairs = old if isinstance(old, list) else [(old, new)]
            counts = [source.count(o) for o, _ in pairs]
            if counts != [1] * len(pairs):
                results["mutants"].append({"name": name, "status": "NOT_APPLIED", "count": counts})
                print(name, "NOT_APPLIED", counts, flush=True)
                continue
            for o, n in pairs:
                source = source.replace(o, n)
            open(target, "w").write(source)
            started = time.time()
            code, tail = run_tests(repo)
            status = "KILLED" if code != 0 else "SURVIVED"
            results["mutants"].append({"name": name, "file": path, "status": status, "tail": tail,
                                       "seconds": round(time.time() - started, 1)})
            print(name, status, tail, flush=True)
        finally:
            shutil.rmtree(repo, ignore_errors=True)
    counts = {}
    for mutant in results["mutants"]:
        counts[mutant["status"]] = counts.get(mutant["status"], 0) + 1
    results["counts"] = counts
    json.dump(results, open(OUT, "w"), indent=2)
    print("TOTAL", len(M), counts, flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
