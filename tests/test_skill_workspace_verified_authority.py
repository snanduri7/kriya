"""B3 (Wave-2): a workspace-supplied skill cannot self-attest verification.

MEASURED/TRACED at 04c8db4: ``SkillEngine.discover_and_load`` read ``verified=bool(meta.get("verified"))`` from any
``skill.yaml`` it found, so a cloned repository's ``skills/<name>/skill.yaml`` with ``verified: true`` loaded as verified:
its unverified rules were presented as trusted, ``is_effectively_verified`` held, and the human skill-gap prompt was suppressed.
(``bool("no")`` is also True.)

Invariant: verification is Kriya's, not the project's. A skill that lives inside the workspace carries its ``verified`` value as
untrusted metadata (``verified_claim``, display only); the authoritative ``verified`` is held only for skills outside the
workspace (the Kriya install's library, an operator-chosen directory) and for verification this run earned (held verifications).
No attestation system is introduced: a workspace skill Kriya itself verified is not honoured on the NEXT load either (it is
asked about again) until the future Kriya-owned attestation exists.

W3-1 (owner sign-off 2026-10-30): the same rule for the human skill-gap DECLINE. ``verification_gap_acknowledged`` in a workspace
``skill.yaml`` is a persisted human decision stored in project-controlled bytes; MEASURED at 1985f18 it suppressed the unverified-skill
prompt and the ``unresolved_skill_gaps`` report. Invariant: workspace bytes may not grant themselves acknowledgement authority. A
decline is honoured from ``skill.yaml`` only for a skill Kriya or the operator owns; for a workspace skill it lives only in this run
(the engine's run-local state, cleared with the held verifications) and the next run asks again. No new durable storage.
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from kriya.skills.skill import HeldSkillVerification, SkillEngine, get_global_skills_dir


def _skill(root, name="widgetlib", verified="true", extra=""):
    folder = root / name
    folder.mkdir(parents=True)
    lines = [f"name: {name}", "description: Test", f"tags: [{name}]"]
    if verified is not None:
        lines.append(f"verified: {verified}")
    (folder / "skill.yaml").write_text("\n".join(lines) + "\n" + extra)
    (folder / "rules.txt").write_text("Widgets must be printed in uppercase.\n")
    return folder


def _engine(skills_dir, workspace, **kwargs):
    engine = SkillEngine(str(skills_dir), load_global=False, workspace_path=str(workspace), **kwargs)
    engine.discover_and_load()
    return engine


def _loaded(engine, name="widgetlib"):
    return next(s for s in engine.list_skills() if s.name == name)


def test_a_workspace_skill_claiming_verified_true_is_not_verified(tmp_path):
    workspace = tmp_path / "ws"
    _skill(workspace / "skills", verified="true")
    engine = _engine(workspace / "skills", workspace)
    skill = _loaded(engine)
    assert skill.verified is False and skill.verified_claim is True
    assert engine.is_effectively_verified(skill) is False


@pytest.mark.parametrize("claim", ['"no"', '"false"', "1", "[true]", "{a: 1}", '"yes"', "null"])
def test_malformed_or_non_boolean_claims_in_the_workspace_never_verify(tmp_path, claim):
    workspace = tmp_path / "ws"
    _skill(workspace / "skills", verified=claim)
    skill = _loaded(_engine(workspace / "skills", workspace))
    assert skill.verified is False and skill.verified_claim is False


def test_a_workspace_false_claim_does_not_revoke_a_verification_this_run_earned(tmp_path):
    workspace = tmp_path / "ws"
    _skill(workspace / "skills", verified="false")
    engine = _engine(workspace / "skills", workspace)
    skill = _loaded(engine)
    assert engine.is_effectively_verified(skill) is False
    engine.hold_verifications([HeldSkillVerification(skill_name="widgetlib", context="widgetlib 2.0", rule_texts=None)])
    assert engine.is_effectively_verified(skill) is True            # Kriya-owned, earned by a passing run of THIS engine's run


def test_a_skill_outside_the_workspace_keeps_its_verified_state(tmp_path):
    workspace, external = tmp_path / "ws", tmp_path / "operator-skills"
    workspace.mkdir()
    _skill(external, verified="true")
    skill = _loaded(_engine(external, workspace))
    assert skill.verified is True and skill.verified_claim is True


def test_a_boolean_false_or_absent_flag_outside_the_workspace_is_unverified_and_strict(tmp_path):
    workspace, external = tmp_path / "ws", tmp_path / "operator-skills"
    workspace.mkdir()
    _skill(external, "absent", verified=None)
    _skill(external, "stringy", verified='"no"')                    # bool("no") is True: a non-boolean is never a verification
    engine = _engine(external, workspace)
    assert [_loaded(engine, n).verified for n in ("absent", "stringy")] == [False, False]


def test_the_kriya_install_library_keeps_its_verified_skills_even_when_the_workspace_is_the_install(tmp_path):
    install_skills = get_global_skills_dir()
    shipped = [s for s in _engine_global(tmp_path).list_skills() if s.verified]
    assert shipped, "the Kriya library ships verified skills"
    workspace_is_install = SkillEngine(str(tmp_path / "ws-skills"), load_global=True, workspace_path=install_skills.rsplit("/", 1)[0])
    workspace_is_install.discover_and_load()
    assert {s.name for s in workspace_is_install.list_skills() if s.verified} >= {s.name for s in shipped}


def _engine_global(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    engine = SkillEngine(str(tmp_path / "none"), load_global=True, workspace_path=str(workspace))
    engine.discover_and_load()
    return engine


def test_a_workspace_skill_that_kriya_marks_verified_is_asked_about_again_on_the_next_load(tmp_path):
    workspace = tmp_path / "ws"
    _skill(workspace / "skills", verified=None)
    engine = _engine(workspace / "skills", workspace)
    assert engine.mark_verified("widgetlib", "widgetlib 2.0") is True
    assert _loaded(engine).verified is True                          # this process: the engine just earned it
    engine.discover_and_load()                                       # the next load reads the file again
    assert _loaded(engine).verified is False and _loaded(engine).verified_claim is True


def test_unverify_still_resets_a_workspace_skill_whose_file_claims_verified(tmp_path, monkeypatch):
    """The operator's reset must reach the file even though the claim was never authoritative."""
    from click.testing import CliRunner

    from kriya.cli import main

    workspace = tmp_path / "ws"
    _skill(workspace / "skills", verified="true")
    (workspace / "kriya.yaml").write_text(f"paths:\n  skills: {workspace / 'skills'}\n")
    monkeypatch.chdir(workspace)
    result = CliRunner().invoke(main, ["--config", str(workspace / "kriya.yaml"), "skills", "unverify", "widgetlib"])
    assert result.exit_code == 0 and "reset to unverified" in result.output, result.output
    assert _loaded(_engine(workspace / "skills", workspace)).verified_claim is False


def test_show_names_an_unattested_claim(tmp_path, monkeypatch):
    from click.testing import CliRunner

    from kriya.cli import main

    workspace = tmp_path / "ws"
    _skill(workspace / "skills", verified="true")
    (workspace / "kriya.yaml").write_text(f"paths:\n  skills: {workspace / 'skills'}\n")
    monkeypatch.chdir(workspace)
    result = CliRunner().invoke(main, ["--config", str(workspace / "kriya.yaml"), "skills", "show", "widgetlib"])
    assert result.exit_code == 0, result.output
    assert "Verified:    no" in result.output and "cannot attest its own verification" in result.output


def test_the_run_shadow_copy_does_not_launder_a_workspace_claim(tmp_path):
    """During a run the registry is loaded from a copy under the Kriya state directory (outside the workspace): the
    durable path decides, not the copy's."""
    import subprocess

    workspace, state = tmp_path / "ws", tmp_path / "state"
    _skill(workspace / "skills", verified="true")
    for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"], ["add", "-A"], ["commit", "-qm", "i"]):
        subprocess.run(["git", *args], cwd=workspace, check=True, capture_output=True)
    state.mkdir()
    engine = _engine(workspace / "skills", workspace)
    assert engine.activate_run_shadow(str(workspace), str(state)) is True and engine.run_shadow_active
    skill = _loaded(engine)
    assert str(state) in skill.source_path                          # really loaded from the copy
    assert skill.verified is False and skill.verified_claim is True


def test_an_unknown_path_relation_fails_closed(tmp_path, monkeypatch):
    import kriya.skills.skill as skill_module
    from kriya.platform.filesystem_semantics import PathRelation

    external = tmp_path / "operator-skills"
    _skill(external, verified="true")
    (tmp_path / "ws").mkdir()
    monkeypatch.setattr(skill_module, "path_relation", lambda root, target: PathRelation.UNKNOWN)
    assert _loaded(_engine(external, tmp_path / "ws")).verified is False


# ---------------------------------------------------------------------------------------- owner controls 3, 6, 7 (B3 decision)

def test_a_verification_this_run_earned_survives_reloads_of_the_workspace_files_for_the_run(tmp_path):
    """Control 3: the run-local owner (held verifications) is not discarded merely because the skill source is workspace-local,
    and it is independent of whatever the workspace file says on any reload during the run."""
    workspace = tmp_path / "ws"
    folder = _skill(workspace / "skills", verified="false")
    engine = _engine(workspace / "skills", workspace)
    engine.hold_verifications([HeldSkillVerification(skill_name="widgetlib", context="widgetlib 2.0", rule_texts=("Widgets must be printed in uppercase.",))])
    for claim in ("false", "true", None):
        lines = ["name: widgetlib", "description: Test", "tags: [widgetlib]"] + ([f"verified: {claim}"] if claim else [])
        (folder / "skill.yaml").write_text("\n".join(lines) + "\n")
        engine.discover_and_load()
        assert engine.is_effectively_verified(_loaded(engine)) is True
    assert engine.discard_held_verifications() == 1
    assert engine.is_effectively_verified(_loaded(engine)) is False


def test_a_repository_edit_from_false_to_true_gains_no_authority(tmp_path):
    """Control 6."""
    workspace = tmp_path / "ws"
    folder = _skill(workspace / "skills", verified="false")
    engine = _engine(workspace / "skills", workspace)
    assert engine.is_effectively_verified(_loaded(engine)) is False
    (folder / "skill.yaml").write_text("name: widgetlib\ndescription: Test\ntags: [widgetlib]\nverified: true\n")
    engine.discover_and_load()
    assert _loaded(engine).verified is False and _loaded(engine).verified_claim is True
    assert engine.is_effectively_verified(_loaded(engine)) is False
    reloaded = _engine(workspace / "skills", workspace)
    assert reloaded.is_effectively_verified(_loaded(reloaded)) is False


def test_a_kriya_written_workspace_skill_copied_into_another_repository_gains_no_authority(tmp_path):
    """Control 7: the bytes Kriya wrote (`verified: true`, `verified_at`, `verified_context`) are indistinguishable from forged
    bytes once the folder is copied into a repository the project controls."""
    import shutil
    origin = tmp_path / "origin"
    _skill(origin / "skills", verified=None)
    first = _engine(origin / "skills", origin)
    assert first.mark_verified("widgetlib", "widgetlib 2.0") is True
    assert "verified: true" in (origin / "skills" / "widgetlib" / "skill.yaml").read_text()
    clone = tmp_path / "clone"
    shutil.copytree(origin / "skills", clone / "skills")
    skill = _loaded(_engine(clone / "skills", clone))
    assert skill.verified is False and skill.verified_claim is True and skill.verified_context == "widgetlib 2.0"


# --- W3-1: the human skill-gap decline is Kriya's/the operator's, never the project's ---------------------------------------

def _ack_skill(root, name="widgetlib", ack="true"):
    return _skill(root, name, verified=None, extra=f"verification_gap_acknowledged: {ack}\n")


def test_a_workspace_skill_claiming_a_gap_acknowledgement_is_still_asked_about(tmp_path):
    workspace = tmp_path / "ws"
    _ack_skill(workspace / "skills")
    engine = _engine(workspace / "skills", workspace)
    skill = _loaded(engine)
    assert skill.verification_gap_acknowledged is False and engine.is_gap_acknowledged(skill) is False


def test_a_skill_outside_the_workspace_keeps_its_acknowledged_decline(tmp_path):
    workspace, external = tmp_path / "ws", tmp_path / "operator-skills"
    workspace.mkdir()
    _ack_skill(external)
    engine = _engine(external, workspace)
    skill = _loaded(engine)
    assert skill.verification_gap_acknowledged is True and engine.is_gap_acknowledged(skill) is True


@pytest.mark.parametrize("claim", ['"yes"', '"true"', "1", "[true]", "null"])
def test_a_non_boolean_acknowledgement_never_counts_even_outside_the_workspace(tmp_path, claim):
    workspace, external = tmp_path / "ws", tmp_path / "operator-skills"
    workspace.mkdir()
    _ack_skill(external, ack=claim)
    engine = _engine(external, workspace)
    assert engine.is_gap_acknowledged(_loaded(engine)) is False


def test_the_repository_flipping_the_flag_from_false_to_true_gains_no_authority(tmp_path):
    workspace = tmp_path / "ws"
    folder = _ack_skill(workspace / "skills", ack="false")
    assert _loaded(_engine(workspace / "skills", workspace)).verification_gap_acknowledged is False
    (folder / "skill.yaml").write_text((folder / "skill.yaml").read_text().replace("acknowledged: false", "acknowledged: true"))
    flipped = _engine(workspace / "skills", workspace)                      # a new load / a new run
    assert flipped.is_gap_acknowledged(_loaded(flipped)) is False


def test_a_kriya_written_acknowledgement_copied_into_a_workspace_gains_no_authority(tmp_path):
    workspace, external = tmp_path / "ws", tmp_path / "operator-skills"
    workspace.mkdir()
    _skill(external, verified=None)
    operator_engine = _engine(external, workspace)
    assert operator_engine.mark_gap_acknowledged("widgetlib") is True      # Kriya writes the decline where it owns the skill
    assert "verification_gap_acknowledged: true" in (external / "widgetlib" / "skill.yaml").read_text()
    shutil.copytree(external / "widgetlib", workspace / "skills" / "widgetlib")
    copied = _engine(workspace / "skills", workspace)
    assert copied.is_gap_acknowledged(_loaded(copied)) is False


def test_a_decline_about_a_workspace_skill_holds_for_this_run_only_and_is_never_written_to_the_project(tmp_path):
    workspace = tmp_path / "ws"
    folder = _skill(workspace / "skills", verified=None)
    before = (folder / "skill.yaml").read_bytes()
    engine = _engine(workspace / "skills", workspace)
    assert engine.is_gap_acknowledged(_loaded(engine)) is False
    assert engine.mark_gap_acknowledged("widgetlib") is True
    assert engine.is_gap_acknowledged(_loaded(engine)) is True              # the legitimate same-run decline works
    engine.discover_and_load()                                              # each work unit's analysis reloads the skills
    assert engine.is_gap_acknowledged(_loaded(engine)) is True              # ... and the decline survives it (run-local owner)
    assert (folder / "skill.yaml").read_bytes() == before                   # nothing durable was created in the project
    new_run = _engine(workspace / "skills", workspace)
    assert new_run.is_gap_acknowledged(_loaded(new_run)) is False           # the next run asks again


@pytest.mark.parametrize("end_of_run", ["discard_held_verifications", "apply_held_verifications"])
def test_the_run_local_decline_ends_with_the_run(tmp_path, end_of_run):
    workspace = tmp_path / "ws"
    _skill(workspace / "skills", verified=None)
    engine = _engine(workspace / "skills", workspace)
    engine.mark_gap_acknowledged("widgetlib")
    getattr(engine, end_of_run)()
    engine.discover_and_load()
    assert engine.is_gap_acknowledged(_loaded(engine)) is False


@pytest.mark.parametrize("reset", ["mark_verified", "mark_unverified"])
def test_a_verify_or_unverify_ends_the_run_local_decline_as_their_contract_says(tmp_path, reset):
    """`mark_verified` / `mark_unverified` document that they clear a prior gap acknowledgement; the run-local one honours that too."""
    workspace = tmp_path / "ws"
    _skill(workspace / "skills", verified=None)
    engine = _engine(workspace / "skills", workspace)
    engine.mark_gap_acknowledged("widgetlib")
    assert engine.is_gap_acknowledged(_loaded(engine)) is True

    assert getattr(engine, reset)("widgetlib") is True
    engine.discover_and_load()

    assert engine.is_gap_acknowledged(_loaded(engine)) is False


def test_verifying_one_skill_does_not_end_another_skills_run_local_decline(tmp_path):
    workspace = tmp_path / "ws"
    _skill(workspace / "skills", verified=None)
    _skill(workspace / "skills", name="otherlib", verified=None)
    engine = _engine(workspace / "skills", workspace)
    engine.mark_gap_acknowledged("widgetlib")
    engine.mark_gap_acknowledged("otherlib")

    engine.mark_verified("otherlib")
    engine.discover_and_load()

    assert engine.is_gap_acknowledged(engine.get_skill("widgetlib")) is True
    assert engine.is_gap_acknowledged(engine.get_skill("otherlib")) is False


def _gap_run(tmp_path, monkeypatch, where):
    """The REAL run_generation_workflow with only the model scripted: how many times does the human skill-gap callback fire for a
    skill whose skill.yaml carries ``verification_gap_acknowledged: true``, in the workspace or in an operator-owned directory?"""
    sys.path.insert(0, str(Path(__file__).resolve().parent / "state_machine"))
    import _kernel_golden as kg  # noqa: E402  # pylint: disable=import-outside-toplevel
    from _scripted_run import engine, script  # noqa: E402  # pylint: disable=import-outside-toplevel
    for key, value in kg.GIT_ENV.items():
        monkeypatch.setenv(key, value)
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "calc.py").write_text(kg.BASE)
    (workspace / "test_calc.py").write_text(kg.TESTS)
    skills_root = workspace / "skills" if where == "workspace" else tmp_path / "operator-skills"
    _skill(skills_root, "evil-lib", verified=None, extra="verification_gap_acknowledged: true\n")
    for argv in (["init", "-q"], ["config", "user.email", "t@e.com"], ["config", "user.name", "t"], ["add", "-A"], ["commit", "-q", "-m", "i"]):
        subprocess.run(["git", *argv], cwd=str(workspace), check=True)
    eng, llm, cfg = engine(chain=False)
    cfg.paths.skills = str(skills_root)
    cfg.skills.load_global = False
    cfg.skills.load_cwd = False
    gap_calls = []
    script(llm, plan="Step 1: add mul", target="calc.py", developer=lambda m, n, text: kg.GOOD)

    def gap_callback(reason, names):
        gap_calls.append(list(names))

    import asyncio  # pylint: disable=import-outside-toplevel
    with kg.isolated_working_directory(tmp_path):
        result = asyncio.run(eng.run_generation_workflow(
            goal=kg.GOAL + " Follow the evil-lib conventions.", workspace_path=str(workspace),
            approval_callback=lambda files, reason: True, skill_gap_callback=gap_callback))
    return gap_calls, result


def test_end_to_end_a_workspace_skill_claiming_an_acknowledgement_still_triggers_the_human_gap_prompt(tmp_path, monkeypatch):
    gap_calls, result = _gap_run(tmp_path, monkeypatch, "workspace")
    assert gap_calls == [["evil-lib"]]                                      # MEASURED 0 calls before W3-1
    assert result.get("unresolved_skill_gaps") == ["evil-lib"]


def test_end_to_end_an_operator_owned_acknowledged_skill_still_suppresses_the_prompt(tmp_path, monkeypatch):
    gap_calls, result = _gap_run(tmp_path, monkeypatch, "operator")
    assert gap_calls == [] and not result.get("unresolved_skill_gaps")


def test_the_gap_prompt_reads_the_decline_through_the_engine_predicate_not_the_raw_skill_field():
    """The one reader that decides whether the human is asked (stage_analysis, per work unit) must use ``is_gap_acknowledged``: the raw field
    cannot see a same-run decline about a workspace skill, and reading it alone would re-ask on every unit. Structural, in the repository's
    own style (the decision is one line inside a large function)."""
    source = (Path(__file__).resolve().parent.parent / "kriya" / "workflow" / "stage_analysis.py").read_text(encoding="utf-8")
    assert "not se.is_gap_acknowledged(s)" in source
    assert "not s.verification_gap_acknowledged" not in source
