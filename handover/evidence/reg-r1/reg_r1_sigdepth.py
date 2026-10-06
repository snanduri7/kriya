"""REG-R1 Part 4b (investigation): which per-test signature depth is deterministic on this suite?

For every failing/erroring JUnit case, candidate signature definitions (all after Kriya a049c02's own
normalize_failure_text, the same volatile-token rules level 1 uses):
  S0 id+outcome                      S1 S0 + exception type
  S2 S1 + message                    S3 S2 + crash location (last 'file.py:N:' frame line of the body)
  S4 S3 + every traceback frame line ('file.py:N: in func' / 'file.py:N: Exc')
  S5 S1 + whole failure body
usage: venv-gr1/bin/python reg_r1_sigdepth.py <runs_dir> <out.json>
"""
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

from kriya.workflow.validation_baseline import normalize_failure_text as norm

FRAME = re.compile(r"^\S+\.py:\d+: .*$", re.MULTILINE)
PAIRS = [("BASE-1", "BASE-2"), ("CANDIDATE-1", "CANDIDATE-2"), ("BASE-1", "CANDIDATE-1"), ("BASE-2", "CANDIDATE-2")]


def failing(path):
    out = {}
    for case in ET.parse(path).getroot().iter("testcase"):
        for tag in ("failure", "error"):
            element = case.find(tag)
            if element is None:
                continue
            body = element.text or ""
            frames = FRAME.findall(body)
            base = (f"{case.get('classname')}::{case.get('name')}", tag)
            kind, message = element.get("type"), norm(element.get("message") or "")
            out[base[0]] = {"S0": base, "S1": base + (kind,), "S2": base + (kind, message),
                            "S3": base + (kind, message, norm(frames[-1]) if frames else ""),
                            "S4": base + (kind, message, tuple(norm(f) for f in frames)),
                            "S5": base + (kind, norm(body))}
            break
    return out


def main(runs, out):
    data = {label: failing(os.path.join(runs, f"{label}.junit.xml"))
            for label in ("BASE-1", "BASE-2", "CANDIDATE-1", "CANDIDATE-2")}
    report = {}
    for a, b in PAIRS:
        row = {}
        for level in ("S0", "S1", "S2", "S3", "S4", "S5"):
            changed = sorted(k for k in set(data[a]) & set(data[b]) if data[a][k][level] != data[b][k][level])
            row[level] = changed
        report[f"{a} vs {b}"] = row
        print(f"{a} vs {b}: " + "  ".join(f"{lv}={len(v)}" for lv, v in row.items()))
        for lv, v in row.items():
            for k in v:
                print(f"     {lv} changed: {k}")
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=1)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
