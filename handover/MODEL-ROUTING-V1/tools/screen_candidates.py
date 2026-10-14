#!/usr/bin/env python3
"""Exposure-FIRST screen for replacement candidates (metadata only: no clone, no build, no model call).

Usage: screen_candidates.py [--resume] OUT.json repo#pr [repo#pr ...]      (ids come from bank/logs/pass2_pool.json)

For each candidate it resolves the fix commit's parent (the base SHA) through the GitHub REST API, then compares repository,
PR number (instance_id suffix), base SHA and fix SHA against the six public benchmark datasets using exposure_check's own
matching rules, so the replacement screen and the final-bank exposure table are produced by the same logic. A candidate with
EXACT/MATERIAL TASK OVERLAP is rejected here and is never cloned, built or certified.
Public benchmark task overlap only; model training exposure is not assessed.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import exposure_check as ex  # noqa: E402

POOL = ex.BANK / "logs/pass2_pool.json"


def base_of(slug, sha):
    out = subprocess.run(["gh", "api", f"repos/{slug}/commits/{sha}", "--jq", "[.parents[].sha]|join(\" \")"],
                         capture_output=True, text=True, check=False, timeout=120)
    parents = out.stdout.split()
    return parents[0] if out.returncode == 0 and parents else None


def classify(ds, rows, err, repo, pr, base, fix, files):
    same = [r for r in rows if (r["repo"] or "").lower() == repo.lower()
            or ds.endswith("SWE-smith") and repo.lower().replace("/", "__") in (r["instance_id"] or "").lower()]
    if err and not rows:
        return {"class": "UNKNOWN", "error": err}
    exact = [r["instance_id"] for r in same if re.search(rf"[-_]{pr}$", r["instance_id"] or "")
             or (base and (r["base_commit"] or "").startswith(base[:12])) or fix[:12] in (r["patch"] or "")]
    related = [r["instance_id"] for r in same if files & ex.patch_files(r["patch"]) and not ds.endswith("SWE-smith")]
    cls = "EXACT/MATERIAL TASK OVERLAP" if exact else "POSSIBLE RELATED TASK" if related else "SAME REPOSITORY ONLY" if same else "NO KNOWN OVERLAP"
    return {"class": cls, "same_repo_rows": len(same), "exact": exact[:5], "related_same_file": related[:8], **({"partial_error": err} if err else {})}


def main():
    resume = "--resume" in sys.argv  # keep every definite cell of an earlier run; re-query only UNKNOWN / partially failed cells
    args = [a for a in sys.argv[1:] if a != "--resume"]
    out, ids = args[0], args[1:]
    prev = json.loads(Path(out).read_text()) if resume and Path(out).exists() else {}
    pool = {f"{x['repo'].split('/')[1]}#{x['pr']}": x for x in json.loads(POOL.read_text())}
    cache, res = {}, {}
    for cid in ids:
        cand = pool[cid]
        slug, pr, fix = cand["repo"], str(cand["pr"]), cand["merge_sha"]
        base = base_of(slug, fix)
        files = set(cand["prod"]) | set(cand["tests"])
        per = {}
        for ds in ex.DATASETS:
            old = prev.get(cid, {}).get("per_dataset", {}).get(ds)
            if old and old["class"] != "UNKNOWN" and "partial_error" not in old:
                per[ds] = old
                continue
            if (ds, slug) not in cache:
                cache[(ds, slug)] = ex.rows_for(ds, slug)
            rows, err = cache[(ds, slug)]
            per[ds] = classify(ds, rows, err, slug, pr, base, fix, files)
        worst = max((v["class"] for v in per.values()), key=lambda c: (["NO KNOWN OVERLAP", "SAME REPOSITORY ONLY", "POSSIBLE RELATED TASK", "UNKNOWN", "EXACT/MATERIAL TASK OVERLAP"].index(c)))
        res[cid] = {"repo": slug, "pr": pr, "base": base, "fix": fix, "title": cand["title"], "worst": worst, "per_dataset": per}
        print(cid, worst, {k.split("/")[1]: v["class"][:10] + "(%d)" % v.get("same_repo_rows", 0) for k, v in per.items()}, flush=True)
        Path(out).write_text(json.dumps(res, indent=1))


if __name__ == "__main__":
    sys.exit(main())
