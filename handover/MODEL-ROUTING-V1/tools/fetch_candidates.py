#!/usr/bin/env python3
"""Pass-3 helper: materialise shortlisted fix commits as ordinary (non-bare) git repos.

Per candidate: shallow-fetch only the PR merge commit (depth 2 => its parent(s)), derive
BASE = merge^1 and FIX = merge, check the tree diff file list equals the PR file list
(guards against rebase-merges where the merge commit is only the last PR commit), and
pull PR + linked-issue text via the GitHub REST API (evaluator-only provenance record).

Usage: fetch_candidates.py POOL.json REPOS_DIR OUT_DIR repo#pr [repo#pr ...]
"""
import json
import os
import subprocess
import sys


def run(args, cwd=None):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=False, timeout=600)


def gh_json(path):
    out = run(["gh", "api", path])
    return json.loads(out.stdout) if out.returncode == 0 else {}


def main():
    pool_path, repos_dir, out_dir, ids = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4:]
    pool = {f"{x['repo'].split('/')[1]}#{x['pr']}": x for x in json.load(open(pool_path, encoding="utf-8"))}
    os.makedirs(out_dir, exist_ok=True)
    for cid in ids:
        cand = pool.get(cid)
        if cand is None:
            print(f"{cid}: not in pool")
            continue
        slug = cand["repo"]
        rdir = os.path.join(repos_dir, slug.replace("/", "_"))
        if not os.path.isdir(os.path.join(rdir, ".git")):
            os.makedirs(rdir, exist_ok=True)
            run(["git", "init", "-q"], rdir)
            run(["git", "remote", "add", "origin", f"https://github.com/{slug}.git"], rdir)
        sha = cand["merge_sha"]
        fetched = run(["git", "fetch", "-q", "--depth", "2", "origin", sha], rdir)
        if fetched.returncode:
            print(f"{cid}: FETCH FAILED {fetched.stderr.strip()[:120]}")
            continue
        parents = run(["git", "rev-list", "--parents", "-n", "1", sha], rdir).stdout.split()[1:]
        if not parents:
            print(f"{cid}: no parent")
            continue
        base = parents[0]
        diff = run(["git", "diff", "--name-only", base, sha], rdir).stdout.split()
        want = sorted(cand["prod"] + cand["tests"] + cand["other"])
        same = sorted(diff) == want
        pr = gh_json(f"repos/{slug}/pulls/{cand['pr']}")
        issues = [gh_json(f"repos/{slug}/issues/{i['number']}") for i in cand["issues"]]
        rec = {**cand, "id": cid, "base_sha": base, "fix_sha": sha, "n_parents": len(parents),
               "diff_matches_pr_files": same, "diff_files": diff,
               "pr_body": (pr.get("body") or "")[:4000], "pr_created": pr.get("created_at"),
               "issues_full": [{"number": i.get("number"), "title": i.get("title"),
                                "created_at": i.get("created_at"), "body": (i.get("body") or "")[:4000]}
                               for i in issues]}
        with open(os.path.join(out_dir, cid.replace("#", "_") + ".json"), "w", encoding="utf-8") as fh:
            json.dump(rec, fh, indent=1)
        print(f"{cid}: base={base[:9]} fix={sha[:9]} parents={len(parents)} diff==pr_files={same}")


if __name__ == "__main__":
    main()
