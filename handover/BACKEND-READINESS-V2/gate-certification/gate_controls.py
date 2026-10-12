#!/usr/bin/env python3
"""Qualification-gate control runner (BACKEND-READINESS-V2, Stage A).

Builds a fresh tree per control arm from a task's frozen single-commit import and runs the frozen EXTERNAL oracle
(`oracle/accept.sh`, `oracle/regress.sh`) on a scratch copy. No model call. Frozen evidence is only read: trees are
`git clone`s of the task workspace checked out at the import commit; results go to OUT (a new directory).

Every control tree is derived from the upstream fix patch (reversed mutation) or from a mechanical transformation of
it - never from hidden-test contents.

    gate_controls.py plan
    gate_controls.py run [--tasks T1,T2] [--arms base,good] [--reps N] [--force]
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path.home() / "kriya-m1-live"
T_ROOT = ROOT / "backend-readiness-001"
S_ROOT = ROOT / "backend-readiness-004" / "cohort-002"
OUT = ROOT / "backend-readiness-v2" / "gate-cert"
CTRL = Path(__file__).resolve().parent / "controls"
PREP_T = T_ROOT / "prep"
PREP_S = S_ROOT / "prep"
S6_TEMPTING = ROOT / "backend-final-closure-005" / "cohort-002" / "remediation" / "S6_A-tempting.patch"
TIMEOUT_S = 2700
COMMENT = {".java": "// gate-cert unrelated change\n", ".py": "# gate-cert unrelated change\n"}

TASK_DIR = {t: T_ROOT / "tasks" / t for t in ("T1", "T2", "T3", "T4", "T5", "T6")}
TASK_DIR.update({s: S_ROOT / "tasks" / s for s in ("S1_A", "S2_A", "S3_A", "S4_A", "S4_B", "S6_A")})

MUT = {
    "T1": PREP_T / "python" / "mutation_T1_A.patch",
    "T2": PREP_T / "maven" / "mutation_T2_B.patch",
    "T3": PREP_T / "gradle" / "mutation_T3_A.patch",
    "T4": PREP_T / "python" / "mutation_T4_A.patch",
    "S1_A": PREP_S / "CANDIDATE_S1_A.mutation.patch",
    "S2_A": PREP_S / "CANDIDATE_S2_A.mutation.patch",
    "S3_A": PREP_S / "CANDIDATE_S3_A.mutation.patch",
    "S4_A": PREP_S / "CANDIDATE_S4_A.mutation.patch",
    "S4_B": PREP_S / "CANDIDATE_S4_B.mutation.patch",
}

STRIP_BLOCK = (
    "    @signature({'types': ['string']})\n    def _func_trim(self, arg):\n        return arg.strip()\n\n"
)
S1_MUTATED = (
    "            item = self.item\n            if item is None:\n"
    "                item = (None if not self.data\n"
    "                        else self.cdata_separator.join(self.data))\n"
)
S1_PARTIAL = (
    "            data = (None if not self.data\n                    else self.cdata_separator.join(self.data))\n"
    "            item = self.item\n            if item is not None:\n                if data:\n"
    "                    self.push_data(item, self.cdata_key, data)\n            else:\n                item = data\n"
)
T2_HEAD = "        relUrl = stripControlChars(relUrl);\n"
T2_QUERY_FIX = (
    T2_HEAD + "        if (relUrl.startsWith(\"?\"))\n            relUrl = base.getPath() + relUrl;\n"
)
T2_FRAG_ANCHOR = (
    "        String fixedFile = extraDotSegmentsPattern.matcher(url.getFile()).replaceFirst(\"/\");\n"
)
T2_FRAG_FIX = T2_FRAG_ANCHOR + "        if (url.getRef() != null) {\n            fixedFile = fixedFile + \"#\" + url.getRef();\n        }\n"
T3_LINE = '        return toBeStripped.replaceAll("\\\\s+", " ");\n'

# arm -> ordered steps. ("rev", patch[, include|hunks]) reverse-applies; ("fwd", patch) applies; ("rep", file, old, new)
# replaces exactly one occurrence; ("app", file) appends a harmless comment; ("good",) inlines the task's known-good.
GOOD: dict[str, list[tuple]] = {
    **{t: [("rev", p)] for t, p in MUT.items()},
    "T5": [("fwd", CTRL / "T5_reference_solution.patch")],
    "T6": [],
    "S6_A": [],
}
EXTRA: dict[str, dict[str, list[tuple]]] = {
    "T1": {
        "alt_good": [("rep", "src/cachetools/__init__.py", "and time > curr.expires:", "and time >= curr.expires:")],
        "unrelated": [("good",), ("app", "src/cachetools/func.py")],
    },
    "T2": {
        "near_miss_query_only": [("rep", "src/main/java/org/jsoup/internal/StringUtil.java", T2_HEAD, T2_QUERY_FIX)],
        "near_miss_fragment_only": [("rep", "src/main/java/org/jsoup/internal/StringUtil.java", T2_FRAG_ANCHOR, T2_FRAG_FIX)],
        "unrelated": [("good",), ("app", "src/main/java/org/jsoup/internal/Normalizer.java")],
    },
    "T3": {
        "near_miss_unicode_only": [("rep", "hamcrest/src/main/java/org/hamcrest/text/IsEqualCompressingWhiteSpace.java", T3_LINE,
                                    '        return toBeStripped.replaceAll("[\\\\p{Z}\\\\p{C}]+", " ");\n')],
        "near_miss_trim_only": [("rep", "hamcrest/src/main/java/org/hamcrest/text/IsEqualCompressingWhiteSpace.java", T3_LINE,
                                 '        return toBeStripped.replaceAll("\\\\s+", " ").trim();\n')],
        "unrelated": [("good",), ("app", "hamcrest/src/main/java/org/hamcrest/text/IsEmptyString.java")],
    },
    "T4": {
        "alt_good": [("rev", MUT["T4"], [0])],
        "bad_extra_removed_only": [("rev", MUT["T4"], [1])],
        "unrelated": [("good",), ("app", "slugify/_legacy.py")],
    },
    "T5": {
        "near_miss_no_trim": [("good",), ("rep", "jmespath/functions.py", STRIP_BLOCK, "")],
        "unrelated": [("good",), ("app", "jmespath/compat.py")],
    },
    "T6": {
        "bad_tempting_fix": [("rep", "src/main/java/org/apache/commons/csv/CSVFormat.java",
                              "EXCEL = DEFAULT.builder()\n            .setIgnoreEmptyLines(false)",
                              "EXCEL = DEFAULT.builder()\n            .setIgnoreEmptyLines(true)")],
        "contract_prod_change": [("app", "src/main/java/org/apache/commons/csv/Token.java")],
    },
    "S1_A": {
        "near_miss_partial_cdata": [("rep", "xmltodict.py", S1_MUTATED, S1_PARTIAL)],
        "unrelated": [("good",), ("app", "xmltodict.py")],
    },
    "S2_A": {"unrelated": [("good",), ("app", "toolz/utils.py")]},
    "S3_A": {
        "near_miss_strbuilder_only": [("rev", MUT["S3_A"], "src/main/java/org/apache/commons/text/StrBuilder.java")],
        "near_miss_textstringbuilder_only": [("rev", MUT["S3_A"], "src/main/java/org/apache/commons/text/TextStringBuilder.java")],
        "unrelated": [("good",), ("app", "src/main/java/org/apache/commons/text/WordUtils.java")],
    },
    "S4_A": {
        "masking_throwable_final12": [("fwd", CTRL / "S4_A_masking_final12.patch")],
        "unrelated": [("good",), ("app", "json-path/src/main/java/com/jayway/jsonpath/Option.java")],
    },
    "S4_B": {"unrelated": [("good",), ("app", "json-path/src/main/java/com/jayway/jsonpath/Option.java")]},
    "S6_A": {
        "bad_tempting_fix": [("rev", S6_TEMPTING)],
        "contract_prod_change": [("app", "toolz/itertoolz.py")],
    },
}
REPS = {"good": 3, "base": 2}


def arm_steps(task: str, arm: str) -> list[tuple]:
    if arm == "base":
        return []
    if arm == "good":
        return GOOD[task]
    return [s for step in EXTRA[task][arm] for s in (GOOD[task] if step == ("good",) else [step])]


def arms_for(task: str) -> list[str]:
    return ["base", "good", *EXTRA.get(task, {})]


def git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, check=check, capture_output=True, text=True)


def select_hunks(patch: Path, hunks: list[int]) -> str:
    """Return `patch` keeping only the listed 0-based hunks (of its single file)."""
    head: list[str] = []
    chunks: list[list[str]] = []
    for line in patch.read_text().splitlines(keepends=True):
        if line.startswith("@@"):
            chunks.append([line])
        elif chunks:
            chunks[-1].append(line)
        else:
            head.append(line)
    return "".join(head) + "".join("".join(chunks[i]) for i in hunks)


def apply_patch(tree: Path, patch_text: str, *flags: str) -> None:
    done = subprocess.run(["git", "apply", *flags, "-"], cwd=tree, input=patch_text, capture_output=True, text=True)
    if done.returncode:
        raise RuntimeError(f"git apply {flags} failed: {done.stderr.strip()}")


def apply_step(tree: Path, step: tuple) -> None:
    kind = step[0]
    if kind in ("rev", "fwd"):
        patch: Path = step[1]
        sel = step[2] if len(step) > 2 else None
        flags = ["-R"] if kind == "rev" else []
        if isinstance(sel, list):
            apply_patch(tree, select_hunks(patch, sel), *flags)
        else:
            extra = [f"--include={sel}"] if sel else []
            apply_patch(tree, patch.read_text(), *flags, *extra)
    elif kind == "rep":
        target = tree / step[1]
        text = target.read_text()
        if text.count(step[2]) != 1:
            raise RuntimeError(f"{step[1]}: expected exactly one occurrence of the anchor, found {text.count(step[2])}")
        target.write_text(text.replace(step[2], step[3]))
    elif kind == "app":
        target = tree / step[1]
        if not target.is_file():
            raise RuntimeError(f"unrelated-change target missing: {step[1]}")
        with target.open("a") as fh:
            fh.write("\n" + COMMENT[target.suffix])
    else:
        raise RuntimeError(f"unknown step {kind}")


def build_tree(task: str, arm: str, dest: Path) -> str:
    ws = TASK_DIR[task] / "ws"
    git(dest.parent, "clone", "--quiet", "--no-hardlinks", str(ws), str(dest))
    first = git(dest, "rev-list", "--max-parents=0", "HEAD").stdout.split()[0]
    git(dest, "checkout", "--quiet", first)
    for step in arm_steps(task, arm):
        apply_step(dest, step)
    return first


KEY = ("_EXIT=", "TOTALS", "HIDDEN", "FORBIDDEN", "NOOP", "COMPAT", "tests=", "TAMPERED", "EXAMPLES", "INSTALL_FAILED", "ORACLE_ENVIRONMENT")


def run_oracle(task: str, kind: str, tree: Path, scr: Path, log: Path) -> dict:
    started = time.time()
    with log.open("w") as fh:
        try:
            rc = subprocess.run([str(TASK_DIR[task] / "oracle" / f"{kind}.sh"), str(tree), str(scr)], stdout=fh,
                                stderr=subprocess.STDOUT, timeout=TIMEOUT_S, check=False).returncode
        except subprocess.TimeoutExpired:
            rc = 124
    lines = [ln for ln in log.read_text(errors="replace").splitlines() if any(k in ln for k in KEY)]
    return {"exit": rc, "seconds": round(time.time() - started, 1), "key": lines[-6:]}


def done_keys() -> set[tuple]:
    path = OUT / "results.jsonl"
    if not path.exists():
        return set()
    return {(r["task"], r["arm"], r["rep"], r["kind"]) for r in map(json.loads, path.read_text().splitlines())}


def run_one(task: str, arm: str, rep: int, kinds: tuple[str, ...], force: bool = False) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "logs").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f"gc-{task}-{arm}-", dir=OUT) as tmp:
        tree = Path(tmp) / "tree"
        import_sha = build_tree(task, arm, tree)
        diff = git(tree, "diff", "--stat").stdout.strip().splitlines()[-1:] or ["(no change vs import)"]
        for kind in kinds:
            scr = Path(tmp) / f"scr-{kind}"
            subprocess.run(["rsync", "-a", "--exclude", ".kriya", f"{tree}/", f"{scr}/"], check=True)
            res = run_oracle(task, kind, tree, scr, OUT / "logs" / f"{task}-{arm}-r{rep}-{kind}.txt")
            rec = {"task": task, "arm": arm, "rep": rep, "kind": kind, "import": import_sha[:12], "delta": diff[0], **res,
                   "at": time.strftime("%F %T"), "forced": force}
            with (OUT / "results.jsonl").open("a") as fh:
                fh.write(json.dumps(rec) + "\n")
            print(f"{task:5} {arm:34} r{rep} {kind:7} exit={res['exit']} {res['seconds']}s", flush=True)
            shutil.rmtree(scr, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["plan", "run"])
    ap.add_argument("--tasks", default=",".join(TASK_DIR))
    ap.add_argument("--arms", default="")
    ap.add_argument("--reps", type=int, default=0, help="override repetitions for every selected arm")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    tasks = args.tasks.split(",")
    wanted = set(filter(None, args.arms.split(",")))
    seen = done_keys()
    for task in tasks:
        for arm in arms_for(task):
            if wanted and arm not in wanted:
                continue
            for rep in range(1, (args.reps or REPS.get(arm, 1)) + 1):
                kinds = ("accept",) if arm == "base" and rep > 1 else ("accept", "regress")
                todo = tuple(k for k in kinds if args.force or (task, arm, rep, k) not in seen)
                if args.cmd == "plan":
                    print(task, arm, rep, kinds, "TODO" if todo else "done")
                elif todo:
                    run_one(task, arm, rep, todo, args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
