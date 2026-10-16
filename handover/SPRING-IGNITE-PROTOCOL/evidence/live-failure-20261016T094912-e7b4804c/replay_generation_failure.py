"""Model-free replay of the RECORDED Developer sequence of live run 20261016T094912-e7b4804c (scenario 04) through Kriya's own response parser and edit engine.

For every recorded Developer response of attempts 2-4 it reports: how Kriya's parser classified it, what Kriya's edit engine does with it against the file content of that moment and the source the
model was shown, and whether the resulting candidate compiles (javac, against the recorded attempt-1 Protocol.java). It shows which proposals were correct and which transitions discarded them.

Usage: python replay_generation_failure.py <repo root> <evidence dir (.../scenario-04)>   (needs javac on PATH; no model, no network)
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(sys.argv[1])
EVIDENCE = Path(sys.argv[2])
sys.path.insert(0, str(REPO))
from kriya.agents.response_protocol import parse_structured  # noqa: E402
from kriya.workflow.edit_safety import apply_anchored_edits  # noqa: E402
from kriya.workflow.file_integrity import FileIntegrityError  # noqa: E402

EXTRACTED = EVIDENCE / "extracted"
PROTOCOL = "src/main/java/com/example/protocol/Protocol.java"
PARSER = "src/main/java/com/example/protocol/ProtocolParser.java"


def read(prefix: str) -> str:
    return next(EXTRACTED.glob(prefix)).read_text(encoding="utf-8")


def request_text(call: int) -> str:
    messages = json.loads(read(f"*model.request-call{call}-messages.txt"))
    return "\n".join(m["content"] for m in messages)


SOURCES = {PROTOCOL: read("059-candidate.change-callNone-after.txt"), PARSER: read("060-candidate.change-callNone-after.txt")}  # the attempt-1 candidate, as staged


def javac_errors(parser_source: str) -> list:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp, "com", "example", "protocol")
        root.mkdir(parents=True)
        (root / "Protocol.java").write_text(SOURCES[PROTOCOL], encoding="utf-8")
        (root / "ProtocolParser.java").write_text(parser_source, encoding="utf-8")
        out = subprocess.run(["javac", "-d", str(Path(tmp, "out")), str(root / "Protocol.java"), str(root / "ProtocolParser.java")], capture_output=True, text=True, check=False)
        return [line for line in out.stderr.splitlines() if "error:" in line]


def classify(call: int, path: str) -> dict:
    response = read(f"*model.response-call{call}-content.txt")
    parsed = parse_structured(response, path)
    report = {"call": call, "path": path, "parse_kind": parsed.kind if hasattr(parsed, "kind") else None, "parse_reason_code": getattr(parsed, "reason_code", None)}
    edits = getattr(parsed, "edits", None)
    if edits:
        pairs = [{"search": s, "replace": r} for s, r in edits]
        try:
            patched = apply_anchored_edits(SOURCES[path], pairs, request_text(call))
            report["edit_engine"] = "APPLIED"
            if path == PARSER:
                report["javac_errors_after"] = javac_errors(patched)
        except FileIntegrityError as error:
            report["edit_engine"] = f"REFUSED {error.args[0] if error.args else error}"[:200]
    return report


def main() -> int:
    report = {"baseline_javac_errors_of_attempt1_parser": javac_errors(SOURCES[PARSER])}
    report["attempt2_call8_parser_edit"] = classify(8, PARSER)
    report["attempt3_call9_protocol_response"] = classify(9, PROTOCOL)
    report["attempt3_call10_parser_edit"] = classify(10, PARSER)
    report["attempt4_call11_protocol_edit"] = classify(11, PROTOCOL)
    report["attempt4_call12_parser_edit"] = classify(12, PARSER)
    print(json.dumps(report, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
