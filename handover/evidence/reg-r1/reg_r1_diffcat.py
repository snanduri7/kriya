"""REG-R1 Part 3: categorize every differing line between two raw gate outputs (no normalization before diffing).

Each differing line pair is assigned the FIRST matching category below (rules were written after reading the
actual differences; anything unmatched is "other" and printed in full). Also reports whether the pair still differs
after Kriya a049c02's own normalize_failure_text (the level-1 input) and which lines survive it.

usage: venv-gr1/bin/python reg_r1_diffcat.py <runs_dir> <out.json>
"""
import difflib
import json
import os
import re
import sys
from collections import Counter

from kriya.workflow.validation_baseline import normalize_failure_text

PAIRS = [("BASE-1", "BASE-2"), ("CANDIDATE-1", "CANDIDATE-2"), ("BASE-1", "CANDIDATE-1"), ("BASE-2", "CANDIDATE-2")]
RULES = (
    ("memory_address (random identifier)", re.compile(r"0x[0-9a-fA-F]{6,}")),
    ("container hostname / env ordering (environment chatter)", re.compile(r"'HOSTNAME': ")),
    ("timing value in assertion text (timing)", re.compile(r"\d+\.\d{4,}|super-linear")),
    ("summary duration (timing)", re.compile(r"^=+ .* in [\d.]+s =+$")),
    ("temporary path", re.compile(r"/tmp/|pytest-of-|/var/folders/")),
    ("progress percentage", re.compile(r"\[\s*\d+%\]$")),
)


def category(old, new):
    for name, pattern in RULES:
        if pattern.search(old) or pattern.search(new):
            return name
    return "other"


def read(runs, label):
    with open(os.path.join(runs, f"{label}.gate_output.txt"), encoding="utf-8", newline="") as handle:
        return handle.read()


def diff_pairs(a, b):
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        olds, news = a[i1:i2], b[j1:j2]
        for k in range(max(len(olds), len(news))):
            yield (i1 + k + 1, olds[k] if k < len(olds) else "", news[k] if k < len(news) else "")


def main(runs, out):
    report = {}
    for x, y in PAIRS:
        a, b = read(runs, x), read(runs, y)
        lines = list(diff_pairs(a.splitlines(), b.splitlines()))
        counts = Counter(category(o, n) for _, o, n in lines)
        na, nb = normalize_failure_text(a).splitlines(), normalize_failure_text(b).splitlines()
        survivors = [{"line": ln, "old": o[:300], "new": n[:300], "category": category(o, n)}
                     for ln, o, n in diff_pairs(na, nb)]
        examples = {}
        for ln, o, n in lines:
            examples.setdefault(category(o, n), {"line": ln, "old": o[:240], "new": n[:240]})
        report[f"{x} vs {y}"] = {
            "raw_identical": a == b, "raw_differing_lines": len(lines), "raw_by_category": dict(counts),
            "first_example_by_category": examples,
            "other": [{"line": ln, "old": o, "new": n} for ln, o, n in lines if category(o, n) == "other"],
            "kriya_normalized_identical": normalize_failure_text(a) == normalize_failure_text(b),
            "after_kriya_normalization": survivors,
            "after_kriya_normalization_by_category": dict(Counter(s["category"] for s in survivors)),
        }
        r = report[f"{x} vs {y}"]
        print(f"== {x} vs {y}: raw lines {r['raw_differing_lines']} {r['raw_by_category']}; "
              f"after Kriya normalization {len(survivors)} {r['after_kriya_normalization_by_category']}; "
              f"other={len(r['other'])}")
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=1)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
