"""B2-c A3/A4/A5 compatibility precheck (harness; no model). For each frozen task, on a fresh clone at its base:
claim class of its goal (unchanged), Maven/JUnit/Surefire presence, whether the intended change touches the trust
surface, and whether Kriya's JVM acceptance staging runs there - measured with a neutral INFRASTRUCTURE PROBE (one
case asserting only that the target class loads; never an oracle, never added to any task).

usage: PYTHONPATH=<b2a checkout> python a345_precheck.py <out.json>
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
from kriya.workflow.acceptance_jvm import run_java_acceptance
from kriya.workflow.requirements import behavior_strength, derive_requirements, mutation_path_roles

TASKS = {
    "A3": ("p3v-a3-java-lang", "~/kriya-bench/commons-lang", "4ee346e59", "org.apache.commons.lang3.math", "NumberUtils"),
    "A4": ("p3v-a4-java-cli", "~/kriya-bench-r2/commons-cli", "d95484f", "org.apache.commons.cli", "CommandLine"),
    "A5": ("p3v-a5-spring-xml", "~/kriya-bench/spring-framework-petclinic", "09351b3",
           "org.springframework.samples.petclinic.web", "PetTypeFormatter"),
}
PROBE = """package {package};

import static org.junit.jupiter.api.Assertions.assertNotNull;

import org.junit.jupiter.api.Test;

class KriyaAcceptanceProbeTest {{
    // kriya_requirement: REQ-1
    @Test
    void targetClassLoads() {{
        assertNotNull({cls}.class);
    }}
}}
"""


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout


def main(out):
    results = {}
    for task, (name, source, base, package, cls) in TASKS.items():
        goal = open(os.path.expanduser(f"~/kriya-m1-live/goals/{name}.txt")).read()
        reqs = derive_requirements(goal)
        work = tempfile.mkdtemp(prefix=f"b2c-pre-{task}-")
        os.environ["KRIYA_STATE_DIR"] = os.path.join(work, "state")
        try:
            repo = os.path.join(work, "repo")
            git(work, "clone", "-q", os.path.expanduser(source), repo)
            git(repo, "checkout", "-q", "--detach", base)
            revision = git(repo, "rev-parse", "HEAD").strip()
            cand = os.path.join(work, "candidate")
            git(repo, "worktree", "add", "-q", "--detach", cand)
            tracked = git(repo, "ls-tree", "-r", "--name-only", revision).split()
            targets = mutation_path_roles(reqs, tracked)["authorized"]
            pom = open(os.path.join(repo, "pom.xml")).read()
            probe = os.path.join(work, "KriyaAcceptanceProbeTest.java")
            header = ""
            if os.environ.get("PROBE_LICENSE_HEADER") == "1":
                # The repository's own source header (the target file's leading block comment): Apache RAT rejects
                # a file without one. A comment, not behaviour.
                target_source = open(os.path.join(repo, targets[0])).read()
                if target_source.startswith("/*"):
                    header = target_source[:target_source.index("*/") + 2] + "\n"
            open(probe, "w").write(header + PROBE.format(package=package, cls=cls))
            artifact = ao.load_acceptance(probe, reqs, os.path.join(work, "artifacts"))
            started = time.time()
            run = run_java_acceptance(artifact, cand, candidate_paths=targets, base_revision=revision,
                                      validator_factory=lambda root, repo=repo: PolymorphicValidator(
                                          root, original_workspace_path=repo, autonomy_cfg=AutonomyConfig()))
            judgment = ao.judge_acceptance(artifact, run)["REQ-1"]
            req1 = reqs.requirements[0].text
            strength, why = behavior_strength(req1, regression_covered=True)
            results[task] = {
                "repository": f"{source} @ {base}", "requirements": reqs.ids,
                "maven": True, "junit5": "junit-jupiter" in pom, "surefire_declared_in_pom": "maven-surefire-plugin" in pom,
                "intended_change_paths": targets,
                "intended_change_on_trust_surface": any(not p.startswith("src/main/") for p in targets),
                "claim_class": strength, "claim_reasons": why["reasons"],
                "probe_code": judgment.code, "probe_reason": judgment.reason[:300],
                "probe_case_results": judgment.evidence.get("case_results"),
                "probe_seconds": round(time.time() - started, 1),
                "trust_surface_digest": run.trust_surface_digest,
            }
            print(task, json.dumps(results[task], default=str), flush=True)
        finally:
            shutil.rmtree(work, ignore_errors=True)
    json.dump(results, open(out, "w"), indent=2, default=str)


if __name__ == "__main__":
    main(sys.argv[1])
