"""Deterministic replay for a captured live-run incident, no LLM call needed.

Motivation: every bug found in the 2026-08-16/17 diagnosis-mismatch
investigation (docs/design.md §§7.23-7.26) was root-caused by hand -
extracting a raw completion from kriya.log via ad-hoc regex/string-slicing,
then re-typing it into a throwaway Python one-liner to feed through
DeveloperAgent._split_fix_analysis_edit() / find_edits_ignoring_own_diagnosis()
/ etc. That worked, but was slow and error-prone (the b-10c investigation
spent real effort just proving what text a DEBUG log line actually
contained, before ever getting to the interesting question). This script
automates that extraction + replay path.

Requires the log to have been captured with `logging.level: DEBUG` (see
spikes/eval_harness/run_harness.py's --log-level flag) - specifically the
"Developer raw completion for '<file>' (pre-parse): <repr>" lines added in
kriya/agents/agent.py._fill_missing_content(). Without DEBUG, there is
nothing here to replay; the tool says so rather than guessing.

Usage:
    # List every captured raw completion in a log, with an index to replay:
    .venv/bin/python spikes/incident_replay/replay_attempt.py list \\
        --log spikes/eval_harness/runs/b-10m/workspaces/ignite_qpid_person/logs/kriya.log

    # Replay one of them through the real, deterministic parsing/attribution
    # pipeline - no live model call:
    .venv/bin/python spikes/incident_replay/replay_attempt.py replay \\
        --log spikes/eval_harness/runs/b-10m/workspaces/ignite_qpid_person/logs/kriya.log \\
        --index 4 --fail-type compile

    # With the pre-edit file content available (needed for
    # find_edits_ignoring_own_diagnosis' full accuracy - without it, the
    # tool still shows the parsed analysis/edits, just skips that check):
    .venv/bin/python spikes/incident_replay/replay_attempt.py replay \\
        --log .../kriya.log --index 4 --fail-type compile \\
        --prior-content-file /tmp/PersonDemoApp.java.before
"""
import argparse
import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from kriya.agents.agent import DeveloperAgent
from kriya.workflow.attribution import find_edits_ignoring_own_diagnosis
from kriya.workflow.edit_safety import find_structural_corruption
from kriya.workflow.static_checks import run_static_checks

_RAW_COMPLETION_RE = re.compile(
    r"^.*?DEBUG kriya\.agents\.agent: Developer raw completion for '(?P<filepath>[^']+)' "
    r"\(pre-parse\): (?P<repr>.+)$"
)
_TIMESTAMP_RE = re.compile(r"^(\S+ \S+)")

# Best-effort mapping from a "Quality Gates FAILED (...): <message>" line's
# own free-text prefix to the Failure.type string that produced it - used
# only to auto-suggest --fail-type when scanning a log, since the type
# itself isn't printed verbatim in that WARNING line. A debugging aid, not
# production logic - get this wrong and the tool just suggests the wrong
# --fail-type, which the user can override; it never silently mis-replays.
_FAIL_TYPE_PREFIXES = [
    ("COMPILATION FAILURE", "compile"),
    ("STATIC RULE VIOLATION", "static_rule_violation"),
    ("STRUCTURAL CORRUPTION", "structural_corruption"),
    ("DIAGNOSIS MISMATCH", "diagnosis_mismatch"),
    ("ANCHORED EDIT FAILURE", "anchored_edit"),
    ("MISDIRECTED EDIT", "misdirected_edit"),
    ("UNADDRESSED ERROR LOCATION", "unaddressed_error_location"),
    ("INCOMPLETE GENERATION", "incomplete_generation"),
    ("RUNTIME VERIFICATION FAILURE", "run_verification"),
    ("Error code:", "general_error"),
]


@dataclass
class RawCompletion:
    index: int
    timestamp: str
    filepath: str
    raw_text: str
    preceding_fail_type_guess: Optional[str]


def extract_raw_completions(log_path: str) -> List[RawCompletion]:
    """Scans a DEBUG-level kriya.log for every captured raw Developer
    completion, in order. Uses ast.literal_eval on the logged repr() - the
    exact inverse of how it was written (f"...: {content!r}") - rather than
    hand-parsing escape sequences, so this recovers the byte-for-byte
    original string, not an approximation."""
    completions: List[RawCompletion] = []
    last_fail_type_guess: Optional[str] = None
    with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if "Quality Gates FAILED" in line:
                for prefix, fail_type in _FAIL_TYPE_PREFIXES:
                    if prefix in line:
                        last_fail_type_guess = fail_type
                        break
                continue
            m = _RAW_COMPLETION_RE.match(line)
            if not m:
                continue
            ts_m = _TIMESTAMP_RE.match(line)
            timestamp = ts_m.group(1) if ts_m else ""
            try:
                raw_text = ast.literal_eval(m.group("repr"))
            except (ValueError, SyntaxError):
                # A genuinely malformed/truncated repr (e.g. the harness
                # killed the process mid-write) - skip rather than crash the
                # whole scan, this is exactly the kind of edge case a
                # forensic tool must survive.
                continue
            completions.append(RawCompletion(
                index=len(completions),
                timestamp=timestamp,
                filepath=m.group("filepath"),
                raw_text=raw_text,
                preceding_fail_type_guess=last_fail_type_guess,
            ))
    return completions


def replay(raw_text: str, filepath: str, prior_content: Optional[str], fail_type: Optional[str]) -> dict:
    """Runs the real, deterministic parsing/attribution pipeline against a
    captured raw completion - identical code paths to
    DeveloperAgent._fill_missing_content()'s prefer_anchored_edit branch,
    just without the live LLM call that produced raw_text in the first
    place. Returns a plain dict, printed by the CLI below - kept as a
    function so this is also directly usable from a Python shell/notebook,
    not just the CLI."""
    report: dict = {"filepath": filepath}

    analysis, edits, content = DeveloperAgent._split_fix_analysis_edit(raw_text)
    report["analysis"] = analysis
    report["edits"] = edits
    report["content"] = content
    report["shape"] = "edits" if edits else ("full_content" if content is not None else "no_change_needed")

    if edits:
        candidate_pieces = [e.get("replace", "") for e in edits]
    elif content is not None:
        candidate_pieces = [content]
    else:
        candidate_pieces = []
    candidate_content = "\n".join(candidate_pieces) if candidate_pieces else None

    if candidate_content is not None:
        structural_problem = find_structural_corruption(filepath, candidate_content)
        report["structural_corruption"] = structural_problem
    else:
        report["structural_corruption"] = None

    if prior_content is not None:
        if edits:
            diagnosis_mismatch = find_edits_ignoring_own_diagnosis(analysis, edits, None, prior_content)
        else:
            diagnosis_mismatch = find_edits_ignoring_own_diagnosis(analysis, None, content, prior_content)
        report["diagnosis_mismatch"] = diagnosis_mismatch
    else:
        report["diagnosis_mismatch"] = "SKIPPED - no --prior-content-file supplied"

    if fail_type == "static_rule_violation" and candidate_content is not None:
        still_violates = run_static_checks(
            worktree_path=".", all_files_written=[filepath], overrides={filepath: candidate_content},
        )
        report["static_check_after_candidate"] = still_violates
        report["would_bypass_diagnosis_mismatch"] = bool(
            report["diagnosis_mismatch"] not in (None, "SKIPPED - no --prior-content-file supplied")
            and not still_violates
        )
    elif fail_type == "compile":
        report["would_bypass_diagnosis_mismatch"] = report["diagnosis_mismatch"] not in (
            None, "SKIPPED - no --prior-content-file supplied",
        )
    else:
        report["would_bypass_diagnosis_mismatch"] = False

    return report


def _print_report(report: dict) -> None:
    print(f"filepath: {report['filepath']}")
    print(f"shape: {report['shape']}")
    print()
    print("analysis:")
    print(f"  {report['analysis']!r}")
    if report["shape"] == "edits":
        for i, e in enumerate(report["edits"]):
            print(f"edit #{i}:")
            print(f"  search:  {e.get('search', '')!r}")
            print(f"  replace: {e.get('replace', '')!r}")
    elif report["shape"] == "full_content":
        print(f"content: {report['content']!r}")
    print()
    print(f"structural_corruption: {report['structural_corruption']!r}")
    print(f"diagnosis_mismatch: {report['diagnosis_mismatch']!r}")
    if "static_check_after_candidate" in report:
        print(f"static_check_after_candidate: {report['static_check_after_candidate']!r}")
    print(f"would_bypass_diagnosis_mismatch: {report['would_bypass_diagnosis_mismatch']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    list_p = sub.add_parser("list", help="List every captured raw completion in a log, with its index.")
    list_p.add_argument("--log", required=True)

    replay_p = sub.add_parser("replay", help="Replay one captured completion through the real pipeline.")
    replay_p.add_argument("--log", required=True)
    replay_p.add_argument("--index", type=int, required=True)
    replay_p.add_argument(
        "--fail-type", default=None,
        help="Failure type this retry was responding to (compile, static_rule_violation, ...) - "
             "if omitted, uses the auto-detected guess from the preceding 'Quality Gates FAILED' line.",
    )
    replay_p.add_argument(
        "--prior-content-file", default=None,
        help="Path to a file containing the target file's content BEFORE this edit - needed for "
             "find_edits_ignoring_own_diagnosis' check; without it, that check is reported as skipped.",
    )

    args = parser.parse_args()
    completions = extract_raw_completions(args.log)
    if not completions:
        print(
            f"No 'Developer raw completion' lines found in {args.log} - either this log wasn't captured "
            "with logging.level: DEBUG, or no per-file Developer completion happened in this run.",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.command == "list":
        for c in completions:
            preview = c.raw_text[:80].replace("\n", "\\n")
            print(f"[{c.index}] {c.timestamp} | {c.filepath} | guessed fail_type={c.preceding_fail_type_guess} | {preview}...")
        return

    if args.index >= len(completions):
        print(f"Index {args.index} out of range - {len(completions)} completions found (0-{len(completions) - 1}).", file=sys.stderr)
        sys.exit(1)
    c = completions[args.index]
    fail_type = args.fail_type or c.preceding_fail_type_guess
    prior_content = None
    if args.prior_content_file:
        prior_content = Path(args.prior_content_file).read_text(encoding="utf-8")
    report = replay(c.raw_text, c.filepath, prior_content, fail_type)
    _print_report(report)


if __name__ == "__main__":
    main()
