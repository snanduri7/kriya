#!/usr/bin/env python3
"""Targeted public-benchmark task-overlap check (metadata only; no model calls).

For each frozen task and each public benchmark dataset (Hugging Face datasets-server /filter API) list rows of the same repository and
compare PR number (instance_id suffix), base commit, fix commit and the set of files touched by the patch. Classes:
  EXACT/MATERIAL TASK OVERLAP : same repo and (same PR number or same base commit or fix sha appears)
  POSSIBLE RELATED TASK       : same repo and the benchmark patch touches a file the reference patch touches
  SAME REPOSITORY ONLY        : same repo present, nothing above
  NO KNOWN OVERLAP            : repo absent from the dataset
  UNKNOWN                     : query failed
Training exposure of any model is NOT assessed by this check.
"""
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

BANK = Path.home() / "kriya-m1-live/model-routing-v1/bank"
DATASETS = ["nebius/SWE-rebench", "nebius/SWE-rebench-V2", "nebius/SWE-bench-extra", "SWE-Gym/SWE-Gym", "SWE-bench/SWE-bench", "SWE-bench/SWE-smith"]
COLS = ("instance_id", "repo", "base_commit", "patch")
MAX_PAGES = {"SWE-bench/SWE-smith": 3}  # synthetic bugs: repo presence is all that is meaningful


def get(url, tries=8):
    """The datasets-server answers HTTP 500 while an index is (re)building; retry with growing back-off, then report UNKNOWN."""
    err = "unknown"
    for i in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=120) as r:
                return json.load(r)
        except Exception as exc:  # network/server error
            err = str(exc)[:160]
            time.sleep(min(10 + 15 * i, 75))
    return {"error": err}


def rows_for(ds, repo):
    name = repo.split("/")[1]
    out, errs = [], None
    for op, pat in (("ILIKE", repo if not ds.endswith("SWE-smith") else "swesmith/" + repo.replace("/", "__") + ".%"),):
        for page in range(MAX_PAGES.get(ds, 60)):
            where = f'"repo" {op} \'{pat}\''
            url = (f"https://datasets-server.huggingface.co/filter?dataset={urllib.parse.quote(ds)}&config=default&split={SPLIT[ds]}"
                   f"&where={urllib.parse.quote(where)}&offset={page * 100}&length=100")
            d = get(url)
            if "error" in d or "rows" not in d:
                return out, d.get("error", "no rows key: " + str(d)[:120])
            out += [{c: r["row"].get(c) for c in COLS} for r in d["rows"]]
            if len(d["rows"]) < 100:
                break
    return out, errs


SPLIT = {"nebius/SWE-rebench": "test", "nebius/SWE-rebench-V2": "train", "nebius/SWE-bench-extra": "train", "SWE-Gym/SWE-Gym": "train",
         "SWE-bench/SWE-bench": "test", "SWE-bench/SWE-smith": "train"}


def patch_files(p):
    return set(re.findall(r"^diff --git a/(\S+)", p or "", re.M))


def main():
    ids = json.loads((BANK / "final_ids.json").read_text())
    prev = {}
    if "--resume" in sys.argv:  # keep every cell that already has a definite class; re-query only UNKNOWN cells
        prev = json.loads((BANK / "exposure/overlap.json").read_text())
    res = {}
    cache = {}
    for tid in sorted(ids):
        src = json.loads((BANK / "frozen" / tid / "task_source.json").read_text())["meta"]
        repo, pr, base, fix = src["repo"], str(src["pr"]), src["base"], src["fix"]
        ref_files = patch_files((BANK / "frozen" / tid / "evaluator/ref_prod.patch").read_text())
        per = {}
        for ds in DATASETS:
            old = prev.get(tid, {}).get("per_dataset", {}).get(ds)
            if old and old["class"] != "UNKNOWN" and "partial_error" not in old:
                per[ds] = old
                continue
            if (ds, repo) not in cache:
                cache[(ds, repo)] = rows_for(ds, repo)
            rows, err = cache[(ds, repo)]
            same = [r for r in rows if (r["repo"] or "").lower().replace("swesmith/", "").replace("__", "/").startswith(repo.lower()) or r["repo"] and r["repo"].lower() == repo.lower()
                    or ds.endswith("SWE-smith") and repo.lower().replace("/", "__") in (r["instance_id"] or "").lower()]
            if err and not rows:
                per[ds] = {"class": "UNKNOWN", "error": err}
                continue
            exact = [r["instance_id"] for r in same if re.search(rf"[-_]{pr}$", r["instance_id"] or "") or (r["base_commit"] or "").startswith(base[:12]) or fix[:12] in (r["patch"] or "")]
            related = [r["instance_id"] for r in same if ref_files & patch_files(r["patch"]) and not ds.endswith("SWE-smith")]
            cls = "EXACT/MATERIAL TASK OVERLAP" if exact else "POSSIBLE RELATED TASK" if related else "SAME REPOSITORY ONLY" if same else "NO KNOWN OVERLAP"
            per[ds] = {"class": cls, "same_repo_rows": len(same), "exact": exact[:5], "related_same_file": related[:8], **({"partial_error": err} if err else {})}
        res[tid] = {"repo": repo, "pr": pr, "base": base, "fix": fix, "per_dataset": per}
        print(tid, repo, {k.split("/")[1]: v["class"][:14] + ("(%d)" % v.get("same_repo_rows", 0)) for k, v in per.items()}, flush=True)
    Path(BANK / "exposure/overlap.json").write_text(json.dumps(res, indent=1))


if __name__ == "__main__":
    sys.exit(main())
