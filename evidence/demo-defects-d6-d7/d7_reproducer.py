"""D7 model-free reproducer: KNOW A attempt 4 (runtime-4, run-a-20261001T121651Z). The model's analysis says to
remove `Ignition.getOrCreateIgnite()`; its edit removes that call. Replays the exact responses through the
production parser and attribution.find_edits_ignoring_own_diagnosis. Usage: python d7_reproducer.py <repo root>"""
import json
import sys
from pathlib import Path

ROOT = Path(sys.argv[1])
sys.path.insert(0, str(ROOT))
from kriya.agents.response_protocol import parse_structured  # noqa: E402
from kriya.workflow.attribution import find_edits_ignoring_own_diagnosis  # noqa: E402

FIX = Path(__file__).parent / "fixtures"
PATH = "src/main/java/com/example/IgniteDemoApp.java"
original = parse_structured((FIX / "knowa_attempt1_file_response.txt").read_text(), PATH, patch_allowed=False).content
response = parse_structured((FIX / "knowa_attempt4_edit_response.txt").read_text(), PATH, patch_allowed=True)
edits = response.edit_dicts()
verdict = find_edits_ignoring_own_diagnosis(response.analysis, edits, None, original)
print(json.dumps({
    "call_in_original": "Ignition.getOrCreateIgnite(" in original,
    "call_in_edit_search": any("Ignition.getOrCreateIgnite(" in e["search"] for e in edits),
    "call_in_edit_replace": any("getOrCreateIgnite" in e["replace"] for e in edits),
    "verdict": "DIAGNOSIS MISMATCH" if verdict else "accepted",
}, indent=1))
