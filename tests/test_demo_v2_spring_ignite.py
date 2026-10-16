"""Scenario 04 (Spring XML + Apache Ignite + Protocol, greenfield): the deterministic, model-free contracts of its assets.

Static or in-process only: no model, no network, no Maven, no Ignite (those are `demo-v2/scripts/qualify_spring_ignite.sh`, which runs the real tools).
What is pinned here is what the live demo silently depends on: the goal is natural and carries no tool command, the scenario configuration validates and
sets the one setting that lets a DESKTOP launch past KnowledgeGuard, the skill and the reference candidate are the ones that were qualified (digests), the
KnowledgeGuard behaviour the configuration relies on (measured, see handover/SPRING-IGNITE-PROTOCOL/QUALIFICATION.md), the skill loads through Kriya's
SkillService, the reference candidate satisfies the structural contract, and reset keeps the project folder genuinely empty.
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

import pytest
import yaml

from kriya.config.config import AppConfig
from kriya.skills.service import SkillService
from kriya.skills.skill import SkillEngine, mentions_term
from kriya.tools.knowledge import KnowledgeCache, KnowledgeGuard, extract_library_versions

REPO = Path(__file__).resolve().parent.parent
DEMO_V2 = REPO / "demo-v2"
SCENARIO = DEMO_V2 / "scenarios" / "04-spring-ignite-protocol"
FIXTURE = DEMO_V2 / "fixtures" / "spring-ignite-protocol"
REFERENCE = FIXTURE / "reference"
SKILL = REPO / "skills" / "ignite-java17"
GOAL = (SCENARIO / "goal.md").read_text(encoding="utf-8")
IGNITE_218_RELEASED = datetime(2026, 4, 23, tzinfo=timezone.utc)  # the ignite-core-2.18.0 jar's own build stamp


def _env(path: Path) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            key, _, value = line.partition("=")
            out[key] = value
    return out


def _dir_digest(root: Path) -> str:
    """The same digest demo-v2/scripts/_common.sh dir_digest computes: sha256 of the sorted '<file sha256>  ./<path>' lines."""
    lines = sorted(f"./{p.relative_to(root).as_posix()}" for p in root.rglob("*") if p.is_file())
    text = "".join(f"{hashlib.sha256((root / name[2:]).read_bytes()).hexdigest()}  {name}\n" for name in lines)
    return hashlib.sha256(text.encode()).hexdigest()


def _rendered_config(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    target = work / "kriya.yaml"
    target.write_text((SCENARIO / "config" / "kriya.template.yaml").read_text(encoding="utf-8").replace("@WORK@", str(work)), encoding="utf-8")
    return target


# ---- the goal ----

def test_goal_is_natural_and_names_what_the_scenario_needs() -> None:
    for needed in ("Java 17", "Maven", "Spring XML", "Apache Ignite 2.18.0", "Protocol", "ProtocolParser", "JUnit", "[RESULT]", "1700000000", "Hello, Protocol"):
        assert needed in GOAL, needed
    assert GOAL.count("\n") <= 1 and len(GOAL) < 2000  # one paragraph, no hidden second document


def test_goal_carries_no_tool_command_no_flag_no_skill_and_no_internal_term() -> None:
    forbidden = re.compile(r"mvn|exec:|add-opens|--add|exec-maven|surefire|skill|knowledge|kriya|IgniteSpringBean|Ignition\.start|getBean|ConfigurableApplicationContext|classifier", re.IGNORECASE)
    assert forbidden.search(GOAL) is None


def test_negative_control_the_goal_check_catches_an_engineered_goal() -> None:
    forbidden = re.compile(r"mvn|exec:|add-opens|--add|exec-maven|surefire|skill|knowledge|kriya|IgniteSpringBean|Ignition\.start|getBean|ConfigurableApplicationContext|classifier", re.IGNORECASE)
    for engineered in (GOAL + " Run it with mvn exec:exec.", GOAL + " Use the ignite skill.", GOAL + " Use IgniteSpringBean.", GOAL + " Pass --add-opens."):
        assert forbidden.search(engineered) is not None


def test_goal_states_the_wire_format_the_reference_implements() -> None:
    assert "9-byte big-endian header" in GOAL and "protocolVersion 1 byte, softwareVersion 1 byte, dataLength 3 bytes, time 4 bytes" in GOAL
    parser = (REFERENCE / "src" / "main" / "java" / "com" / "example" / "protocol" / "ProtocolParser.java").read_text(encoding="utf-8")
    assert "HEADER_LENGTH = 9" in parser and "out[2] = (byte) (body.length >> 16)" in parser and "out[5] = (byte) (time >> 24)" in parser


# ---- the scenario configuration ----

def test_scenario_configuration_validates_and_keeps_every_approval_boundary(tmp_path: Path) -> None:
    raw = yaml.safe_load(_rendered_config(tmp_path).read_text(encoding="utf-8"))
    config = AppConfig.model_validate(raw)  # a real, validated config: unknown fields are rejected
    assert config.autonomy.mode == "human-in-the-loop"
    assert config.autonomy.run_verification_enabled is True
    assert config.autonomy.web_lookup_enabled is False and config.autonomy.web_lookup_auto_approve is False
    assert config.search.base_url == ""
    assert config.workflow_controller.enabled is True and config.workflow_controller.mode == "enforce"
    assert config.autonomy.generation_time_budget_seconds == 900  # not enlarged: no measurement justifies it
    assert config.autonomy.contained_execution_required is False and config.runtime_profile is None  # the standard development-profile Demo V2 scenario, not a security downgrade
    assert config.llm.model == "qwen3-coder:30b"
    assert config.skills.load_global is False and config.skills.load_cwd is False
    assert config.paths.skills == str(tmp_path / "work" / "skills")


def test_scenario_configuration_sets_knowledge_offline_mode_and_does_not_disable_the_guard(tmp_path: Path) -> None:
    config = AppConfig.model_validate(yaml.safe_load(_rendered_config(tmp_path).read_text(encoding="utf-8")))
    assert config.knowledge.offline_mode is True
    assert config.knowledge.check_enabled is True  # the guard stays ON; offline mode only removes the public release-date lookup


# ---- identity: what was qualified is what is shipped ----

def test_recorded_digests_match_the_skill_and_the_reference_candidate() -> None:
    env = _env(FIXTURE / "fixture.env")
    assert _dir_digest(SKILL) == env["SKILL_DIGEST"]
    assert _dir_digest(REFERENCE) == env["REFERENCE_DIGEST"]
    assert len([p for p in SKILL.rglob("*") if p.is_file()]) == int(env["SKILL_FILES"])
    assert len([p for p in REFERENCE.rglob("*") if p.is_file()]) == int(env["REFERENCE_FILES"])


def test_negative_control_an_edited_skill_or_candidate_changes_the_digest(tmp_path: Path) -> None:
    env = _env(FIXTURE / "fixture.env")
    edited_skill = tmp_path / "skill"
    shutil.copytree(SKILL, edited_skill)
    (edited_skill / "rules.txt").write_text((edited_skill / "rules.txt").read_text(encoding="utf-8") + "\nan extra rule\n", encoding="utf-8")
    assert _dir_digest(edited_skill) != env["SKILL_DIGEST"]
    edited_reference = tmp_path / "reference"
    shutil.copytree(REFERENCE, edited_reference)
    (edited_reference / "pom.xml").write_text((edited_reference / "pom.xml").read_text(encoding="utf-8").replace("2.18.0", "2.17.0"), encoding="utf-8")
    assert _dir_digest(edited_reference) != env["REFERENCE_DIGEST"]


def test_expected_result_line_is_what_the_goal_asks_for() -> None:
    env = _env(FIXTURE / "fixture.env")
    line = env["EXPECTED_RESULT_LINE"]
    assert line == "[RESULT] protocolVersion=1, softwareVersion=2, dataLength=15, time=1700000000, bodyLength=15, equals=true"
    assert len("Hello, Protocol".encode("utf-8")) == 15  # bodyLength and dataLength follow from the goal's body
    assert "[RESULT] protocolVersion=X, softwareVersion=Y, dataLength=Z, time=T, bodyLength=B, equals=" in GOAL


# ---- KnowledgeGuard: the behaviour the configuration relies on ----

def _gaps(tmp_path: Path, *, offline: bool, with_skill: bool, release_known: bool) -> list:
    skills = tmp_path / "skills"
    memory = tmp_path / "memory"
    skills.mkdir()
    memory.mkdir()
    if with_skill:
        shutil.copytree(SKILL, skills / SKILL.name)
    if release_known:
        KnowledgeCache(str(memory), ttl_days=30).set_release_date("java", "org.apache.ignite:ignite-core", "2.18.0", IGNITE_218_RELEASED, "test")
    report = KnowledgeGuard(skills_dir=str(skills), cutoff_date_str="2023-12-01", offline=offline, memory_dir=str(memory), cache_ttl_days=30).check_goal(GOAL, str(tmp_path))
    return [(g["library"], g["version"]) for g in report.gaps]


def test_the_goal_names_exactly_one_versioned_library() -> None:
    assert extract_library_versions(GOAL) == [("org.apache.ignite:ignite-core", "2.18.0")]


def test_the_default_guard_would_stop_a_desktop_run_on_this_goal_even_with_the_skill(tmp_path: Path) -> None:
    # A desktop launch is always knowledge-policy strict and cannot acknowledge a gap: this is WHY the scenario sets knowledge.offline_mode.
    assert _gaps(tmp_path, offline=False, with_skill=True, release_known=True) == [("org.apache.ignite:ignite-core", "2.18.0")]


def test_offline_mode_with_the_verified_skill_clears_the_guard(tmp_path: Path) -> None:
    assert _gaps(tmp_path, offline=True, with_skill=True, release_known=False) == []


def test_offline_mode_alone_is_not_a_bypass_the_skill_is_what_resolves_the_gap(tmp_path: Path) -> None:
    assert _gaps(tmp_path, offline=True, with_skill=False, release_known=False) == [("org.apache.ignite:ignite-core", "2.18.0")]


# ---- the skill through Kriya ----

def test_the_skill_loads_through_skillservice_and_is_selected_for_the_goal(tmp_path: Path) -> None:
    config_path = _rendered_config(tmp_path)
    skills_dir = tmp_path / "work" / "skills"
    skills_dir.mkdir()
    shutil.copytree(SKILL, skills_dir / SKILL.name)
    workspace = tmp_path / "work" / "workspace"
    workspace.mkdir()
    config = AppConfig.model_validate(yaml.safe_load(config_path.read_text(encoding="utf-8")))
    descriptors, total = SkillService(config, str(workspace)).list()
    assert total == 1 and [d.id for d in descriptors] == ["ignite-java17"]
    descriptor = descriptors[0]
    assert descriptor.verified is True and descriptor.verified_context == "org.apache.ignite:ignite-core 2.18.0" and descriptor.rule_count == 44 and descriptor.supported_versions == "*"
    engine = SkillEngine.from_config(config, workspace_path=str(workspace))
    engine.discover_and_load()
    skill = engine.get_skill("ignite-java17")
    assert mentions_term(GOAL, skill.name) or any(mentions_term(GOAL, tag) for tag in skill.tags)  # the workflow's own relevance predicate
    rules = " ".join(skill.rules)
    for rule_fact in ("exec:exec", "--add-opens=java.base/java.nio=ALL-UNNAMED", "--add-opens=java.base/java.util=ALL-UNNAMED", "EXACTLY ONE", "try (ConfigurableApplicationContext", "ignite-spring"):
        assert rule_fact in rules, rule_fact


def test_negative_control_without_the_skill_directory_nothing_is_selected(tmp_path: Path) -> None:
    config = AppConfig.model_validate(yaml.safe_load(_rendered_config(tmp_path).read_text(encoding="utf-8")))
    (tmp_path / "work" / "skills").mkdir()
    (tmp_path / "work" / "workspace").mkdir()
    descriptors, total = SkillService(config, str(tmp_path / "work" / "workspace")).list()
    assert total == 0 and descriptors == []


# ---- the reference candidate: the structural contract ----

def _ref(*parts: str) -> str:
    return (REFERENCE.joinpath(*parts)).read_text(encoding="utf-8")


def test_reference_pom_satisfies_the_build_contract() -> None:
    pom = _ref("pom.xml")
    assert "<maven.compiler.source>17</maven.compiler.source>" in pom and "<maven.compiler.target>17</maven.compiler.target>" in pom
    for artifact in ("ignite-core", "ignite-spring"):
        assert re.search(rf"<artifactId>{artifact}</artifactId>\s*<version>\$\{{ignite\.version\}}</version>", pom), artifact
    assert "<ignite.version>2.18.0</ignite.version>" in pom
    assert re.search(r"<artifactId>junit-jupiter</artifactId>\s*<version>5\.8\.2</version>\s*<scope>test</scope>", pom)
    assert "<goal>" not in pom and "exec:java" not in pom and "<mainClass>" not in pom and "jvmArguments" not in pom  # exec:exec only: exec:java cannot apply JVM flags
    assert "<executable>java</executable>" in pom and "<classpath/>" in pom
    assert pom.rstrip().index("<argument>${exec.mainClass}</argument>") > pom.index("<classpath/>")  # the main class is the FINAL argument, overridable
    for required in ("--add-opens=java.base/java.nio=ALL-UNNAMED", "--add-opens=java.base/java.util=ALL-UNNAMED"):  # MEASURED required on Java 17 and 21 (QUALIFICATION.md section 6; raw output in evidence/)
        assert required in pom
    assert pom.count("--add-opens=") == 24  # the full documented Ignite list from the verified skill (22 of them are optional for this workload)


def test_reference_spring_xml_uses_exactly_one_start_mechanism() -> None:
    xml = _ref("src", "main", "resources", "ignite-config.xml")
    main = _ref("src", "main", "java", "com", "example", "Main.java")
    assert 'class="org.apache.ignite.IgniteSpringBean"' in xml and 'id="igniteNode"' in xml
    assert "Ignition.start" not in main  # never both: that starts two nodes under one instance name
    assert 'getBean("igniteNode")' in main and "try (ConfigurableApplicationContext" in main  # by id, and the context is closed so the JVM can exit


def test_reference_main_really_uses_protocol_parser_and_the_cache() -> None:
    main = _ref("src", "main", "java", "com", "example", "Main.java")
    assert "IgniteCache<Integer, Protocol> cache" in main  # explicitly typed, as in the staged Stage 2 evidence
    assert "cache.put(1, decoded)" in main and "Protocol fromCache = cache.get(1)" in main
    assert "parser.decode(parser.encode(original))" not in main and "parser.decode(encoded)" in main
    assert "original.equals(fromCache)" in main  # the comparison is against what came back FROM THE CACHE
    assert main.index("cache.put(") < main.index("cache.get(") < main.index("[RESULT]")
    assert "fromCache.getDataLength()" in main  # the printed values are the cache's, not the in-memory original's


def test_reference_test_is_meaningful_not_vacuous() -> None:
    test = _ref("src", "test", "java", "com", "example", "protocol", "ProtocolParserTest.java")
    assert len(re.findall(r"@Test", test)) == int(_env(FIXTURE / "fixture.env")["REFERENCE_TEST_COUNT"])
    assert "assertTrue(true)" not in test and "assertTrue(1" not in test and "assertEquals(true, true)" not in test
    assert "parser.decode(parser.encode(original))" in test and "assertEquals(original, decoded)" in test  # a real round trip
    assert "assertArrayEquals(new byte[] {1, 2, 0, 0, 3, 1, 2, 3, 4, 9, 8, 7}, encoded)" in test  # pins the big-endian header bytes
    assert "assertThrows(IllegalArgumentException.class" in test  # a malformed input is rejected


def test_reference_protocol_equality_compares_the_body_by_content() -> None:
    protocol = _ref("src", "main", "java", "com", "example", "protocol", "Protocol.java")
    assert "Arrays.equals(body, other.body)" in protocol and "Arrays.hashCode(body)" in protocol
    assert "public Protocol()" in protocol  # a no-arg constructor: the shape the historical staged build used as an Ignite cache value


# ---- the qualification helper's own constants ----

def _helper():
    spec = importlib.util.spec_from_file_location("qualify_spring_ignite", DEMO_V2 / "scripts" / "qualify_spring_ignite.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_ignite_type_id_matches_the_measured_marshaller_file_name() -> None:
    helper = _helper()
    assert helper.ignite_type_id("com.example.protocol.Protocol") == -165319811  # MEASURED: ignite/work/db/marshaller/-165319811.classname0 holds com.example.protocol.Protocol
    assert helper.ignite_type_id("com.example.protocol.ProtocolParser") != -165319811  # negative control: a different class has a different id


# ---- reset keeps the folder genuinely empty (greenfield) ----

def _run_reset(work: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "DEMO_V2_WORK": str(work), "KRIYA_BIN": str(work / "no-such-kriya")}  # no authority revoke: this test must never touch the operator's real approvals
    return subprocess.run(["bash", str(DEMO_V2 / "scripts" / "reset_demo.sh"), "--scenario", "04"], capture_output=True, text=True, check=False, timeout=120, env=env)


def test_reset_gives_an_empty_workspace_and_a_verified_skill_copy_and_never_the_reference(tmp_path: Path) -> None:
    work = tmp_path / "work"
    result = _run_reset(work)
    assert result.returncode == 0, result.stderr
    workspace = work / "scenario-04" / "workspace"
    assert workspace.is_dir() and list(workspace.iterdir()) == []  # EMPTY: no source, no pom.xml, no test, no .git, no kriya.yaml (prepare_demo.sh adds only kriya.yaml)
    skills = work / "scenario-04" / "skills"
    assert [p.name for p in skills.iterdir()] == ["ignite-java17"]
    assert _dir_digest(skills / "ignite-java17") == _env(FIXTURE / "fixture.env")["SKILL_DIGEST"]
    outside_skills = {p.name for p in (work / "scenario-04").rglob("*") if skills not in p.parents}  # the skill ships its own examples/pom.xml; that is knowledge, not the application
    assert not ({"Protocol.java", "ProtocolParser.java", "Main.java", "pom.xml", "ignite-config.xml"} & outside_skills)  # the application is generated at run time


def test_reset_is_idempotent_and_restores_a_dirtied_workspace_and_skill_copy(tmp_path: Path) -> None:
    work = tmp_path / "work"
    assert _run_reset(work).returncode == 0
    scenario = work / "scenario-04"
    (scenario / "workspace" / "leftover.txt").write_text("from an earlier run")
    (scenario / "skills" / "ignite-java17" / "skill.yaml").write_text("verification_gap_acknowledged: true\n")
    (scenario / "state").mkdir(exist_ok=True)
    (scenario / "state" / "old.db").write_text("x")
    assert _run_reset(work).returncode == 0
    assert list((scenario / "workspace").iterdir()) == []
    assert _dir_digest(scenario / "skills" / "ignite-java17") == _env(FIXTURE / "fixture.env")["SKILL_DIGEST"]
    assert list((scenario / "state").iterdir()) == []


def test_reset_refuses_when_the_skill_in_the_repository_was_edited(tmp_path: Path) -> None:
    # Negative control for the digest guard: run the same reset against a throw-away copy of demo-v2 whose fixture.env records a different digest.
    fake_repo = tmp_path / "repo"
    shutil.copytree(DEMO_V2, fake_repo / "demo-v2", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(SKILL, fake_repo / "skills" / "ignite-java17")
    env_file = fake_repo / "demo-v2" / "fixtures" / "spring-ignite-protocol" / "fixture.env"
    env_file.write_text(re.sub(r"^SKILL_DIGEST=.*$", "SKILL_DIGEST=" + "0" * 64, env_file.read_text(encoding="utf-8"), flags=re.MULTILINE), encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=fake_repo, check=True)
    env = {**os.environ, "DEMO_V2_WORK": str(tmp_path / "work"), "KRIYA_BIN": str(tmp_path / "no-such-kriya")}
    result = subprocess.run(["bash", str(fake_repo / "demo-v2" / "scripts" / "reset_demo.sh"), "--scenario", "04"], capture_output=True, text=True, check=False, timeout=120, env=env)
    assert result.returncode != 0 and "does not have its recorded digest" in result.stderr


@pytest.mark.parametrize("script", ["prepare_demo.sh", "reset_demo.sh", "verify_demo.sh", "launch_gui.sh"])
def test_scenario_04_is_an_accepted_scenario_of_every_shared_script(script: str, tmp_path: Path) -> None:
    help_text = subprocess.run(["bash", str(DEMO_V2 / "scripts" / script), "--help"], capture_output=True, text=True, check=False, timeout=30,
                               env={**os.environ, "DEMO_V2_WORK": str(tmp_path / "work")}).stdout
    assert re.search(r"--scenario 00\|01\|02\|03\|04", help_text), help_text


# ---- verify_demo.sh --phase post for scenario 04 (needs Maven + the offline repository; skipped where they are absent) ----

_HAS_OFFLINE_STACK = shutil.which("mvn") is not None and (Path.home() / ".m2/repository/org/apache/ignite/ignite-core/2.18.0/ignite-core-2.18.0.jar").is_file() \
    and (Path.home() / ".m2/repository/org/junit/jupiter/junit-jupiter/5.8.2/junit-jupiter-5.8.2.pom").is_file()


def _post(work: Path) -> list:
    """The PASS/FAIL lines of `verify_demo.sh --scenario 04 --phase post` (the tracked-tree line is about this checkout, not the project, and is excluded)."""
    result = subprocess.run(["bash", str(DEMO_V2 / "scripts" / "verify_demo.sh"), "--scenario", "04", "--phase", "post"], capture_output=True, text=True, check=False, timeout=300,
                            env={**os.environ, "DEMO_V2_WORK": str(work), "KRIYA_BIN": str(work / "no-such-kriya")})
    return [line for line in result.stdout.splitlines() if line.startswith(("PASS", "FAIL")) and "tracked tree" not in line]


def _project(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    assert _run_reset(work).returncode == 0
    workspace = work / "scenario-04" / "workspace"
    shutil.copytree(REFERENCE, workspace, dirs_exist_ok=True)
    (work / "scenario-04" / "config").mkdir(exist_ok=True)
    (work / "scenario-04" / "config" / "kriya.yaml").write_text((SCENARIO / "config" / "kriya.template.yaml").read_text(encoding="utf-8").replace("@WORK@", str(work / "scenario-04")), encoding="utf-8")
    (work / "scenario-04" / "state" / "run.marker").write_text("x")
    return work


@pytest.mark.skipif(not _HAS_OFFLINE_STACK, reason="needs mvn and the offline Maven repository (Ignite 2.18.0, JUnit 5.8.2)")
def test_post_check_passes_on_the_reference_and_leaves_the_project_untouched(tmp_path: Path) -> None:
    work = _project(tmp_path)
    workspace = work / "scenario-04" / "workspace"
    before = _dir_digest(workspace)
    lines = _post(work)
    assert lines and not [line for line in lines if line.startswith("FAIL")], lines
    for needed in ("Ignite itself logged", "marshaller registry names a Protocol", "executed, none skipped", "printed exactly"):
        assert any(needed in line and line.startswith("PASS") for line in lines), needed
    assert _dir_digest(workspace) == before  # the evidence workspace is never rebuilt, cleaned or written to


@pytest.mark.skipif(not _HAS_OFFLINE_STACK, reason="needs mvn and the offline Maven repository (Ignite 2.18.0, JUnit 5.8.2)")
def test_post_check_rejects_a_program_that_prints_the_line_without_ignite(tmp_path: Path) -> None:
    work = _project(tmp_path)
    main = work / "scenario-04" / "workspace" / "src" / "main" / "java" / "com" / "example" / "Main.java"
    main.write_text('package com.example;\npublic class Main { public static void main(String[] a) { System.out.println("' + _env(FIXTURE / "fixture.env")["EXPECTED_RESULT_LINE"] + '"); } }\n', encoding="utf-8")
    failures = [line for line in _post(work) if line.startswith("FAIL")]
    assert any("Ignite start/stop" in line for line in failures) and any("marshaller" in line for line in failures), failures


@pytest.mark.skipif(not _HAS_OFFLINE_STACK, reason="needs mvn and the offline Maven repository (Ignite 2.18.0, JUnit 5.8.2)")
def test_post_check_rejects_missing_tests_and_a_leftover_ignite_directory(tmp_path: Path) -> None:
    work = _project(tmp_path)
    workspace = work / "scenario-04" / "workspace"
    shutil.rmtree(workspace / "src" / "test")
    (workspace / "ignite").mkdir()
    failures = [line for line in _post(work) if line.startswith("FAIL")]
    assert any("no test under src/test/java" in line for line in failures) and any("ignite/ work directory is left" in line for line in failures), failures
