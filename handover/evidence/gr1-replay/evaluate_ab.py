"""Parse each frozen replay response with Kriya's real Developer parser (a049c02) and, when it yields edits, apply
them with Kriya's own edit engine to a copy of the frozen base engine.py. Never touches a real workspace.

usage: <kriya a049c02 python> evaluate_ab.py <run_dir> <base_engine.py>
Writes candidate_<arm>.py / candidate_<arm>.diff / parse_<arm>.json into run_dir."""
import difflib
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

from kriya.agents.response_protocol import parse_structured
from kriya.workflow.file_integrity import FileIntegrityError, load_snapshot, mutate_snapshot

PATH = "graphify/extractors/engine.py"
# Windows shown in the sealed attempt-2 request (lines, inclusive); B adds 215-232.
WINDOWS = {"A": [(5356, 5370), (5385, 5413), (5478, 5479), (5522, 5523), (6268, 6275)]}
WINDOWS["B"] = WINDOWS["A"] + [(215, 232)]


def search_lines(base_lines, search):
    """1-based line spans where the SEARCH block occurs exactly (complete lines)."""
    block = search.split("\n")
    if block and block[-1] == "":
        block = block[:-1]
    width = len(block)
    return [(i + 1, i + width) for i in range(len(base_lines) - width + 1) if base_lines[i:i + width] == block]


def main(run_dir, base_engine):
    run = Path(run_dir)
    base_text = Path(base_engine).read_text(encoding="utf-8")
    base_lines = base_text.split("\n")
    for arm in ("A", "B"):
        raw = (run / f"response_{arm}.txt").read_text(encoding="utf-8")
        parsed = parse_structured(raw, PATH, patch_allowed=True)
        record = {"arm": arm, "kind": parsed.kind, "reason_code": getattr(parsed, "reason_code", None),
                  "detail": getattr(parsed, "detail", None)}
        edits = parsed.edit_dicts() if parsed.kind == "edits" else []
        record["edit_count"] = len(edits)
        spans = []
        for edit in edits:
            found = search_lines(base_lines, edit["search"])
            inside = bool(found) and all(any(lo <= s and e <= hi for lo, hi in WINDOWS[arm]) for s, e in found)
            spans.append({"search_spans": found, "inside_shown_windows": inside,
                          "no_op": edit["search"] == edit["replace"]})
        record["edits"] = spans
        if edits:
            with tempfile.TemporaryDirectory() as scratch:
                target = Path(scratch) / "engine.py"
                shutil.copyfile(base_engine, target)
                try:
                    text, data = mutate_snapshot(load_snapshot(str(target)), edits)
                    (run / f"candidate_{arm}.py").write_bytes(data)
                    diff = "".join(difflib.unified_diff(base_text.splitlines(True), text.splitlines(True),
                                                        f"a/{PATH}", f"b/{PATH}"))
                    (run / f"candidate_{arm}.diff").write_text(diff, encoding="utf-8")
                    record.update(applicable=True, candidate_sha256=hashlib.sha256(data).hexdigest(),
                                  changed=text != base_text)
                except FileIntegrityError as error:
                    record.update(applicable=False, apply_error=str(error))
        (run / f"parse_{arm}.json").write_text(json.dumps(record, indent=1, default=str))
        print(json.dumps(record, default=str))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
