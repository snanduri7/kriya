"""Model-free replay of the RECORDED Developer analyses of live run 20261016T094912-e7b4804c through Kriya's own self-diagnosis extractor
(kriya/workflow/attribution.py::extract_self_diagnosed_files), plus controls that isolate the mechanism.

Usage: python replay_attribution.py <repo root> <evidence dir (.../scenario-04)>   (no model, no network)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(sys.argv[1])
EXTRACTED = Path(sys.argv[2]) / "extracted"
sys.path.insert(0, str(REPO))
from kriya.workflow.attribution import extract_self_diagnosed_files  # noqa: E402

PROTOCOL = "src/main/java/com/example/protocol/Protocol.java"
PARSER = "src/main/java/com/example/protocol/ProtocolParser.java"
KNOWN = [PROTOCOL, PARSER, "pom.xml", "src/main/resources/applicationContext.xml"]


def main() -> int:
    report = {"recorded_analyses_attached_to_ProtocolParser": {}, "controls": {}}
    for path in sorted(EXTRACTED.glob("*developer.parse*analysis_model_claimed*")):
        analysis = path.read_text(encoding="utf-8")
        report["recorded_analyses_attached_to_ProtocolParser"][path.name] = extract_self_diagnosed_files([{"filepath": PARSER, "analysis": analysis}], KNOWN)
    controls = {
        "analysis that only QUOTES the compiler message (names the class Protocol, says to use getters in the parser)":
            "The compiler says: body has private access in Protocol. The parser must use getBody() instead of the field.",
        "analysis that names no other file": "Use the getter methods instead of direct field access.",
        "analysis that really redirects ('the defect is in Protocol.java')": "The real defect is in Protocol.java: its fields need accessors.",
    }
    for label, text in controls.items():
        report["controls"][label] = extract_self_diagnosed_files([{"filepath": PARSER, "analysis": text}], KNOWN)
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
