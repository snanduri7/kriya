"""Planner reasoning-on vs reasoning-off POC - first-hand evidence for 3
findings from a SME review of PlannerAgent (2026-08-15 session), before
deciding whether/how to fix them:

  1. Planner has zero output-sanity check - an empty/near-empty plan (a real,
     reproduced failure mode this session for a reasoning model starved of
     token budget) would silently flow into Architect today. This script's
     `plan_len_chars` + `plan_looks_empty` fields give a direct read on
     whether that actually happens on a real goal, in both think modes.
  2. Planner's system prompt never tells the model to honor the skill-
     conventions context it's actually given (workflow.py appends
     `convention_prompt` to Planner's user prompt same as Architect's, but
     Planner's system prompt - unlike Developer's - has no "use this"
     reminder). This script assembles a REAL convention_prompt slice from
     actual skills/<name>/rules.txt files (not synthetic) and gives both
     think modes the identical passive context, so a difference in rule-
     adherence between modes is attributable to reasoning, not to different
     input.
  3. extract_planner_code_blocks() (kriya/workflow/file_resolution.py)
     exists because Planner routinely writes real code in fenced blocks even
     though its prompt never asks for it - confirmed by that function's own
     docstring for this EXACT goal shape (ignite_qpid_protocol-family, 2026-
     08-11). This script runs the REAL function against both arms' plan
     text + the REAL Architect's resolved file list, so "does reasoning
     change how much reusable code Planner emits" is an objective count,
     not a guess from reading two files.

Deliberately NOT using kriya.core.llm.LLMClient for the actual completions -
confirmed earlier this session it has no code path to send an explicit
`think` param at all (always defers to Ollama's default), so there is no way
to force think:false through the real client today. Instead this reuses
bench.py's call_ollama() (which DOES support think=True/False) but sources
the REAL PlannerAgent/ArchitectAgent system prompts via direct import - zero
drift risk from hand-copying prompt text, while still getting the reasoning
toggle the real client can't do yet. Architect is deliberately run with
think=False in BOTH arms - the only varied input across the two runs is
Planner's think setting, so any difference in the FINAL file list/plan
content is attributable to that one variable, not a second reasoning axis
compounding it.

Usage:
    .venv/bin/python spikes/model_speed_poc/planner_reasoning_poc.py

Requires:
    - `ollama serve` running with qwen3.6:27b pulled (confirmed present
      earlier this session)
    - Run from a venv with kriya installed editable (`pip install -e .`) -
      same requirement as spikes/eval_harness/*.py

Expected runtime: the think:true arm gives Planner up to ~16K tokens of
budget (REASONING_HEADROOM_TOKENS on top of a 4K content budget) on a real,
non-trivial 3-layer goal - substantially more reasoning room than the tiny
code-gen prompts in bench.py needed, and this goal is far more complex than
those. Confirmed live earlier this session: reasoning on a MUCH simpler
prompt (bench.py's "write a task queue module") burned 4500+ tokens without
finishing. This run could plausibly take many minutes for the think arm
alone. On top of that, the REAL convention_prompt for ignite_qpid_protocol's
3 skills (ignite-java17/qpid/binary-wire-protocol rules.txt, verbatim, no
synthetic content) comes out to ~34KB / ~8500 tokens - confirmed by actually
loading them, not assumed - so there's real prefill cost on every call in
both arms, on top of the reasoning cost that only hits the think arm. Run
this yourself, in your own terminal - don't ask an assistant session to run
or poll it (same standing policy as spikes/eval_harness/).
"""
import argparse
import subprocess
import sys
from pathlib import Path

SPIKE_DIR = Path(__file__).parent
REPO_ROOT = SPIKE_DIR.parent.parent
sys.path.insert(0, str(SPIKE_DIR.parent / "eval_harness"))

from goals import GOALS  # noqa: E402

from bench import call_ollama  # noqa: E402

from kriya.agents.agent import ArchitectAgent, PlannerAgent  # noqa: E402
from kriya.agents.contracts import parse_file_list  # noqa: E402
from kriya.analyzer.analyzer import RepositoryAnalyzer  # noqa: E402
from kriya.workflow.file_resolution import extract_planner_code_blocks  # noqa: E402

MODEL = "qwen3.6:27b"
PLAN_MAX_TOKENS = 4096
ARCHITECT_MAX_TOKENS = 3000
REASONING_HEADROOM_TOKENS = 12000
# ignite_qpid_protocol is the exact goal shape extract_planner_code_blocks()'s
# own docstring cites as the confirmed real-world trigger for finding 3, and
# these are its real, already-verified skills (SkillEngine-loaded in the
# actual pipeline for this goal) - not an invented convention_prompt.
GOAL_SKILLS = {
    "ignite_qpid_protocol": ["ignite-java17", "qpid", "binary-wire-protocol"],
    "ignite_qpid_person": ["ignite-java17", "qpid"],
}
RESULTS_DIR = SPIKE_DIR / "results" / "planner_reasoning_poc"
PLAN_EMPTY_THRESHOLD_CHARS = 50


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "planner-reasoning-poc@kriya.local"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Kriya Planner Reasoning POC"], cwd=path, check=True)
    (path / "README.md").write_text("planner reasoning poc scratch project\n")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=path, check=True)


def _load_convention_prompt(skill_names: list) -> str:
    """Reads REAL skills/<name>/rules.txt files directly. Approximates (does
    NOT byte-match) workflow.py's own skills_prompt assembly - testing
    whether reasoning improves rule-ADHERENCE doesn't require exact prompt-
    assembly fidelity, just realistic real content in both arms."""
    parts = []
    for name in skill_names:
        rules_path = REPO_ROOT / "skills" / name / "rules.txt"
        if rules_path.exists():
            parts.append(f"### Skill: {name}\n{rules_path.read_text().strip()}")
    if not parts:
        return ""
    return "\n\nEngineering Skill Conventions (apply these where relevant):\n\n" + "\n\n".join(parts)


def run_one_mode(goal, repo_context: str, convention_prompt: str, think: bool, workspace: Path) -> dict:
    mode_label = "think" if think else "no_think"
    print(f"  [{mode_label}] Planner...", end=" ", flush=True)

    planner_system = PlannerAgent("planner", None).system_prompt
    plan_prompt = f"Goal: {goal.text}\n\nWorkspace Context:\n{repo_context}" + convention_prompt
    plan_max_tokens = PLAN_MAX_TOKENS + (REASONING_HEADROOM_TOKENS if think else 0)
    plan_result = call_ollama(MODEL, plan_prompt, plan_max_tokens, think=think, system_prompt=planner_system)
    plan_text = plan_result["output_text"]
    plan_looks_empty = len(plan_text.strip()) < PLAN_EMPTY_THRESHOLD_CHARS
    print(
        f"total={plan_result['total_time_s']}s think_tok~{plan_result['thinking_tokens_approx']} "
        f"content_tok~{plan_result['content_tokens_approx']} plan_chars={len(plan_text)} "
        f"{'[EMPTY/NEAR-EMPTY!]' if plan_looks_empty else ''}"
    )

    print(f"  [{mode_label}] Architect (think=False, fixed)...", end=" ", flush=True)
    architect_system = ArchitectAgent("architect", None).system_prompt
    design_prompt = f"Plan:\n{plan_text}\n\nWorkspace Context:\n{repo_context}" + convention_prompt
    design_result = call_ollama(MODEL, design_prompt, ARCHITECT_MAX_TOKENS, think=False, system_prompt=architect_system)
    design_text = design_result["output_text"]
    files, err = parse_file_list(design_text)
    print(f"total={design_result['total_time_s']}s structured={'YES' if files else 'NO (' + str(err) + ')'}")

    code_blocks = extract_planner_code_blocks(plan_text, files or [])

    mode_dir = workspace / mode_label
    mode_dir.mkdir(parents=True, exist_ok=True)
    (mode_dir / "plan.txt").write_text(plan_text)
    (mode_dir / "design.txt").write_text(design_text)
    if plan_result["thinking_text"]:
        (mode_dir / "plan_thinking.txt").write_text(plan_result["thinking_text"])

    return {
        "mode": mode_label,
        "plan_total_time_s": plan_result["total_time_s"],
        "plan_thinking_tokens_approx": plan_result["thinking_tokens_approx"],
        "plan_content_tokens_approx": plan_result["content_tokens_approx"],
        "plan_chars": len(plan_text),
        "plan_looks_empty": plan_looks_empty,
        "architect_total_time_s": design_result["total_time_s"],
        "architect_structured": files is not None,
        "architect_files": files,
        "reusable_code_blocks_found": list(code_blocks.keys()),
    }


def main():
    global MODEL
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--goal-id", default="ignite_qpid_protocol")
    parser.add_argument("--model", default=MODEL)
    args = parser.parse_args()

    MODEL = args.model

    goal = next((g for g in GOALS if g.id == args.goal_id), None)
    if goal is None:
        print(f"No goal '{args.goal_id}'. Known ids: {[g.id for g in GOALS]}", file=sys.stderr)
        sys.exit(1)

    workspace = RESULTS_DIR / args.goal_id
    workspace.mkdir(parents=True, exist_ok=True)
    scratch_repo = workspace / "_scratch_repo"
    scratch_repo.mkdir(exist_ok=True)
    _init_git_repo(scratch_repo)
    repo_context = RepositoryAnalyzer(str(scratch_repo)).analyze().model_dump_json(indent=2)

    skills = GOAL_SKILLS.get(args.goal_id, [])
    convention_prompt = _load_convention_prompt(skills)
    if not convention_prompt:
        print(f"[NOTE] No skill mapping for goal '{args.goal_id}' - running with zero convention_prompt "
              "(finding 2 won't be testable for this goal). Add an entry to GOAL_SKILLS to test it.")

    print(f"Model: {MODEL} | Goal: {args.goal_id} | Skills injected: {skills or 'none'}\n")

    results = []
    for think in (False, True):
        results.append(run_one_mode(goal, repo_context, convention_prompt, think, workspace))
        print()

    (workspace / "results.json").write_text(__import__("json").dumps(results, indent=2))

    print("=== Comparison ===")
    for r in results:
        print(f"\n[{r['mode']}]")
        print(f"  Plan: {r['plan_chars']} chars, {r['plan_thinking_tokens_approx']} thinking tok, "
              f"{r['plan_content_tokens_approx']} content tok, {r['plan_total_time_s']}s"
              f"{' -- LOOKS EMPTY/NEAR-EMPTY (finding 1!)' if r['plan_looks_empty'] else ''}")
        print(f"  Architect: structured={r['architect_structured']}, files={r['architect_files']}")
        print(f"  Reusable code blocks Planner wrote (finding 3): {r['reusable_code_blocks_found'] or 'none'}")

    print(f"\nFull plan/design text + thinking transcripts written under {workspace}")
    print("Read plan.txt for both modes side by side to judge finding 2 (rule adherence) directly.")


if __name__ == "__main__":
    main()
