"""Offline replay (no model, no network) of the live attempt-6 `diagnosis_mismatch` rejection.

Reads the sealed attempt-evidence records beside this file, takes the model's REAL new pom.xml (record 181) and the pom.xml it
replaced (record 204's `proposed` blob is the OLD pom: the record logs `failed_content=prior_content`), and runs the production
check on them. Run from the repository root with the project interpreter:  .venv/bin/python -I <this file>
"""
import gzip
import hashlib
import json
import re
from pathlib import Path

from kriya.workflow.attribution import find_edits_ignoring_own_diagnosis

BASE = Path(__file__).resolve().parent / "attempt-evidence"


def _records():
    with open(BASE / "records.jsonl", encoding="utf-8") as handle:
        return {record["seq"]: record for record in map(json.loads, handle)}


def _blob(ref):
    digest = ref.split(":", 1)[1]
    return gzip.decompress((BASE / "blobs" / digest[:2] / f"{digest}.gz").read_bytes()).decode("utf-8")


def main():
    records = _records()
    response = _blob(records[181]["blobs"]["content"])
    new_pom = re.search(r'<<<KRIYA:FILE path="pom.xml">>>\n(.*?)<<<KRIYA:END_FILE>>>', response, re.S).group(1)
    old_pom = _blob(records[204]["blobs"]["proposed"])
    analysis = response.split("<<<KRIYA:")[0].strip()
    print("new pom sha", hashlib.sha256(new_pom.encode()).hexdigest()[:12], "old pom sha", hashlib.sha256(old_pom.encode()).hexdigest()[:12])
    verdict = find_edits_ignoring_own_diagnosis(analysis, None, new_pom, old_pom)
    print("RESULT(new pom):", (verdict or "None")[:160])


if __name__ == "__main__":
    main()
