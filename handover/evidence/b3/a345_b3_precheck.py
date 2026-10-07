"""B3 A3/A4/A5 precheck (harness; no model). For each frozen task (goal unchanged): validate the proposed minimum
acceptance suite against the goal (Kriya's own artifact rules), write an UNAPPROVED approval template for owner review
(``accept_suite_as_sufficient: false`` - never approved here), and measure the suite on fresh clones:
  base       - the unmodified base revision: the suite must not pass (non-vacuous);
  reference  - DIAGNOSTIC ONLY: a harness-written implementation of the goal, to show the suite compiles and can pass.
               It is never part of any artifact, never shown to a model, and proves nothing about a future candidate.

usage: PYTHONPATH=<checkout> python a345_b3_precheck.py <out.json> [TASK ...]
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

from kriya.config.config import AutonomyConfig
from kriya.tools.validate import PolymorphicValidator
from kriya.workflow import acceptance_oracle as ao
from kriya.workflow.acceptance_approval import approval_template
from kriya.workflow.acceptance_jvm import run_java_acceptance
from kriya.workflow.requirements import behavior_strength, derive_requirements, mutation_path_roles

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "a345")
TASKS = {
    "A3": ("p3v-a3-java-lang", "~/kriya-bench/commons-lang", "4ee346e59",
           "src/main/java/org/apache/commons/lang3/math/NumberUtils.java",
           ("\n}\n", "\n    public static int clamp(final int value, final int min, final int max) {\n"
                     "        if (min > max) {\n            throw new IllegalArgumentException(\"min > max\");\n        }\n"
                     "        return Math.max(min, Math.min(max, value));\n    }\n}\n")),
    "A4": ("p3v-a4-java-cli", "~/kriya-bench-r2/commons-cli", "d95484f",
           "src/main/java/org/apache/commons/cli/CommandLine.java",
           ("    public boolean hasOption(final String optionName) {",
            "    public boolean hasAnyOption(final String... optNames) {\n        if (optNames == null) {\n"
            "            return false;\n        }\n        for (final String name : optNames) {\n"
            "            if (hasOption(name)) {\n                return true;\n            }\n        }\n"
            "        return false;\n    }\n\n    public boolean hasOption(final String optionName) {")),
    "A5": ("p3v-a5-spring-xml", "~/kriya-bench/spring-framework-petclinic", "09351b3",
           "src/main/java/org/springframework/samples/petclinic/web/PetTypeFormatter.java",
           ("if (type.getName().equals(text)) {", "if (type.getName().equalsIgnoreCase(text.trim())) {")),
}


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout


def measure(artifact, repo, base, cand, targets):
    started = time.time()
    run = run_java_acceptance(artifact, cand, candidate_paths=targets, base_revision=base,
                              validator_factory=lambda root: PolymorphicValidator(
                                  root, original_workspace_path=repo, autonomy_cfg=AutonomyConfig()))
    judgment = ao.judge_acceptance(artifact, run)["REQ-1"]
    return {"code": judgment.code, "reason": judgment.reason[:300], "case_results": judgment.evidence.get("case_results"),
            "seconds": round(time.time() - started, 1)}


def main(out):
    results = {}
    only = set(sys.argv[2:])  # optional task filter (e.g. A4 A5); default: all
    for task, (name, source, base_ref, target, (anchor, replacement)) in TASKS.items():
        if only and task not in only:
            continue
        goal = open(os.path.expanduser(f"~/kriya-m1-live/goals/{name}.txt")).read()
        reqs = derive_requirements(goal)
        work = tempfile.mkdtemp(prefix=f"b3-pre-{task}-")
        os.environ["KRIYA_STATE_DIR"] = os.path.join(work, "state")
        try:
            repo = os.path.join(work, "repo")
            git(work, "clone", "-q", os.path.expanduser(source), repo)
            git(repo, "checkout", "-q", "--detach", base_ref)
            base = git(repo, "rev-parse", "HEAD").strip()
            suite = os.path.join(work, "KriyaAcceptanceTest.java")
            shutil.copy(os.path.join(HERE, f"{task}_KriyaAcceptanceTest.java"), suite)
            artifact = ao.load_acceptance(suite, reqs, os.path.join(work, "artifacts"))
            template = approval_template(reqs, artifact, ["REQ-1"], base)
            with open(os.path.join(HERE, f"{task}_approval_TEMPLATE_unapproved.json"), "w") as handle:
                json.dump(template, handle, indent=2)
            targets = mutation_path_roles(reqs, git(repo, "ls-tree", "-r", "--name-only", base).split())["authorized"]
            base_cand = os.path.join(work, "base")
            git(repo, "worktree", "add", "-q", "--detach", base_cand)
            ref_cand = os.path.join(work, "reference")
            git(repo, "worktree", "add", "-q", "--detach", ref_cand)
            path = os.path.join(ref_cand, target)
            text = open(path).read()
            index = text.rfind(anchor) if anchor == "\n}\n" else text.find(anchor)
            assert index >= 0, (task, "reference anchor")
            open(path, "w").write(text[:index] + replacement + text[index + len(anchor):])
            strength, why = behavior_strength(reqs.requirements[0].text, regression_covered=True)
            results[task] = {
                "goal_unchanged_from": f"~/kriya-m1-live/goals/{name}.txt", "claim": strength, "claim_reasons": why["reasons"],
                "acceptance_sha256": artifact.digest, "cases": artifact.identities_for("REQ-1"),
                "approval_template": f"a345/{task}_approval_TEMPLATE_unapproved.json", "base_revision": base,
                "base": measure(artifact, repo, base, base_cand, targets),
                "reference_DIAGNOSTIC_ONLY": measure(artifact, repo, base, ref_cand, targets),
            }
            print(task, json.dumps(results[task], default=str)[:1200], flush=True)
        finally:
            shutil.rmtree(work, ignore_errors=True)
    json.dump(results, open(out, "w"), indent=2, default=str)


if __name__ == "__main__":
    main(sys.argv[1])
