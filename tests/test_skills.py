import os
from unittest.mock import patch

from click.testing import CliRunner

from kriya.cli import main
from kriya.skills.skill import Skill, SkillEngine
from kriya.workflow.skill_extraction import _skill_staleness_warning


def _make_skill(**overrides):
    defaults = dict(name="qpid", description="Qpid broker skill", tags=["qpid"])
    defaults.update(overrides)
    return Skill(**defaults)


def test_skill_staleness_warning_fires_on_version_drift():
    """Architectural add-on from a 2026-08-12 SME review: verified_context's
    own field docstring says "a pinned version gets yanked, a new major
    version changes the config shape" as the reason it's worth tracking, but
    nothing read it back to check until now."""
    skill = _make_skill(verified_context="qpid 9.2.1", verified_at="2026-08-01")
    warning = _skill_staleness_warning(skill, "qpid", "9.3.0")
    assert warning is not None
    assert "9.2.1" in warning
    assert "9.3.0" in warning
    assert "2026-08-01" in warning


def test_skill_staleness_warning_silent_on_matching_version():
    skill = _make_skill(verified_context="qpid 9.2.1")
    assert _skill_staleness_warning(skill, "qpid", "9.2.1") is None


def test_skill_staleness_warning_silent_when_never_verified():
    skill = _make_skill(verified_context=None)
    assert _skill_staleness_warning(skill, "qpid", "9.3.0") is None


def test_skill_staleness_warning_silent_when_verified_context_has_no_version():
    skill = _make_skill(verified_context="version unspecified")
    assert _skill_staleness_warning(skill, "qpid", "9.3.0") is None


def test_skill_staleness_warning_silent_for_a_different_library():
    # verified_context is for a DIFFERENT library than the one being checked -
    # must not be misread as a version mismatch for "qpid".
    skill = _make_skill(verified_context="ignite-core 2.18.0")
    assert _skill_staleness_warning(skill, "qpid", "9.3.0") is None


def test_skill_engine_prefers_explicit_supplied_dir_over_cwd_guess(tmp_path, monkeypatch):
    """Regression test for a real bug found live, 2026-08-12 (SME architecture
    review): discover_and_load() documents "later paths override earlier
    ones" as its precedence model, but SkillEngine.__init__ previously added
    the explicitly supplied skills directory BEFORE the implicit CWD-guess
    directory (os.getcwd()/skills) - so a same-named skill present in both
    silently let the implicit guess win over an explicitly configured
    paths.skills, the opposite of expected precedence whenever the two
    differ (e.g. supplied_dir is an absolute path elsewhere)."""
    cwd_dir = tmp_path / "cwd_root"
    cwd_dir.mkdir()
    monkeypatch.chdir(cwd_dir)

    # The implicit CWD-guess directory (os.getcwd()/skills).
    cwd_skill_folder = cwd_dir / "skills" / "widgetlib"
    cwd_skill_folder.mkdir(parents=True)
    (cwd_skill_folder / "skill.yaml").write_text("name: widgetlib\ndescription: CWD guess version\n")
    (cwd_skill_folder / "rules.txt").write_text("CWD guess rule.\n")

    # The explicitly configured (supplied) skills directory - a different
    # absolute path, matching a project that sets paths.skills explicitly.
    explicit_skill_folder = tmp_path / "explicit_config_skills" / "widgetlib"
    explicit_skill_folder.mkdir(parents=True)
    (explicit_skill_folder / "skill.yaml").write_text("name: widgetlib\ndescription: Explicit config version\n")
    (explicit_skill_folder / "rules.txt").write_text("Explicit config rule.\n")

    se = SkillEngine(str(tmp_path / "explicit_config_skills"), load_global=True)
    se.discover_and_load()

    skill = se.get_skill("widgetlib")
    assert skill.description == "Explicit config version"
    assert skill.rules == ["Explicit config rule."]


def test_skills_lifecycle(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    
    se = SkillEngine(str(skills_dir), load_global=False)
    
    # 1. Create skill skeleton
    path = se.create_skill_skeleton("FastAPI App")
    assert os.path.exists(path)
    assert os.path.exists(os.path.join(path, "skill.yaml"))
    assert os.path.exists(os.path.join(path, "instructions.md"))
    
    # Let's add rules and examples to the created skeleton folder
    with open(os.path.join(path, "rules.txt"), "w") as f:
        f.write("Always use async def\nNo sync dependencies")
    
    with open(os.path.join(path, "examples", "main.py"), "w") as f:
        f.write("# FastAPI example")
        
    # 2. Discover and Load
    se_load = SkillEngine(str(skills_dir), load_global=False)
    se_load.discover_and_load()
    
    skills = se_load.list_skills()
    assert len(skills) == 1
    
    skill = se_load.get_skill("FastAPI App")
    assert isinstance(skill, Skill)
    assert skill.name == "FastAPI App"
    assert "FastAPI App" in skill.description
    assert skill.rules == ["Always use async def", "No sync dependencies"]
    assert skill.examples == {"main.py": "# FastAPI example"}
    assert "fastapi-app" in skill.tags
    
    # Test find by tag
    tagged = se_load.find_skills_by_tag("fastapi-app")
    assert len(tagged) == 1
    assert tagged[0].name == "FastAPI App"

def test_is_version_supported():
    from kriya.skills.skill import is_version_supported
    
    # 1. Base cases
    assert is_version_supported("2.18.0", "*") is True
    assert is_version_supported("2.18.0", "") is True
    
    # 2. Operators
    assert is_version_supported("2.18.0", "==2.18.0") is True
    assert is_version_supported("2.18.0", "!=2.15.0") is True
    assert is_version_supported("2.18.0", ">=2.15.0") is True
    assert is_version_supported("2.18.0", "<=2.20.0") is True
    assert is_version_supported("2.18.0", ">2.17") is True
    assert is_version_supported("2.18.0", "<3.0") is True
    
    # 3. Mismatches
    assert is_version_supported("2.18.0", "==2.15.0") is False
    assert is_version_supported("2.18.0", "<2.15.0") is False
    assert is_version_supported("2.18.0", ">3.0.0") is False
    
    # 4. Range combinations
    assert is_version_supported("2.18.0", ">=2.15.0 <3.0.0") is True
    assert is_version_supported("3.1.0", ">=2.15.0 <3.0.0") is False


def test_is_version_supported_comma_separated_range_applies_both_bounds():
    """Regression test for a finding from the 2026-08-12 SME review: a
    comma-separated range with no space (">=2.15.0,<3.0.0", a natural way to
    write it) used to become ONE token - re.match only ever consumed the
    FIRST bound and silently ignored everything after the comma, so the
    upper bound never applied at all."""
    from kriya.skills.skill import is_version_supported

    assert is_version_supported("2.18.0", ">=2.15.0,<3.0.0") is True
    # The real bug: without this fix, this would incorrectly be True too,
    # since the "<3.0.0" upper bound was silently dropped.
    assert is_version_supported("3.1.0", ">=2.15.0,<3.0.0") is False


def test_parse_version_parts_preserves_the_numeric_component_a_qualifier_is_attached_to():
    """Regression test for a finding from the 2026-08-12 SME review: a
    trailing-non-digit strip over the WHOLE string only works when the
    qualifier's own last character is a non-digit ("1.2.3-SNAPSHOT" - the
    old code already handled this case correctly, since "-SNAPSHOT" is
    itself all non-digit chars at the string's end). It silently breaks
    when the qualifier ENDS in a digit instead (e.g. "rc1" in "1.0.5-rc1"):
    nothing is stripped, the affected part's own int("5-rc1") then raises
    and gets coerced to 0 - not just losing the qualifier, zeroing out an
    otherwise-correct patch number entirely."""
    from kriya.skills.skill import parse_version_parts

    assert parse_version_parts("1.0.5-rc1") == (1, 0, 5)
    assert parse_version_parts("1.2.3-SNAPSHOT") == (1, 2, 3)
    assert parse_version_parts("2.18.0") == (2, 18, 0)


def _make_promote_project(tmp_path):
    """Sets up a project-local skills dir with an already-approved auto-<repo> lesson,
    and a separate 'global' skills dir (standing in for the real shared library, always
    accessed only via a patched _get_global_skills_dir so tests never touch the real
    product skill library) with a pre-existing target skill."""
    project_skills = tmp_path / "project_skills"
    (project_skills / "auto-myrepo").mkdir(parents=True)
    (project_skills / "auto-myrepo" / "rules.txt").write_text(
        "Cache name arguments must be a String literal.\nUse SLF4J for logging.\n"
    )

    global_skills = tmp_path / "global_skills"
    (global_skills / "qpid").mkdir(parents=True)
    (global_skills / "qpid" / "rules.txt").write_text("Use qpid-broker-core 9.2.1.\n")

    config_file = tmp_path / "kriya.yaml"
    config_file.write_text(f"paths:\n  skills: {project_skills}\n")
    return str(config_file), project_skills, global_skills


def test_skills_promote_single_rule(tmp_path):
    config_file, _project_skills, global_skills = _make_promote_project(tmp_path)
    runner = CliRunner()

    with patch("kriya.cli._get_global_skills_dir", return_value=str(global_skills)):
        result = runner.invoke(
            main,
            ["--config", config_file, "skills", "promote", "auto-myrepo", "qpid",
             "--rule", "Use SLF4J for logging."],
            input="y\n"
        )

    assert result.exit_code == 0, result.output
    target_rules = (global_skills / "qpid" / "rules.txt").read_text()
    assert "Use SLF4J for logging." in target_rules
    # The unrelated rule must NOT have been promoted.
    assert "Cache name arguments" not in target_rules

def test_skills_promote_declined_confirmation_does_not_write(tmp_path):
    config_file, _project_skills, global_skills = _make_promote_project(tmp_path)
    runner = CliRunner()

    with patch("kriya.cli._get_global_skills_dir", return_value=str(global_skills)):
        result = runner.invoke(
            main,
            ["--config", config_file, "skills", "promote", "auto-myrepo", "qpid", "--all"],
            input="n\n"
        )

    assert result.exit_code == 0
    target_rules = (global_skills / "qpid" / "rules.txt").read_text()
    assert "Cache name arguments" not in target_rules
    assert "Use SLF4J for logging." not in target_rules

def test_skills_promote_all_skips_already_present_rules(tmp_path):
    config_file, project_skills, global_skills = _make_promote_project(tmp_path)
    # Pre-seed the target with one of the two source rules already present.
    (global_skills / "qpid" / "rules.txt").write_text(
        "Use qpid-broker-core 9.2.1.\nUse SLF4J for logging.\n"
    )
    runner = CliRunner()

    with patch("kriya.cli._get_global_skills_dir", return_value=str(global_skills)):
        result = runner.invoke(
            main,
            ["--config", config_file, "skills", "promote", "auto-myrepo", "qpid", "--all"],
            input="y\n"
        )

    assert result.exit_code == 0, result.output
    target_rules = (global_skills / "qpid" / "rules.txt").read_text()
    assert target_rules.count("Use SLF4J for logging.") == 1
    assert "Cache name arguments must be a String literal." in target_rules

def test_skills_promote_unknown_target_skill_fails(tmp_path):
    config_file, _project_skills, global_skills = _make_promote_project(tmp_path)
    runner = CliRunner()

    with patch("kriya.cli._get_global_skills_dir", return_value=str(global_skills)):
        result = runner.invoke(
            main,
            ["--config", config_file, "skills", "promote", "auto-myrepo", "no-such-skill", "--all"],
        )

    assert result.exit_code != 0
    assert "does not exist" in result.output

def test_skills_promote_requires_rule_or_all(tmp_path):
    config_file, _project_skills, global_skills = _make_promote_project(tmp_path)
    runner = CliRunner()

    with patch("kriya.cli._get_global_skills_dir", return_value=str(global_skills)):
        result = runner.invoke(
            main,
            ["--config", config_file, "skills", "promote", "auto-myrepo", "qpid"],
        )

    assert result.exit_code != 0

def test_skills_promote_marks_target_skill_verified(tmp_path):
    config_file, _project_skills, global_skills = _make_promote_project(tmp_path)
    # _make_promote_project's target skill has no skill.yaml - give it one so
    # verification-marking has something real to update.
    (global_skills / "qpid" / "skill.yaml").write_text("name: qpid\ndescription: Test\n")
    runner = CliRunner()

    with patch("kriya.cli._get_global_skills_dir", return_value=str(global_skills)):
        result = runner.invoke(
            main,
            ["--config", config_file, "skills", "promote", "auto-myrepo", "qpid",
             "--rule", "Use SLF4J for logging."],
            input="y\n"
        )

    assert result.exit_code == 0, result.output
    se = SkillEngine(str(global_skills), load_global=False)
    se.discover_and_load()
    skill = se.get_skill("qpid")
    assert skill.verified is True
    assert skill.verified_context == "promoted from 'auto-myrepo'"


def _make_local_project(tmp_path, skill_name="widgetlib", verified=False, gap_acknowledged=False):
    """A project-local skills dir (not the shared/global one) with a single skill,
    for testing kriya skills list/show/unverify without touching the real repo's
    skills - those commands load with load_global=True by default, so the real repo's
    skills will also appear in output; tests only assert on their own skill's line."""
    project_skills = tmp_path / "project_skills"
    skill_folder = project_skills / skill_name
    skill_folder.mkdir(parents=True)
    yaml_lines = [f"name: {skill_name}", "description: Test skill.", f"tags: [{skill_name}]"]
    if verified:
        yaml_lines += ["verified: true", "verified_context: widgetlib 2.0.0", "verified_at: '2026-01-01'"]
    if gap_acknowledged:
        yaml_lines += ["verification_gap_acknowledged: true"]
    (skill_folder / "skill.yaml").write_text("\n".join(yaml_lines) + "\n")
    (skill_folder / "rules.txt").write_text("Existing rule.\n")

    config_file = tmp_path / "kriya.yaml"
    config_file.write_text(f"paths:\n  skills: {project_skills}\n")
    return str(config_file), project_skills

def test_skills_unverify_resets_verified_skill(tmp_path):
    config_file, project_skills = _make_local_project(tmp_path, verified=True)
    runner = CliRunner()

    result = runner.invoke(main, ["--config", config_file, "skills", "unverify", "widgetlib"])

    assert result.exit_code == 0, result.output
    assert "reset to unverified" in result.output
    se = SkillEngine(str(project_skills), load_global=False)
    se.discover_and_load()
    skill = se.get_skill("widgetlib")
    assert skill.verified is False
    assert skill.verification_gap_acknowledged is False

def test_skills_unverify_already_unverified_is_a_noop(tmp_path):
    config_file, _project_skills = _make_local_project(tmp_path, verified=False)
    runner = CliRunner()

    result = runner.invoke(main, ["--config", config_file, "skills", "unverify", "widgetlib"])

    assert result.exit_code == 0
    assert "already unverified" in result.output

def test_skills_unverify_resets_stuck_gap_acknowledged_even_when_already_unverified(tmp_path):
    """Regression test for a real bug caught live this session: a skill can be
    verified=False AND verification_gap_acknowledged=True at the same time (the
    user was once asked to strengthen it and declined, but it was never later
    verified) - in that exact state, the old code's `if not skill.verified: ...
    return` fired before ever calling mark_unverified(), so
    verification_gap_acknowledged stayed stuck True forever and the skill-gap
    prompt could never fire again for that skill. Worked around live by hand-
    editing skill.yaml directly since the CLI command couldn't actually help."""
    config_file, project_skills = _make_local_project(tmp_path, verified=False, gap_acknowledged=True)
    runner = CliRunner()

    result = runner.invoke(main, ["--config", config_file, "skills", "unverify", "widgetlib"])

    assert result.exit_code == 0, result.output
    assert "reset to unverified" in result.output
    se = SkillEngine(str(project_skills), load_global=False)
    se.discover_and_load()
    skill = se.get_skill("widgetlib")
    assert skill.verified is False
    assert skill.verification_gap_acknowledged is False

def test_skills_unverify_unknown_skill_fails(tmp_path):
    config_file, _project_skills = _make_local_project(tmp_path, verified=False)
    runner = CliRunner()

    result = runner.invoke(main, ["--config", config_file, "skills", "unverify", "no-such-skill"])

    assert result.exit_code != 0
    assert "not found" in result.output

def test_skills_list_shows_verification_status(tmp_path):
    config_file, _project_skills = _make_local_project(tmp_path, skill_name="verifiedwidget", verified=True)
    runner = CliRunner()

    result = runner.invoke(main, ["--config", config_file, "skills", "list"])

    assert result.exit_code == 0, result.output
    assert "verifiedwidget" in result.output
    matching_line = next(line for line in result.output.splitlines() if "verifiedwidget" in line)
    assert "[VERIFIED - widgetlib 2.0.0, on 2026-01-01]" in matching_line

def test_conflict_registry_round_trips_resolution(tmp_path):
    from kriya.skills.skill import find_conflict_resolution, record_conflict_resolution

    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    assert find_conflict_resolution(str(skills_dir), "qpid", "Use port 5672.", "artemis", "Use port 5673.") is None

    record_conflict_resolution(str(skills_dir), "qpid", "Use port 5672.", "artemis", "Use port 5673.", "prefer_a")

    assert find_conflict_resolution(str(skills_dir), "qpid", "Use port 5672.", "artemis", "Use port 5673.") == "prefer_a"

def test_conflict_registry_lookup_is_order_independent(tmp_path):
    from kriya.skills.skill import find_conflict_resolution, record_conflict_resolution

    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    record_conflict_resolution(str(skills_dir), "qpid", "Use port 5672.", "artemis", "Use port 5673.", "prefer_a")

    # Looked up with the two skills swapped, the resolution must flip accordingly -
    # "prefer_a" (qpid) becomes "prefer_b" when qpid is now the second argument.
    assert find_conflict_resolution(str(skills_dir), "artemis", "Use port 5673.", "qpid", "Use port 5672.") == "prefer_b"

def test_conflict_registry_overwrites_prior_resolution_for_same_pair(tmp_path):
    from kriya.skills.skill import find_conflict_resolution, load_conflict_resolutions, record_conflict_resolution

    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    record_conflict_resolution(str(skills_dir), "qpid", "Use port 5672.", "artemis", "Use port 5673.", "prefer_a")
    record_conflict_resolution(str(skills_dir), "qpid", "Use port 5672.", "artemis", "Use port 5673.", "both_ok")

    assert find_conflict_resolution(str(skills_dir), "qpid", "Use port 5672.", "artemis", "Use port 5673.") == "both_ok"
    assert len(load_conflict_resolutions(str(skills_dir))) == 1

def test_conflict_registry_distinct_rule_pairs_tracked_separately(tmp_path):
    from kriya.skills.skill import find_conflict_resolution, record_conflict_resolution

    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    record_conflict_resolution(str(skills_dir), "qpid", "Use port 5672.", "artemis", "Use port 5673.", "prefer_a")

    # A different conflicting rule pair between the same two skills is a distinct,
    # unresolved decision - must not be silently covered by the earlier resolution.
    assert find_conflict_resolution(str(skills_dir), "qpid", "Use SLF4J.", "artemis", "Use Log4j.") is None


def test_rule_provenance_round_trips(tmp_path):
    from kriya.skills.skill import load_rule_provenance, record_rule_provenance

    skill_dir = tmp_path / "widgetlib"
    skill_dir.mkdir()

    assert load_rule_provenance(str(skill_dir)) == []

    record_rule_provenance(str(skill_dir), "The magic constant is 42.", "human_text")
    records = load_rule_provenance(str(skill_dir))
    assert len(records) == 1
    assert records[0]["text"] == "The magic constant is 42."
    assert records[0]["verified"] is False
    assert records[0]["source"] == "human_text"
    assert "added_at" in records[0]

def test_rule_provenance_overwrites_record_for_same_text(tmp_path):
    from kriya.skills.skill import load_rule_provenance, record_rule_provenance

    skill_dir = tmp_path / "widgetlib"
    skill_dir.mkdir()
    record_rule_provenance(str(skill_dir), "Rule A.", "human_text")
    record_rule_provenance(str(skill_dir), "Rule A.", "live_lookup:https://example.com")

    records = load_rule_provenance(str(skill_dir))
    assert len(records) == 1
    assert records[0]["source"] == "live_lookup:https://example.com"

def test_mark_rules_verified_only_flips_existing_records(tmp_path):
    from kriya.skills.skill import load_rule_provenance, mark_rules_verified, record_rule_provenance

    skill_dir = tmp_path / "widgetlib"
    skill_dir.mkdir()
    record_rule_provenance(str(skill_dir), "Rule A.", "human_text")
    record_rule_provenance(str(skill_dir), "Rule B.", "human_text")

    # "Rule C." has no provenance record (pre-existing/untracked content) - marking it
    # verified must be a no-op, not create a new record out of thin air.
    mark_rules_verified(str(skill_dir), ["Rule A.", "Rule C."])

    records = {r["text"]: r for r in load_rule_provenance(str(skill_dir))}
    assert records["Rule A."]["verified"] is True
    assert "verified_at" in records["Rule A."]
    assert records["Rule B."]["verified"] is False
    assert "Rule C." not in records

def test_mark_rules_verified_noop_when_no_provenance_file(tmp_path):
    from kriya.skills.skill import mark_rules_verified

    skill_dir = tmp_path / "widgetlib"
    skill_dir.mkdir()
    # Must not raise or create a file for a skill with no tracked rules at all.
    mark_rules_verified(str(skill_dir), ["Some rule."])
    assert not (skill_dir / "rule_provenance.json").exists()


def test_skills_show_displays_verification_provenance(tmp_path):
    config_file, _project_skills = _make_local_project(tmp_path, skill_name="verifiedwidget", verified=True)
    runner = CliRunner()

    result = runner.invoke(main, ["--config", config_file, "skills", "show", "verifiedwidget"])

    assert result.exit_code == 0, result.output
    assert "Verified:    yes" in result.output
    assert "widgetlib 2.0.0" in result.output
    assert "kriya skills unverify verifiedwidget" in result.output

def test_skills_show_flags_unverified_rules_individually(tmp_path):
    from kriya.skills.skill import record_rule_provenance

    config_file, project_skills = _make_local_project(tmp_path, skill_name="widgetlib", verified=False)
    (project_skills / "widgetlib" / "rules.txt").write_text("Existing rule.\nFreshly extracted rule.\n")
    record_rule_provenance(str(project_skills / "widgetlib"), "Freshly extracted rule.", "human_text")

    runner = CliRunner()
    result = runner.invoke(main, ["--config", config_file, "skills", "show", "widgetlib"])

    assert result.exit_code == 0, result.output
    assert "- Existing rule." in result.output
    assert "- Freshly extracted rule." in result.output
    assert "[unverified]" in result.output
    # The pre-existing rule's line must not be flagged - only the tracked one.
    existing_line = next(line for line in result.output.splitlines() if "Existing rule." in line)
    assert "[unverified]" not in existing_line

