"""demo-v2/scripts/answer_prompts.py stands in for a person at the terminal in the scenario 02 rehearsal. It must answer ONLY allowlisted prompts, record every decision,
and stop the run on anything else: a bulk 'yes' would defeat the governed-lookup story."""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

DRIVER = Path(__file__).resolve().parent.parent / "demo-v2" / "scripts" / "answer_prompts.py"
RULES = json.loads((Path(__file__).resolve().parent.parent / "demo-v2" / "scenarios" / "02-knowledge-skill" / "prompt_rules.json").read_text(encoding="utf-8"))

FAKE = textwrap.dedent('''
    import sys
    asks = sys.argv[1:]
    answers = []
    for text in asks:
        sys.stdout.write(text + " [y/N]: ")
        sys.stdout.flush()
        answers.append(sys.stdin.readline().strip())
    print("ANSWERS=" + ",".join(answers))
    sys.exit(0 if all(a == "y" for a in answers) else 7)
''')


def _run(tmp_path, *asks, rules=None):
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(json.dumps(rules or RULES))
    fake = tmp_path / "fake.py"
    fake.write_text(FAKE)
    log = tmp_path / "gov.jsonl"
    result = subprocess.run([sys.executable, str(DRIVER), "--rules", str(rules_path), "--log", str(log), "--", sys.executable, str(fake), *asks],
                            capture_output=True, text=True, timeout=60, check=False)
    records = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    return result, records


def test_allowlisted_prompts_are_answered_and_every_decision_is_recorded(tmp_path):
    result, records = _run(tmp_path, "Do you want to proceed anyway despite the knowledge risk?", "Send this search? (only these bare technology-name terms are sent)",
                           "Use these references for this run? (declining discards all of them)")
    assert result.returncode == 0 and "ANSWERS=y,y,y" in result.stdout
    assert [r["rule"] for r in records] == ["knowledge-risk", "send-search", "use-references"]
    assert all(r["answer"] == "y" and r["why"] and "[y/N]" in r["prompt"] for r in records)


def test_an_unrecognised_prompt_stops_the_run_and_is_never_answered(tmp_path):
    result, records = _run(tmp_path, "Send this search? terms", "Do you want to wipe the disk?", "Use these references for this run?")
    assert result.returncode == 3, result.stdout
    assert "ANSWERS=" not in result.stdout  # the child was killed before it could finish
    assert [r.get("rule") for r in records] == ["send-search", None]
    assert records[-1]["result"] == "UNRECOGNISED_PROMPT_RUN_STOPPED" and records[-1]["answer"] is None


def test_no_prompt_means_no_answer_and_the_childs_exit_status_is_kept(tmp_path):
    result, records = _run(tmp_path)
    assert result.returncode == 0 and records == []


def test_the_allowlist_is_exactly_the_four_decisions_and_each_has_a_reason():
    ids = [r["id"] for r in RULES["rules"]]
    assert ids == ["knowledge-risk", "send-search", "use-references", "apply-changes"]
    assert all(r["answer"] == "y" and r["why"].strip() for r in RULES["rules"])
    assert not any(r["match"] in (".*", "", ".+") for r in RULES["rules"]), "a catch-all rule would be a bulk yes"
