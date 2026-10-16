"""Scenario 04 model-free qualification helper (called by qualify_spring_ignite.sh). No model, no network, no Kriya run.

Subcommands print ONE JSON object on stdout and exit 0 only when every check inside passed:

  runtime  --repo R --fixture F [--contained] [--cache-seed DIR]
      Runs the reference candidate's runtime command through Kriya's own runtime-verification path
      (PolymorphicValidator.run_app_sequence, with the verification tree bound, exactly as the workflow does)
      and checks the application-owned success line, the Ignite start and the exit code.
      --contained uses the OCI backend (network DENIED); the Maven cache is pre-seeded from --cache-seed so no
      network is needed (the workflow's own compile/test gates would normally have filled it).
      --witness additionally places ./cache-witness (offset 1000) in the project: the cache itself then stores every Protocol with `time` moved by 1000,
      so the expected line changes to time=1700001000, equals=false, and only a program that really used the value read back from the cache can print it.
  mutants  --repo R --fixture F
      Mutation self-check of the read-back proof: each mutant (cache.get discarded / replaced by a local copy / Main printing the original / round trip bypassed)
      must be KILLED by the JUnit round-trip tests or by the witness runtime leg; a surviving mutant fails the qualification.
  guard    --repo R --goal G --skill S
      KnowledgeGuard on the scenario goal: the desktop is always knowledge-policy strict, so ANY gap stops the run.
  skill    --repo R --config C --workspace W --goal G
      The verified skill through Kriya's own SkillService / SkillEngine and the workflow's relevance predicate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

IGNITE_218_RELEASED = datetime(2026, 4, 23, tzinfo=timezone.utc)  # the ignite-core-2.18.0 jar's own build stamp: "ver. 2.18.0#20260423"
PROTOCOL_CLASS = "com.example.protocol.Protocol"


def ignite_type_id(class_name: str) -> int:
    """Ignite's binary type id: the Java String.hashCode of the LOWER-CASED class name (measured: com.example.protocol.protocol -> -165319811)."""
    value = 0
    for char in class_name.lower():
        value = (31 * value + ord(char)) & 0xFFFFFFFF
    return value - (1 << 32) if value >= (1 << 31) else value


RUNTIME_COMMAND = ["mvn", "-e", "-q", "compile", "exec:exec", "-Dexec.mainClass=com.example.Main"]


def _expected_line() -> str:
    env = Path(__file__).resolve().parent.parent / "fixtures" / "spring-ignite-protocol" / "fixture.env"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("EXPECTED_RESULT_LINE="):
            return line.split("=", 1)[1]
    raise SystemExit("EXPECTED_RESULT_LINE missing from fixture.env")


WITNESS_OFFSET = 1000
WITNESS_LINE = "[RESULT] protocolVersion=1, softwareVersion=2, dataLength=15, time=1700001000, bodyLength=15, equals=false"


def cmd_runtime(args: argparse.Namespace) -> Dict[str, Any]:
    sys.path.insert(0, args.repo)
    from kriya.config.config import AutonomyConfig
    from kriya.tools.validate import PolymorphicValidator
    from kriya.workflow.file_integrity import VerificationTreeBinding

    fixture = Path(args.fixture)
    files = sorted(str(p.relative_to(fixture)) for p in fixture.rglob("*") if p.is_file())
    witness = bool(getattr(args, "witness", False))
    if witness:
        files.append("cache-witness")
    report: Dict[str, Any] = {"mode": "contained" if args.contained else "host", "witness": witness, "checks": {}}
    with tempfile.TemporaryDirectory(prefix="qualify04-") as tmp:
        if args.contained:
            os.environ["KRIYA_STATE_DIR"] = os.path.join(tmp, "state")
        workspace = os.path.join(tmp, "ws")
        shutil.copytree(fixture, workspace)
        if witness:
            Path(workspace, "cache-witness").write_text(f"{WITNESS_OFFSET}\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)  # the candidate is untracked, as in a fresh unit
        if args.contained:
            key = hashlib.sha256(os.path.realpath(workspace).encode()).hexdigest()[:16]
            cache = os.path.join(tmp, "state", "dependency-cache", "maven", key)
            os.makedirs(os.path.dirname(cache), exist_ok=True)
            shutil.copytree(os.path.expanduser(args.cache_seed), cache)
        cfg = AutonomyConfig(contained_execution_required=args.contained, containment_backend="oci" if args.contained else "none")
        validator = PolymorphicValidator(workspace, autonomy_cfg=cfg)
        validator.tree_binding = VerificationTreeBinding(workspace, [], files)
        started = time.time()
        try:
            result = validator.run_app_sequence([list(RUNTIME_COMMAND)], timeout=300)
        except Exception as exc:  # noqa: BLE001 - a typed stop of the gate is the measurement, reported verbatim
            report["stopped"] = {"type": type(exc).__name__, "text": str(exc)[:600]}
            report["ok"] = False
            return report
        report["elapsed_seconds"] = round(time.time() - started, 1)
        output = result["output"]
        expected = WITNESS_LINE if witness else _expected_line()
        checks = {
            "gate_success": bool(result["success"]),
            "exit_code_0": result["returncode"] == 0,
            "not_timed_out": not result["timed_out"],
            "ignite_node_started": "Ignite node started OK" in output,
            "ignite_node_stopped": "Ignite node stopped OK" in output,
            "expected_result_line_exact": expected in output.splitlines(),
            "verification_pass_marker": ("[VERIFICATION] FAIL" if witness else "[VERIFICATION] PASS") in output,
        }
        artifacts = result.get("runtime_artifacts") or []
        # Independent runtime proof that a Protocol INSTANCE went into the Ignite cache: Ignite writes <work>/db/marshaller/<typeId>.classname0 when it first marshals a user class.
        checks["protocol_class_registered_with_ignite_marshaller"] = f"ignite/work/db/marshaller/{ignite_type_id(PROTOCOL_CLASS)}.classname0" in artifacts
        report["checks"] = checks
        report["runtime_artifacts"] = result.get("runtime_artifacts")
        report["only_ignite_runtime_state"] = all(str(a).startswith("ignite/") for a in (result.get("runtime_artifacts") or []))
        report["egress"] = result.get("egress")
        toolchain = result.get("toolchain_identity") or {}
        report["toolchain"] = {k: toolchain.get(k) for k in ("runtime_version", "build_tool_version", "containment_image", "observed_runtime_version", "observed_build_tool_version")} or None
        report["result_line_lines_before_it"] = next((i for i, line in enumerate(output.splitlines()) if line.startswith("[RESULT]")), None)
        report["ok"] = all(checks.values()) and report["only_ignite_runtime_state"]
        if not report["ok"]:
            report["output_tail"] = output[-2500:]
    return report


MUTANTS = (
    # name, file, text to replace, replacement: each breaks the READ-BACK in one specific way
    ("get_result_discarded", "src/main/java/com/example/ProtocolCacheDemo.java", "Protocol retrieved = cache.get(KEY);",
     "cache.get(KEY);\n        Protocol retrieved = protocol;"),
    ("get_replaced_by_a_local_copy", "src/main/java/com/example/ProtocolCacheDemo.java", "Protocol retrieved = cache.get(KEY);",
     "cache.get(KEY);\n        Protocol retrieved = new Protocol(protocol.getProtocolVersion(), protocol.getSoftwareVersion(), protocol.getTime(), protocol.getBody());"),
    ("main_prints_the_original_not_the_retrieved", "src/main/java/com/example/Main.java", "ProtocolCacheDemo.resultLine(original, retrieved)",
     "ProtocolCacheDemo.resultLine(original, original)"),
    ("main_bypasses_the_cache_round_trip", "src/main/java/com/example/Main.java", "Protocol retrieved = ProtocolCacheDemo.roundTrip(cache, decoded);",
     "Protocol retrieved = decoded;"),
)


def _mvn_test_passes(project: str) -> bool:
    result = subprocess.run(["mvn", "-o", "-B", "clean", "test"], cwd=project, capture_output=True, text=True, check=False, timeout=600)
    return result.returncode == 0 and "Tests run: 5, Failures: 0, Errors: 0, Skipped: 0" in result.stdout


def cmd_mutants(args: argparse.Namespace) -> Dict[str, Any]:
    fixture = Path(args.fixture)
    report: Dict[str, Any] = {"cases": {}}
    with tempfile.TemporaryDirectory(prefix="qualify04-mutants-") as tmp:
        for name, rel, old, new in (("unmutated_reference", None, None, None), *MUTANTS):
            project = os.path.join(tmp, name)
            shutil.copytree(fixture, project)
            if rel is not None:
                target = Path(project, rel)
                text = target.read_text(encoding="utf-8")
                if text.count(old) != 1:
                    report["cases"][name] = {"error": f"mutation target not found exactly once in {rel}"}
                    continue
                target.write_text(text.replace(old, new), encoding="utf-8")
            pristine = project + "-for-witness"  # the JUnit run leaves target/ and ignite/ behind; the runtime leg must start from the mutated sources only
            shutil.copytree(project, pristine)
            tests_pass = _mvn_test_passes(project)
            witness = cmd_runtime(argparse.Namespace(repo=args.repo, fixture=pristine, contained=False, cache_seed="", witness=True))
            case = {"junit_passes": tests_pass, "witness_leg_ok": bool(witness.get("ok"))}
            case["killed"] = not (tests_pass and case["witness_leg_ok"])
            case["killed_by"] = [label for label, alive in (("junit", not tests_pass), ("witness_leg", not case["witness_leg_ok"])) if alive]
            report["cases"][name] = case
    control = report["cases"].get("unmutated_reference", {})
    report["control_passes"] = control.get("junit_passes") is True and control.get("witness_leg_ok") is True
    report["surviving_mutants"] = [n for n, c in report["cases"].items() if n != "unmutated_reference" and not c.get("killed")]
    report["ok"] = report["control_passes"] and not report["surviving_mutants"] and len(report["cases"]) == len(MUTANTS) + 1
    return report


def cmd_guard(args: argparse.Namespace) -> Dict[str, Any]:
    sys.path.insert(0, args.repo)
    from kriya.tools.knowledge import KnowledgeCache, KnowledgeGuard, extract_library_versions

    goal = Path(args.goal).read_text(encoding="utf-8").strip()
    skill = Path(args.skill)

    def run(offline: bool, with_skill: bool, release_known: bool) -> Dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="qualify04-guard-") as tmp:
            skills = os.path.join(tmp, "skills")
            os.makedirs(skills)
            if with_skill:
                shutil.copytree(skill, os.path.join(skills, skill.name))
            memory = os.path.join(tmp, "memory")
            os.makedirs(memory)
            if release_known:  # what the online guard learns from the registry (or its cache); seeded so no network is used
                KnowledgeCache(memory, ttl_days=30).set_release_date("java", "org.apache.ignite:ignite-core", "2.18.0", IGNITE_218_RELEASED, "seeded-for-qualification")
            report = KnowledgeGuard(skills_dir=skills, cutoff_date_str="2023-12-01", offline=offline, memory_dir=memory, cache_ttl_days=30).check_goal(goal, tmp)
            return {"has_gaps": report.has_gaps, "gaps": [(g["library"], g["version"], g["risk_level"]) for g in report.gaps]}

    cases = {
        "default_online_guard_with_skill": run(False, True, True),
        "default_online_guard_without_skill": run(False, False, True),
        "offline_mode_with_skill": run(True, True, False),
        "offline_mode_without_skill": run(True, False, False),
    }
    expected = {
        "default_online_guard_with_skill": True,   # a desktop launch cannot acknowledge this: the run would stop before any model call
        "default_online_guard_without_skill": True,
        "offline_mode_with_skill": False,          # the scenario configuration: the run proceeds
        "offline_mode_without_skill": True,        # offline mode alone is not a bypass: the skill is what resolves the gap
    }
    return {"extracted": extract_library_versions(goal), "cases": cases, "ok": {k: v["has_gaps"] for k, v in cases.items()} == expected}


def cmd_skill(args: argparse.Namespace) -> Dict[str, Any]:
    sys.path.insert(0, args.repo)
    import yaml

    from kriya.config.config import AppConfig
    from kriya.skills.service import SkillService
    from kriya.skills.skill import SkillEngine, mentions_term
    from kriya.workflow.workflow import _split_rules_by_verification

    cfg = AppConfig.model_validate(yaml.safe_load(Path(args.config).read_text(encoding="utf-8")))
    goal = Path(args.goal).read_text(encoding="utf-8").strip()
    descriptors, total = SkillService(cfg, args.workspace).list()
    engine = SkillEngine.from_config(cfg, workspace_path=args.workspace)
    engine.discover_and_load()
    skills = engine.list_skills()
    skill = next((s for s in skills if s.name == "ignite-java17"), None)
    report: Dict[str, Any] = {"service_total": total, "service_ids": [d.id for d in descriptors], "engine_skills": [s.name for s in skills]}
    if skill is None:
        report["ok"] = False
        return report
    relevant = mentions_term(goal, skill.name) or any(mentions_term(goal, tag) for tag in skill.tags)  # the workflow's own predicate (kriya/workflow/workflow.py, stage "skills")
    trusted, unverified = _split_rules_by_verification(skill)
    descriptor = descriptors[0]
    report.update(
        relevant_to_goal=relevant, verified=skill.verified, verified_context=skill.verified_context, tags=list(skill.tags),
        supported_versions=skill.supported_versions, rule_count=len(skill.rules), trusted_rules=len(trusted), unverified_rules=len(unverified),
        has_instructions=bool(skill.instructions.strip()), example_count=len(skill.examples),
        rules_carry_exec_exec_guidance=any("exec:exec" in r for r in trusted), rules_carry_add_opens=any("--add-opens" in r for r in trusted),
        rules_carry_one_mechanism=any("EXACTLY ONE" in r for r in trusted), rules_carry_close_context=any("try (ConfigurableApplicationContext" in r for r in trusted),
        descriptor_digest=descriptor.digest, descriptor_scope=descriptor.scope, descriptor_unknown_fields=list(descriptor.unknown_fields),
    )
    report["ok"] = bool(relevant and skill.verified and not unverified and len(descriptors) == 1 and skill.supported_versions == "*"
                        and report["rules_carry_exec_exec_guidance"] and report["rules_carry_add_opens"] and report["rules_carry_one_mechanism"] and report["rules_carry_close_context"])
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    runtime = sub.add_parser("runtime")
    runtime.add_argument("--repo", required=True)
    runtime.add_argument("--fixture", required=True)
    runtime.add_argument("--contained", action="store_true")
    runtime.add_argument("--cache-seed", default="~/.m2/repository")
    runtime.add_argument("--witness", action="store_true")
    mutants = sub.add_parser("mutants")
    mutants.add_argument("--repo", required=True)
    mutants.add_argument("--fixture", required=True)
    guard = sub.add_parser("guard")
    guard.add_argument("--repo", required=True)
    guard.add_argument("--goal", required=True)
    guard.add_argument("--skill", required=True)
    skill = sub.add_parser("skill")
    skill.add_argument("--repo", required=True)
    skill.add_argument("--config", required=True)
    skill.add_argument("--workspace", required=True)
    skill.add_argument("--goal", required=True)
    args = parser.parse_args()
    report = {"runtime": cmd_runtime, "mutants": cmd_mutants, "guard": cmd_guard, "skill": cmd_skill}[args.command](args)
    print(json.dumps(report, indent=1, default=str))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
