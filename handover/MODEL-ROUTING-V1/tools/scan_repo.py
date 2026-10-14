"""Experiment-only candidate scanner (Stage 2): lists small real upstream fix commits (1-4 production files + tests) in a repo.
Heuristic only - every hit is read by a human before it enters the pool. No model is involved.
Usage: scan_repo.py REPO_DIR LANG(java|python) SINCE(YYYY-MM-DD) [UNTIL]
"""
import re
import subprocess
import sys

TEST_RE = {"java": re.compile(r"(^|/)src/test/|Test\w*\.java$"), "python": re.compile(r"(^|/)tests?/|(^|/)test_\w+\.py$|_test\.py$")}
SRC_RE = {"java": re.compile(r"\.java$"), "python": re.compile(r"\.py$")}
SUBJ = re.compile(r"\b(fix|fixes|fixed|bug|incorrect|wrong|regression|npe|nullpointer|crash|handle|honou?r|respect|ignore[sd]?|support|allow|throw|raise|broken|invalid|missing|off.by.one|overflow|escape|parse)\b", re.I)


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, check=True).stdout


def main(repo, lang, since, until="2026-12-31"):
    log = git(repo, "log", "--no-merges", f"--since={since}", f"--until={until}", "--format=%H|%ad|%s", "--date=short", "--numstat")
    cur, rows = None, []
    for line in log.splitlines():
        if re.match(r"^[0-9a-f]{40}\|", line):
            cur = {"sha": line[:40], "date": line[41:51], "subj": line[52:], "files": []}
            rows.append(cur)
        elif line.strip() and cur is not None:
            a, d, path = line.split("\t", 2)
            cur["files"].append((0 if a == "-" else int(a), 0 if d == "-" else int(d), path))
    for r in rows:
        tests = [f for f in r["files"] if TEST_RE[lang].search(f[2])]
        srcs = [f for f in r["files"] if SRC_RE[lang].search(f[2]) and not TEST_RE[lang].search(f[2])]
        other_noise = [f for f in r["files"] if f not in tests and f not in srcs]
        churn = sum(a + d for a, d, _ in srcs)
        if 1 <= len(srcs) <= 4 and tests and churn <= 120 and len(other_noise) <= 3 and SUBJ.search(r["subj"]):
            print(f"{r['sha'][:10]} {r['date']} src={len(srcs)}({churn}) tests={len(tests)} | {r['subj'][:90]} | {','.join(p.rsplit('/',1)[-1] for _,_,p in srcs)[:70]}")


if __name__ == "__main__":
    main(*sys.argv[1:])
