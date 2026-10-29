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
"""
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
