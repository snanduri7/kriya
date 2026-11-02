"""PRD-034/PRD-033: freeze the release-threshold configuration a certification is bound to.

    python scripts/certification_thresholds.py THRESHOLDS.yaml OUT.json

Loads the operator's external threshold file through the canonical loader (kriya.metrics.thresholds.load_thresholds, which refuses a file that
cannot be parsed and an unknown shape) and records: the source path, the exact file text and its sha256, the loader's digest, the effective
values the loader produced, the Kriya revision and the interpreter. The certification is bound to these values, not to the file's current
content. Exit 0 when the file loads, 1 otherwise (the record then names the error). The file itself is never modified or moved.

A PASS of these thresholds means "no incident was OBSERVED among the adjudicated runs"; a missing adjudication is INCONCLUSIVE (see the metrics
report's evidence_statement). This script records the thresholds only; it makes no statistical claim.
"""
import hashlib
import json
import os
import platform
import subprocess
import sys


def freeze(path: str) -> dict:
    from kriya.metrics.thresholds import ThresholdConfigError, load_thresholds

    record = {"source_path": os.path.realpath(path), "python": platform.python_version(),
              "kriya_revision": subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False).stdout.strip() or None}
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
        record["file_sha256"] = hashlib.sha256(raw).hexdigest()
        record["file_text"] = raw.decode("utf-8")
        thresholds, digest = load_thresholds(path)
    except (OSError, UnicodeDecodeError, ThresholdConfigError) as error:
        record.update({"status": "FAIL", "error": f"{type(error).__name__}: {error}"})
        return record
    record.update({"status": "RECORDED", "loader_digest": digest,
                   "effective": [{"metric": t.metric, "comparator": t.comparator, "value": t.value, "min_samples": t.min_samples} for t in thresholds]})
    return record


def main(argv) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    record = freeze(argv[1])
    with open(argv[2], "w", encoding="utf-8") as handle:
        json.dump(record, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(f"[thresholds] {record['status']} {record.get('file_sha256', '')[:16]}")
    return 0 if record["status"] == "RECORDED" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
