"""LR-R1-M1 M-1 offline measurement (harness, not Kriya code).

Times Kriya's own ``attempt._record_candidate_change`` (ceb9943) on a real
large file: the run's candidate diff construction, with ``emit`` replaced by
a recorder of its arguments (construction cost only), and separately the
gzip cost of the blobs that emit would write (level 6, the writer's).
Cases: commons-lang StringUtils.java (402 KB) with a 30-line method inserted
mid-file (a realistic patch), and the same file with every line changed
(worst case for difflib).
usage: venv/bin/python scripts/m1_offline.py
"""
import gzip
import json
import os
import statistics
import tempfile
import time
from types import SimpleNamespace

from kriya.core.attempt_evidence import scope
from kriya.workflow import attempt

D = os.path.expanduser("~/kriya-m1-live")
SOURCE = os.path.join(D, "ws/run2-lang-countwords/src/main/java/org/apache/commons/lang3/StringUtils.java")
REL = "src/main/java/org/apache/commons/lang3/StringUtils.java"


def measure(before: bytes, after: bytes, repeat: int = 5):
    emitted = []
    attempt.attempt_evidence_scope = SimpleNamespace(capture_mode=lambda: "full",
                                                     emit=lambda kind, payload, content=None: emitted.append(content))
    with tempfile.TemporaryDirectory() as worktree:
        path = os.path.join(worktree, REL)
        os.makedirs(os.path.dirname(path))
        with open(path, "wb") as handle:
            handle.write(after)
        state = SimpleNamespace(candidate_digests={REL: "sha256:x"}, all_original_raw={REL: before})
        ctx = SimpleNamespace(worktree_path=worktree)
        samples = []
        for _ in range(repeat):
            emitted.clear()
            started = time.perf_counter()
            attempt._record_candidate_change(state, ctx)
            samples.append(time.perf_counter() - started)
    content = emitted[-1]
    gz = []
    for _ in range(repeat):
        started = time.perf_counter()
        for blob in (content["before"], content["after"], content["diff"].encode("utf-8")):
            gzip.compress(blob, compresslevel=6)
        gz.append(time.perf_counter() - started)
    return {"construction_median_s": round(statistics.median(samples), 4), "construction_max_s": round(max(samples), 4),
            "gzip_blobs_median_s": round(statistics.median(gz), 4), "diff_bytes": len(content["diff"])}


def main():
    before = open(SOURCE, "rb").read()
    lines = before.decode("utf-8").splitlines(keepends=True)
    middle = len(lines) // 2
    method = "".join(f"    // countWords line {i}\n" for i in range(30))
    patched = "".join(lines[:middle] + [method] + lines[middle:]).encode("utf-8")
    rewritten = "".join(line.rstrip("\n") + " \n" for line in lines).encode("utf-8")
    out = {"file": REL, "bytes": len(before), "lines": len(lines),
           "patch_30_lines": measure(before, patched),
           "every_line_changed": measure(before, rewritten),
           "note": "offline; real attempt._record_candidate_change at ceb9943 with emit stubbed; gzip level 6"}
    print(json.dumps(out, indent=1))
    with open(os.path.join(D, "evidence", "m1_offline.json"), "w", encoding="utf-8") as handle:
        json.dump(out, handle, indent=1)


if __name__ == "__main__":
    main()
