"""REG-R1 Part 4 (investigation artifact, not production code): per-test failure signatures from the preserved
per-invocation JUnit XML of each run.

Signature = (node id, outcome, failure/error type, normalized message, normalized traceback body). The investigative
normalization replaces only tokens shown volatile by Part 3 (listed in VOLATILE); everything else is kept verbatim.
Same test id is never treated as the same failure: identity and signature are compared separately.

usage: python reg_r1_signatures.py <runs_dir> <out_dir> <strict|timing>
"""
import hashlib
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

VOLATILE = (
    (re.compile(r"/private/var/folders/\S+?(?=[\s'\"),\]]|$)"), "<TMP>"),
    (re.compile(r"/var/folders/\S+?(?=[\s'\"),\]]|$)"), "<TMP>"),
    (re.compile(r"/tmp/\S+?(?=[\s'\"),\]]|$)"), "<TMP>"),
    (re.compile(r"/kriya/tmp\S*?(?=[\s'\"),\]]|$)"), "<TMP>"),
    (re.compile(r"\bpytest-\d+\b"), "pytest-<N>"),
    (re.compile(r"\bpopen-gw\d+\b"), "popen-gw<N>"),
    (re.compile(r"0x[0-9a-fA-F]{6,}"), "0x<ADDR>"),
    (re.compile(r"'HOSTNAME': '[0-9a-f]{12}'"), "'HOSTNAME': '<CONTAINER>'"),
)
# Investigative only (Part 3: the one timing-asserting test's numbers vary run to run). Applied in the second,
# "timing-normalized" level, never in the strict level, so its effect stays visible.
TIMING = ((re.compile(r"\b\d+\.\d+(?:s|x)?\b"), "<NUM>"),)
TIMING_LEVEL = False
PAIRS = [("BASE-1", "BASE-2"), ("CANDIDATE-1", "CANDIDATE-2"), ("BASE-1", "CANDIDATE-1"), ("BASE-2", "CANDIDATE-2")]


def normalize(text, timing=False):
    text = text or ""
    for pattern, replacement in VOLATILE + (TIMING if timing else ()):
        text = pattern.sub(replacement, text)
    return text


def cases(path):
    result = {}
    for case in ET.parse(path).getroot().iter("testcase"):
        node = f"{case.get('classname')}::{case.get('name')}"
        outcome, kind, message, body = "passed", None, "", ""
        for tag in ("failure", "error", "skipped"):
            element = case.find(tag)
            if element is not None:
                outcome = {"failure": "failed", "error": "error", "skipped": "skipped"}[tag]
                kind, message, body = element.get("type"), element.get("message") or "", element.text or ""
                break
        raw = {"outcome": outcome, "type": kind, "message": message, "body": body}
        norm = {"outcome": outcome, "type": kind, "message": normalize(message, TIMING_LEVEL),
                "body": normalize(body, TIMING_LEVEL)}
        result[node] = {"raw": raw, "norm": norm,
                        "raw_sig": hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest(),
                        "norm_sig": hashlib.sha256(json.dumps(norm, sort_keys=True).encode()).hexdigest()}
    return result


def compare(pre, post):
    failing = {"failed", "error"}
    pre_fail = {k for k, v in pre.items() if v["norm"]["outcome"] in failing}
    report = {"pre_failures": len(pre_fail), "identical_signature": [], "identical_raw_signature": 0,
              "changed_signature": [], "new_failure": [], "missing_test": sorted(set(pre) - set(post)),
              "added_test": sorted(set(post) - set(pre)), "outcome_transition": []}
    for node in sorted(set(pre) & set(post)):
        a, b = pre[node]["norm"], post[node]["norm"]
        if a["outcome"] != b["outcome"]:
            report["outcome_transition"].append(f"{node}: {a['outcome']} -> {b['outcome']}")
            if b["outcome"] in failing and a["outcome"] not in failing:
                report["new_failure"].append(node)
            continue
        if a["outcome"] in failing:
            if pre[node]["norm_sig"] == post[node]["norm_sig"]:
                report["identical_signature"].append(node)
                report["identical_raw_signature"] += pre[node]["raw_sig"] == post[node]["raw_sig"]
            else:
                report["changed_signature"].append(node)
    return report


def main(runs, out, level="strict"):
    global TIMING_LEVEL
    TIMING_LEVEL = level == "timing"
    data = {label: cases(os.path.join(runs, f"{label}.junit.xml"))
            for label in ("BASE-1", "BASE-2", "CANDIDATE-1", "CANDIDATE-2")}
    for label, entries in data.items():
        with open(os.path.join(out, f"{label}.signatures.{level}.json"), "w", encoding="utf-8") as handle:
            json.dump({k: {"norm": v["norm"], "norm_sig": v["norm_sig"], "raw_sig": v["raw_sig"]}
                       for k, v in entries.items()}, handle, indent=1, sort_keys=True)
    summary = {}
    for a, b in PAIRS:
        r = compare(data[a], data[b])
        summary[f"{a} vs {b}"] = r
        print(f"{a} vs {b}: pre_failures={r['pre_failures']} identical={len(r['identical_signature'])} "
              f"(raw-identical {r['identical_raw_signature']}) changed={len(r['changed_signature'])} "
              f"new={len(r['new_failure'])} missing={len(r['missing_test'])} added={len(r['added_test'])} "
              f"transitions={len(r['outcome_transition'])}")
        for node in r["changed_signature"]:
            print("   CHANGED", node)
        for line in r["outcome_transition"]:
            print("   TRANSITION", line)
    with open(os.path.join(out, f"signature_comparison.{level}.json"), "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=1)


if __name__ == "__main__":
    main(*sys.argv[1:4])
