#!/usr/bin/env python3
"""Freeze the MODEL-ROUTING-V1 task bank (experiment-only, no model calls).

  build    --ids FILE   per task: neutral solver workspace (bank/frozen/<TID>/ws), oracle scripts, task package (task.json)
  leakscan              solver-visible artifacts (ws tree, ws git history, goal) vs reference material
  partition --seed S    deterministic stratified ROUTING-DEV / FINAL-HOLDOUT split (records algorithm, seed, hashes)
  manifest              BANK_MANIFEST.json: per-task table (spec section 13) + digests of every frozen artifact and the tools; BANK_MANIFEST_HASH

Inputs are the candidate working directories under bank/tasks/<CID> (prep + certification already done); the frozen package never
contains reference patches in any solver-visible path. The reference material stays under bank/frozen/<TID>/evaluator/.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

BANK = Path.home() / "kriya-m1-live/model-routing-v1/bank"
FROZEN = BANK / "frozen"
TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import bank_tool as bt  # noqa: E402


# Pre-model reviewer classification (recorded before any model touched the bank); used as a partition feature, never as a model-quality signal.
DIFFICULTY_ANCHORS = frozenset({"MR-J06", "MR-J08", "MR-J10", "MR-P01", "MR-P07", "MR-P10", "MR-P14", "MR-P16"})
OVERLAP_ORDER = ["NO KNOWN OVERLAP", "SAME REPOSITORY ONLY", "POSSIBLE RELATED TASK", "EXACT/MATERIAL TASK OVERLAP"]


def sha_bytes(b):
    return hashlib.sha256(b).hexdigest()


def sha_file(p):
    return sha_bytes(Path(p).read_bytes())


def sha_tree(root, skip=(".git",)):
    h = hashlib.sha256()
    for p in sorted(Path(root).rglob("*")):
        if p.is_file() and not any(s in p.parts for s in skip):
            h.update(str(p.relative_to(root)).encode() + b"\0" + p.read_bytes())
    return h.hexdigest()


def task_type(meta, goal):
    """Rule-based and observable: build_config if the reference touches packaging/build files; feature if the upstream PR title
    starts with Add/Support/Allow; otherwise bug_fix. `regression_reported` is a separate text flag (see regression_flag)."""
    files = meta["prod_files"] + meta["other_files"]
    if any(re.search(r"(^|/)(pyproject\.toml|setup\.(py|cfg)|pom\.xml|build\.gradle(\.kts)?)$", f) for f in files):
        return "build_config"
    if re.match(r"(?i)(add|support|allow)\b", meta["title"]):
        return "feature"
    return "bug_fix"


def regression_flag(cid):
    cand = json.loads((BANK / "cands" / (cid.replace("#", "_") + ".json")).read_text())
    text = " ".join([cand["title"], cand["pr_body"]] + [i["title"] or "" for i in cand["issues_full"]] + [i["body"] or "" for i in cand["issues_full"]])
    return bool(re.search(r"(?i)\bregress(ion|ed)?\b|used to work|changed (at some point|between)|were lost in|no longer", text))


def metadata(cid, meta, goal, spec, cert):
    prod = meta["prod_files"]
    modules = meta.get("prod_modules") or []
    return {"language": meta["lang"], "build_system": meta["build"],
            "module_structure": "multi_module" if (meta["lang"] == "java" and any(modules)) else "single_module",
            "reference_prod_files": len(prod), "reference_test_files": len(meta["test_files"]), "reference_churn": meta["churn"],
            "reference_churn_bucket": "S" if meta["churn"] <= 40 else "M" if meta["churn"] <= 90 else "L",
            "localization_shape": "single_file" if len(prod) == 1 else "multi_file",
            "task_type": task_type(meta, goal), "regression_reported": regression_flag(cid), "f2p_tests": len(spec["F2P"]), "accept_tests": len(spec["accept_set"]),
            "baseline_fails_by_compile_break": spec["baseline_compile_break"],
            "reference_dirs": sorted({str(Path(p).parent) for p in prod})[:4]}


def oracle_script(tid, kind):
    return ("#!/bin/zsh\n# Frozen " + kind + " oracle for " + tid + ". Usage: " + kind + ".sh <TREE> [SCR_unused]. The evaluator copies TREE to its own scratch.\n"
            "# Exit: 0 PASS / 1 REJECT / 3 ENVIRONMENT_UNAVAILABLE / 4 INDETERMINATE (usage and internal errors are 4)\n"
            "[ $# -ge 1 ] || { echo 'usage: " + kind + ".sh TREE' >&2; exit 4; }\n"
            "D=\"$(cd \"$(dirname \"$0\")\" && pwd)\"\n"
            "exec python3 \"$D/../../_tools/bank_eval.py\" evaluate-pkg \"$D/..\" " + kind + " \"$1\" --out \"${ORACLE_LOG:-/dev/null}\"\n")


def vendor_tools():
    dst = FROZEN / "_tools"
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    for name in ("bank_tool.py", "bank_eval.py", "oracle_preflight.py"):
        shutil.copy2(TOOLS / name, dst / name)


def build(ids_file):
    """Package skeleton per task. Certification artifacts (regression_set, cert, task.json) are produced afterwards by `bank_eval.py` + `finalize`."""
    mapping = json.loads(Path(ids_file).read_text())  # {TID: CID}
    FROZEN.mkdir(exist_ok=True)
    vendor_tools()
    for tid, cid in mapping.items():
        src = bt.BANK / "tasks" / cid.replace("#", "_")
        meta = json.loads((src / "task.json").read_text())
        probe = json.loads((src / "probe.json").read_text())
        goal = (src / "goal.txt").read_text()
        out = FROZEN / tid
        if out.exists():
            shutil.rmtree(out)
        for sub in ("evaluator", "oracle"):
            (out / sub).mkdir(parents=True)
        # solver workspace: base tree only, single neutral commit, no remote
        ws = out / "ws"
        shutil.copytree(src / "base_tree", ws, symlinks=True)
        env = dict(os.environ, GIT_AUTHOR_NAME="bank", GIT_AUTHOR_EMAIL="bank@localhost", GIT_COMMITTER_NAME="bank",
                   GIT_COMMITTER_EMAIL="bank@localhost", GIT_AUTHOR_DATE="2000-01-01T00:00:00Z", GIT_COMMITTER_DATE="2000-01-01T00:00:00Z")
        for cmd in (["git", "init", "-q"], ["git", "add", "-A"], ["git", "commit", "-q", "-m", "workspace snapshot"]):
            subprocess.run(cmd, cwd=ws, env=env, check=True, capture_output=True)
        # evaluator material (never solver-visible)
        shutil.copytree(src / "ref_tree", out / "evaluator/ref_tree", symlinks=True)
        shutil.copy2(src / "ref_prod.patch", out / "evaluator/ref_prod.patch")
        shutil.copy2(src / "ref_test.patch", out / "evaluator/ref_test.patch")
        shutil.copy2(src / "probe.json", out / "evaluator/probe.json")
        # oracle (self-contained)
        shutil.copytree(src / "hidden", out / "oracle/hidden")
        keep = ("id", "repo", "pr", "lang", "build", "module", "classes", "test_modules", "pkg", "src_layout", "test_files", "prod_files", "other_files")
        (out / "oracle/meta.json").write_text(json.dumps({k: meta[k] for k in keep if k in meta}, indent=1))
        jvm_scope = ("tests of module " + (meta.get("module") or "<root>") + (" and the reactor modules it depends on (-am); downstream modules are not covered"
                     if meta["build"] == "maven" else " only (gradle :module:test); downstream modules are not covered")) if meta["lang"] == "java" else None
        spec = {"id": cid, "accept_set": sorted(set(probe["F2P"]) | set(probe["P2P_accept"])), "F2P": probe["F2P"], "P2P_accept": probe["P2P_accept"],
                "accept_selectors": meta.get("classes") or meta.get("test_modules"),
                "regress_scope": jvm_scope or "full pytest run from the repository root (collection errors in the pinned environment are tolerated)",
                "baseline_compile_break": probe.get("baseline_compile_break", False), "f2p_absent_at_base": probe.get("f2p_absent_at_base", 0)}
        (out / "oracle/spec.json").write_text(json.dumps(spec, indent=1))
        (out / "goal.txt").write_text(goal)
        (out / "task_source.json").write_text(json.dumps({"candidate_id": cid, "meta": meta}, indent=1))
        for kind in ("accept", "regress"):
            p = out / "oracle" / f"{kind}.sh"
            p.write_text(oracle_script(tid, kind))
            p.chmod(0o755)
        print(tid, cid, "package skeleton built")


AI_MARK = re.compile(r"(?i)\b(claude|codex|copilot|chatgpt|gpt-?\d|gemini|ai[- ]assisted|ai agent|generated (with|by) (an? )?(ai|llm)|co-authored-by: [a-z ]*(claude|copilot))\b")


def finalize():
    """task.json per package from the certification record; digests bind every artifact the oracle uses."""
    prov = json.loads((BANK / "goals/goal_provenance.json").read_text())
    ids = json.loads((BANK / "final_ids.json").read_text())
    for tid in sorted(ids):
        d = FROZEN / tid
        info = json.loads((d / "task_source.json").read_text())
        cid, meta = info["candidate_id"], info["meta"]
        spec = json.loads((d / "oracle/spec.json").read_text())
        reg = json.loads((d / "oracle/regression_set.json").read_text())
        cert = json.loads((d / "evaluator/cert.json").read_text())
        goal = (d / "goal.txt").read_text()
        cand = json.loads((BANK / "cands" / (cid.replace("#", "_") + ".json")).read_text())
        md = metadata(cid, meta, goal, spec, cert)
        md["size_tier"] = "anchor_small" if meta["churn"] <= 40 and len(meta["prod_files"]) == 1 else "standard"
        md["upstream_pr_ai_assisted_per_pr_text"] = bool(AI_MARK.search(cand["pr_body"] + " " + cand["title"]))
        pkg = {
            "TASK_ID": tid, "candidate_id": cid, "repository_id": meta["repo"], "base_revision": meta["base"], "fix_revision": meta["fix"],
            "goal_digest": sha_bytes(goal.encode()), "language": meta["lang"], "build_system": meta["build"],
            "setup_manifest": {"jdk": "21" if meta["repo"] == "jdbi/jdbi" or cid == "resilience4j#2507" else "17" if meta["lang"] == "java" else None,
                               "python": "3.12" if meta["lang"] == "python" else None, "network_at_evaluation": False,
                               "caches": {"maven_local_repo": "bank/m2", "gradle_user_home": "bank/gradle-home", "gradle_distributions": "bank/dist",
                                          "python_venv": f"bank/venvs/{meta['repo'].replace('/', '_')}"},
                               "env": {"SETUPTOOLS_SCM_PRETEND_VERSION": "0.0.1"} if meta["lang"] == "python" else {},
                               "python_build": "scratch tree is built offline into <scratch>/.dist (pip --no-build-isolation --no-index --target); the venv holds no project install"
                               if meta["lang"] == "python" else None},
            "accept_oracle": {"script": "oracle/accept.sh", "digest": sha_file(d / "oracle/accept.sh"), "spec_digest": sha_file(d / "oracle/spec.json"),
                              "hidden_tests_digest": sha_tree(d / "oracle/hidden"), "required_tests": len(spec["accept_set"]),
                              "fail_to_pass": len(spec["F2P"]), "pass_to_pass": len(spec["P2P_accept"]), "selectors": spec["accept_selectors"],
                              "covers": "behavior of the hidden upstream tests changed/added by the fix, by test ID; solver edits to test infrastructure are restored before the run"},
            "regression_oracle": {"script": "oracle/regress.sh", "digest": sha_file(d / "oracle/regress.sh"), "set_digest": sha_file(d / "oracle/regression_set.json"),
                                  "tests": len(reg["ids"]), "scope": spec["regress_scope"], "excluded_superseded_by_fix": len(reg["superseded_by_fix"]),
                                  "excluded_base_failing_or_skipped": reg["base_failing_or_skipped"],
                                  "covers": "every test passing on BASE and on REFERENCE(with original tests) in the scope above"},
            "oracle_meta_digest": sha_file(d / "oracle/meta.json"),
            "environment_preflight": {"baseline_accept_verdicts": [b["accept"]["verdict"] for b in cert["baseline"]],
                                      "baseline_p2p_passing": [b["accept"]["p2p_pass"] for b in cert["baseline"]],
                                      "f2p_failed_or_absent_at_baseline": [b["accept"]["f2p_failed_or_absent"] for b in cert["baseline"]],
                                      "baseline_compile_break": spec["baseline_compile_break"]},
            "reference_fix": {"patch_digest": sha_file(d / "evaluator/ref_prod.patch"), "test_patch_digest": sha_file(d / "evaluator/ref_test.patch"),
                              "ref_tree_digest": sha_tree(d / "evaluator/ref_tree"), "upstream": f"https://github.com/{meta['repo']}/pull/{meta['pr']}", "evaluator_only": True},
            "certification": {"verdict": cert["verdict"], "limits": cert["limits"], "repeats": cert["repeats"], "cost": cert["cost"],
                              "known_good_stable": all(r["accept"]["verdict"] == "PASS" and r["regress"]["verdict"] == "PASS" for r in cert["ref"]),
                              "controls": {n: {"expected": c["expected"], "as_expected": c["as_expected"], "verdict": c["accept"]["verdict"]} for n, c in cert["controls"].items()}},
            "provenance": {"pr_url": cand["url"], "pr_created": cand.get("pr_created"), "pr_merged": cand["mergedAt"],
                           "issues": [{"number": i["number"], "created_at": i["created_at"], "url": f"https://github.com/{meta['repo']}/issues/{i['number']}"}
                                      for i in cand["issues_full"]],
                           "goal_sources": prov[cid]["sources"], "goal_derivation": prov[cid]["derivation"], "ws_tree_digest": sha_tree(d / "ws")},
            "metadata": md,
        }
        (d / "task.json").write_text(json.dumps(pkg, indent=1))
        print(tid, cid, cert["verdict"])


# ------------------------------------------------------------------ leakage scan
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{3,}")


def leakscan(tid):
    d = FROZEN / tid
    pkg = json.loads((d / "task.json").read_text())
    cid = pkg["candidate_id"]
    goal = (d / "goal.txt").read_text()
    findings = []
    # 1. git history of the solver workspace
    revs = subprocess.run(["git", "rev-list", "--all"], cwd=d / "ws", capture_output=True, text=True).stdout.split()
    if len(revs) != 1:
        findings.append(f"ws git has {len(revs)} commits")
    for sha in (pkg["fix_revision"], pkg["base_revision"]):
        if subprocess.run(["git", "cat-file", "-e", sha], cwd=d / "ws", capture_output=True).returncode == 0:
            findings.append(f"ws git contains upstream object {sha[:10]}")
    if subprocess.run(["git", "remote"], cwd=d / "ws", capture_output=True, text=True).stdout.strip():
        findings.append("ws git has a remote")
    # 2. solver-visible tree content vs fix revision / patch digests / hidden-only files
    blob = "\n".join(p.read_text(errors="ignore") for p in (d / "ws").rglob("*") if p.is_file() and ".git/" not in str(p) and p.stat().st_size < 400000)
    for needle in (pkg["fix_revision"][:12], pkg["reference_fix"]["patch_digest"][:16], "expected fix is in", "KNOWN_GOOD", "ref_prod", "ref_tree"):
        if needle in blob or needle in goal:
            findings.append(f"needle '{needle}' present in solver-visible text")
    # 3. goal vs reference material
    ref = (d / "evaluator/ref_prod.patch").read_text()
    added = {t for ln in ref.splitlines() if ln.startswith("+") and not ln.startswith("+++") for t in IDENT.findall(ln)}
    base_text = "\n".join(p.read_text(errors="ignore") for p in (d / "ws").rglob("*") if p.is_file() and ".git/" not in str(p) and p.stat().st_size < 400000 and p.suffix in (".java", ".py", ".kt", ".ftl", ".jj", ".xml", ".toml", ".cfg"))
    base_ids = set(IDENT.findall(base_text))
    ref_only = sorted(t for t in added if t not in base_ids)
    stems = {Path(f).stem for f in pkg_ref_files(d)}
    public = (BANK / "cands" / (cid.replace("#", "_") + ".json")).read_text()
    goal_tokens = set(IDENT.findall(goal))
    for tok in sorted(goal_tokens):
        kind = "reference-only identifier" if tok in ref_only else "changed-file stem" if tok in stems else None
        if kind:
            findings.append(f"goal token '{tok}' is a {kind}" + (" (also in the public issue/PR text)" if tok in public else " (NOT in public text)"))
    paths = [f for f in pkg_ref_files(d) if f in goal or Path(f).name in goal]
    if paths:
        findings.append(f"goal names reference file path(s): {paths}")
    return {"task": tid, "ref_only_identifiers_total": len(ref_only), "findings": findings}


def pkg_ref_files(d):
    return [ln.split(" b/")[-1].strip() for ln in (d / "evaluator/ref_prod.patch").read_text().splitlines() if ln.startswith("diff --git")]


# ------------------------------------------------------------------ partition
def partition(seed, tids):
    """Exact minimiser of the L1 imbalance of feature counts between the two arms, subject to every language splitting n/2 : n/2.
    All C(n,n/2) splits per language are enumerated and combined; ties are broken by sha256(seed|sorted dev ids)."""
    import itertools
    pk = {t: json.loads((FROZEN / t / "task.json").read_text()) for t in tids}

    def feats(p):
        m = p["metadata"]
        return [f"lang={p['language']}", f"build={p['build_system']}", f"type={m['task_type']}", f"loc={m['localization_shape']}",
                f"mod={m['module_structure']}", f"churn={m['reference_churn_bucket']}", f"anchor={p['TASK_ID'] in DIFFICULTY_ANCHORS}"]

    by_lang = {}
    for t in tids:
        by_lang.setdefault(pk[t]["language"], []).append(t)
    options = []
    for lang in sorted(by_lang):
        group = sorted(by_lang[lang])
        options.append([frozenset(c) for c in itertools.combinations(group, len(group) // 2)])
    best = None
    for combo in itertools.product(*options):
        dev = frozenset().union(*combo)
        diff = {}
        for t in tids:
            for f in feats(pk[t]):
                diff[f] = diff.get(f, 0) + (1 if t in dev else -1)
        key = (sum(abs(v) for v in diff.values()), sha_bytes((seed + "|" + ",".join(sorted(dev))).encode()))
        if best is None or key < best[0]:
            best = (key, dev, diff)
    dev = best[1]
    result = {"ROUTING-DEV": sorted(dev), "FINAL-HOLDOUT": sorted(set(tids) - dev)}
    balance = {}
    for t in tids:
        for f in feats(pk[t]):
            balance.setdefault(f, [0, 0])[0 if t in dev else 1] += 1
    manifest_file = FROZEN / "BANK_MANIFEST.json"
    if not manifest_file.exists():
        raise SystemExit("partition refuses to run before the bank manifest exists (freeze first, partition second)")
    inputs = json.loads(manifest_file.read_text())["bank_manifest_hash"]
    out = {"algorithm": "exact L1-imbalance minimiser over per-language n/2 splits, hash tie-break (tools/freeze_bank.py:partition)", "seed": seed,
           "features": ["lang", "build", "type", "loc", "mod", "churn", "anchor"], "total_l1_imbalance": best[0][0], "bank_manifest_hash": inputs,
           "partition": result, "balance_dev_vs_holdout": dict(sorted(balance.items()))}
    out["partition_hash"] = sha_bytes(json.dumps(result, sort_keys=True).encode())
    return out


def package_digest(d):
    return sha_tree(d, skip=(".git", "cert_logs"))


def manifest_cmd(out):
    overlap = json.loads((BANK / "exposure/overlap.json").read_text())
    tasks, files = {}, {}
    for t in sorted(FROZEN.iterdir()):
        if not (t / "task.json").exists():
            continue
        pkg = json.loads((t / "task.json").read_text())
        ov = overlap[t.name]["per_dataset"]
        worst = max((v["class"] for v in ov.values()), key=lambda c: OVERLAP_ORDER.index(c) if c in OVERLAP_ORDER else len(OVERLAP_ORDER))
        cost = pkg["certification"]["cost"]
        tasks[t.name] = {
            "TASK_ID": t.name, "REPOSITORY": pkg["repository_id"], "CANDIDATE": pkg["candidate_id"], "LANGUAGE": pkg["language"], "BUILD_SYSTEM": pkg["build_system"],
            "BASE_REVISION": pkg["base_revision"], "REFERENCE_REVISION": pkg["fix_revision"], "GOAL_DIGEST": pkg["goal_digest"],
            "PACKAGE_DIGEST": package_digest(t), "ACCEPT_ORACLE_DIGEST": pkg["accept_oracle"]["digest"], "ACCEPT_SPEC_DIGEST": pkg["accept_oracle"]["spec_digest"],
            "HIDDEN_TESTS_DIGEST": pkg["accept_oracle"]["hidden_tests_digest"], "REGRESSION_ORACLE_DIGEST": pkg["regression_oracle"]["digest"],
            "REGRESSION_SET_DIGEST": pkg["regression_oracle"]["set_digest"], "PREFLIGHT_DIGEST": sha_file(t / "evaluator/probe.json"),
            "REFERENCE_PATCH_DIGEST": pkg["reference_fix"]["patch_digest"], "CERTIFICATION_VERDICT": pkg["certification"]["verdict"],
            "CERTIFICATION_LIMITS": pkg["certification"]["limits"], "PUBLIC_PROVENANCE": pkg["provenance"]["pr_url"],
            "DIFFICULTY_ANCHOR": t.name in DIFFICULTY_ANCHORS, "BENCHMARK_OVERLAP_CLASS": worst,
            "BENCHMARK_OVERLAP_PER_DATASET": {k: v["class"] for k, v in ov.items()},
            "AI_ASSISTED_UPSTREAM": pkg["metadata"]["upstream_pr_ai_assisted_per_pr_text"],
            "EVALUATOR_RUNTIME_SECS": {"accept": cost["accept_secs"], "regress": cost["regress_secs"], "ref_accept_plus_regress": cost["ref_good_total_secs"]},
            "METADATA": pkg["metadata"]}
    for p in sorted(FROZEN.rglob("*")):
        rel = str(p.relative_to(FROZEN))
        if (p.is_file() and ".git/" not in str(p) and "/ws/" not in str(p) and "/ref_tree/" not in str(p) and "/cert_logs/" not in str(p)
                and p.name not in ("BANK_MANIFEST.json", "partition.json", "MANIFEST.json")):
            files[rel] = sha_file(p)
    ws = {t: {"ws": sha_tree(FROZEN / t / "ws"), "ref_tree": sha_tree(FROZEN / t / "evaluator/ref_tree")} for t in tasks}
    tools = {p.name: sha_file(p) for p in sorted(TOOLS.glob("*.py"))}
    vendored = {p.name: sha_file(p) for p in sorted((FROZEN / "_tools").glob("*.py"))}
    manifest = {"tasks": tasks, "files": files, "solver_workspaces_tree_digest": ws, "tools": tools, "vendored_evaluator_tools": vendored,
                "difficulty_anchors": sorted(DIFFICULTY_ANCHORS)}
    manifest["bank_manifest_hash"] = sha_bytes(json.dumps(manifest, sort_keys=True).encode())
    Path(out or FROZEN / "BANK_MANIFEST.json").write_text(json.dumps(manifest, indent=1))
    print(manifest["bank_manifest_hash"], len(tasks), "tasks", len(files), "files")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "finalize", "leakscan", "partition", "manifest"])
    ap.add_argument("--ids")
    ap.add_argument("--seed", default="MODEL-ROUTING-V1/stage2/partition/1")
    ap.add_argument("--out")
    a = ap.parse_args()
    if a.cmd == "build":
        build(a.ids)
    elif a.cmd == "finalize":
        finalize()
    elif a.cmd == "leakscan":
        res = [leakscan(t.name) for t in sorted(FROZEN.iterdir()) if (t / "task.json").exists()]
        Path(a.out or FROZEN / "leakscan.json").write_text(json.dumps(res, indent=1))
        for r in res:
            print(r["task"], len(r["findings"]), *r["findings"][:6], sep="\n   " if r["findings"] else " ")
    elif a.cmd == "partition":
        tids = [t.name for t in sorted(FROZEN.iterdir()) if (t / "task.json").exists()]
        res = partition(a.seed, tids)
        Path(a.out or FROZEN / "partition.json").write_text(json.dumps(res, indent=1))
        print(json.dumps(res, indent=1))
    else:
        manifest_cmd(a.out)


if __name__ == "__main__":
    main()
