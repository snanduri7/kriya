#!/usr/bin/env python3
"""Pass-1 candidate discovery: GitHub GraphQL metadata only, no clones.

For each OWNER/REPO, page through merged PRs (newest first) and keep the ones that
look like a compact real fix: 1-4 production files, >=1 test file, small churn,
a linked closing issue or a fix-like title. Output is a JSON ledger consumed by the
paper-screen pass. Experiment-only tooling; does not touch Kriya.

Usage: discover_prs.py OUT.json SINCE_ISO OWNER/REPO [OWNER/REPO ...]
"""
import json
import re
import subprocess
import sys

QUERY = """
query($o:String!,$n:String!,$after:String){
  repository(owner:$o,name:$n){
    primaryLanguage{name}
    pullRequests(states:MERGED,first:50,after:$after,orderBy:{field:UPDATED_AT,direction:DESC}){
      pageInfo{hasNextPage endCursor}
      nodes{number title url mergedAt additions deletions changedFiles
        mergeCommit{oid} baseRefName
        closingIssuesReferences(first:3){nodes{number title url createdAt}}
        files(first:12){nodes{path additions deletions}}}}}}
"""
TEST_RE = re.compile(r"(^|/)(tests?|testing|src/test)/|(^|/)test_[^/]*\.py$|_test\.py$|Test[s]?\.java$|Tests?/")
NOISE_RE = re.compile(r"(^|/)(docs?|\.github|CHANGES|CHANGELOG|RELEASE|README|NOTICE)|\.(md|txt|rst|lock|png|svg)$|pom\.xml$|build\.gradle|gradle\.properties", re.I)
FIX_RE = re.compile(r"\b(fix|fixes|fixed|bug|incorrect|wrong|regression|support|add|handle|allow|prevent|avoid|missing)\b", re.I)
MAX_PAGES = 6
CONFIG_MODE = bool(__import__("os").environ.get("DISCOVER_CONFIG_MODE"))  # keep build/packaging files as production files
CFG_RE = re.compile(r"(^|/)(pyproject\.toml|setup\.py|setup\.cfg|MANIFEST\.in|pom\.xml|build\.gradle(\.kts)?|settings\.gradle(\.kts)?|gradle\.properties)$")


def gql(owner, name, after):
    args = ["gh", "api", "graphql", "-f", f"query={QUERY}", "-F", f"o={owner}", "-F", f"n={name}"]
    if after:
        args += ["-F", f"after={after}"]
    out = subprocess.run(args, capture_output=True, text=True, timeout=120, check=False)
    if out.returncode:
        raise RuntimeError(out.stderr.strip()[:300])
    return json.loads(out.stdout)["data"]["repository"]


def classify(pr):
    files = [f["path"] for f in pr["files"]["nodes"]]
    if pr["changedFiles"] > len(files):
        return None  # truncated file list: too large to be compact
    tests = [f for f in files if TEST_RE.search(f)]
    prod = [f for f in files if f not in tests and (not NOISE_RE.search(f) or (CONFIG_MODE and CFG_RE.search(f)))]
    if CONFIG_MODE and not any(CFG_RE.search(f) for f in prod):
        return None
    other = [f for f in files if f not in tests and f not in prod]
    churn = pr["additions"] + pr["deletions"]
    if not (1 <= len(prod) <= 4 and tests and len(other) <= 3 and churn <= 250):
        return None
    issues = pr["closingIssuesReferences"]["nodes"]
    if not (issues or FIX_RE.search(pr["title"])):
        return None
    return {"prod": prod, "tests": tests, "other": other, "churn": churn, "issues": issues}


def scan(slug, since):
    owner, name = slug.split("/")
    after, found, seen = None, [], 0
    for _ in range(MAX_PAGES):
        repo = gql(owner, name, after)
        prs = repo["pullRequests"]
        for pr in prs["nodes"]:
            seen += 1
            if pr["mergedAt"] < since or not pr["mergeCommit"]:
                continue
            c = classify(pr)
            if c:
                found.append({"repo": slug, "lang": (repo["primaryLanguage"] or {}).get("name"),
                              "pr": pr["number"], "title": pr["title"], "url": pr["url"],
                              "mergedAt": pr["mergedAt"], "merge_sha": pr["mergeCommit"]["oid"],
                              "base_ref": pr["baseRefName"], **c})
        if not prs["pageInfo"]["hasNextPage"]:
            break
        after = prs["pageInfo"]["endCursor"]
    return seen, found


def main():
    out, since, slugs = sys.argv[1], sys.argv[2], sys.argv[3:]
    ledger = []
    for slug in slugs:
        try:
            seen, found = scan(slug, since)
        except (RuntimeError, subprocess.TimeoutExpired, KeyError) as exc:
            print(f"{slug}: ERROR {exc}", file=sys.stderr)
            continue
        print(f"{slug}: scanned {seen} merged PRs, {len(found)} plausible")
        ledger += found
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(ledger, fh, indent=1)
    print(f"total {len(ledger)} -> {out}")


if __name__ == "__main__":
    main()
