"""PRD-036 canary authority (scripts/prd036_canary_authority.py): the operator's requirement/acceptance authority for the canary goal is a
release-gate input. It is built through Kriya's OWN loaders (the binders ``kriya generate`` uses), sealed into ONE canonical identity that the frozen
candidate digest includes, and refused on any drift. Synthetic project and goal - no private fixture material.
"""
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))


def _script(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


authority = _script("prd036_canary_authority")

REQ_1 = "Fix a bug in the existing service: Svc.save(Long id) sets the flag in memory but never persists it, so the change is silently lost."
REQ_2 = "Fix only this method so that the flag is persisted, by saving the entity through repo after setting it."
REQ_3 = "Do not modify any other file in the repository."
GOAL = f"{REQ_1}\n\n{REQ_2}\n\n{REQ_3}\n"
EXPECT = "src/main/java/p/Svc.java"
SERVICE = ("package p;\n\npublic class Svc {\n    public void save(Long id) {\n        Object e = null;\n    }\n\n"
           "    public void other() {\n        int x = 1;\n    }\n}\n")
ACCEPTANCE = '''package p;

import static org.junit.jupiter.api.Assertions.assertTrue;

import org.junit.jupiter.api.Test;

class KriyaAcceptanceTest {
    // kriya_requirement: REQ-1
    @Test
    void theFlagIsPersisted() {
        assertTrue(true);
    }

    // kriya_requirement: REQ-1
    @Test
    void theFlagIsPersistedForAnotherEntity() {
        assertTrue(true);
    }
}
'''


ACCEPTANCE_WITH_REQ_2 = ACCEPTANCE.rstrip().rstrip("}") + """
    // kriya_requirement: REQ-2
    @Test
    void theEntityIsSavedThroughTheRepository() {
        assertTrue(true);
    }
}
"""


def _git(root, *args):
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "maintenance.auto=false", "-c", "gc.auto=0", *args],
                   check=True, capture_output=True)


@pytest.fixture
def world(tmp_path):
    """A synthetic Maven project (the workspace), the goal, and a complete, valid authority directory (approval sealed)."""
    workspace = tmp_path / "ws"
    (workspace / "src/main/java/p").mkdir(parents=True)
    (workspace / "pom.xml").write_text("<project/>\n")
    (workspace / EXPECT).write_text(SERVICE)
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    _git(workspace, "add", "-A")
    _git(workspace, "commit", "-q", "-m", "base")
    goal = tmp_path / "goal.md"
    goal.write_text(GOAL)
    directory = tmp_path / "authority"
    (directory / "oracle/base").mkdir(parents=True)
    shutil.copy(SCRIPTS / "prd036_canary_oracle" / "verify.sh", directory / "oracle/verify.sh")
    (directory / "oracle/base/DefaultDriverService.java").write_text(SERVICE)
    (directory / authority.ACCEPTANCE).write_text(ACCEPTANCE)
    (directory / authority.REQUIREMENTS).write_text(json.dumps({"format": "kriya.requirements/1", "requirements": [
        {"id": "REQ-1", "text": REQ_1, "kind": "requirement"}, {"id": "REQ-2", "text": REQ_2, "kind": "constraint"},
        {"id": "REQ-3", "text": REQ_3, "kind": "constraint"}]}))
    entry = lambda rid, text, kind, **extra: {"requirement_id": rid, "original_text": text, "normalized_text": text, "authority": kind,  # noqa: E731
                                              "closure": f"{rid} closure", **extra}
    (directory / authority.MAPPING).write_text(json.dumps({"format": authority.MAPPING_FORMAT, "entries": [
        entry("REQ-1", REQ_1, "acceptance_approval"), entry("REQ-2", REQ_2, "verification_authority"),
        entry("REQ-3", REQ_3, "deterministic", deterministic_closers=["mutation_scope"])]}))
    authority.draft(str(directory), str(goal), str(workspace))
    (directory / "acceptance-approval.PROPOSED.json").rename(directory / authority.APPROVAL)
    return SimpleWorld(directory, goal, workspace)


class SimpleWorld:
    def __init__(self, directory, goal, workspace):
        self.directory, self.goal, self.workspace = directory, goal, workspace

    def identity(self):
        return authority.identity(str(self.directory), str(self.goal), str(self.workspace), EXPECT)

    def refusal(self):
        with pytest.raises(authority.CanaryAuthorityError) as raised:
            self.identity()
        return " | ".join(raised.value.problems)

    def edit(self, name, mutate):
        path = self.directory / name
        document = json.loads(path.read_text())
        mutate(document)
        path.write_text(json.dumps(document))


def test_a_complete_authority_has_one_canonical_identity_that_binds_every_artifact(world):
    first, second = world.identity(), world.identity()
    assert first == second and len(first["digest"]) == 64
    assert {"requirements", "mapping", "acceptance", "approval", "verification_authority", "goal", "baseline_revision", "expect"} <= set(first)
    assert first["approval"]["requirement_ids"] == ["REQ-1"] and first["verification_authority"]["requirement_ids"] == ["REQ-2"]
    assert first["acceptance"]["cases"]["REQ-1"] and not first["acceptance"]["cases"]["REQ-2"]
    assert [e["authority"] for e in first["mapping"]["entries"]] == ["acceptance_approval", "verification_authority", "deterministic"]
    assert authority.compare(first, world.identity()) == []


def test_the_flags_follow_the_mapping(world):
    flags = authority.generate_flags(str(world.directory))
    assert flags[::2] == ["--requirements", "--acceptance", "--acceptance-approval", "--verification-authority"]


# --- the required negative controls ----------------------------------------------------------------------------------

def test_requirements_changed_after_the_freeze_are_refused(world):
    frozen = world.identity()
    world.edit(authority.REQUIREMENTS, lambda d: d["requirements"][0].update(kind="constraint"))  # even metadata: another contract digest
    assert "another requirement set" in world.refusal()     # Kriya's own binder: the approval was made for the frozen set
    world.edit(authority.REQUIREMENTS, lambda d: d["requirements"][0].update(kind="requirement"))
    assert authority.compare(frozen, world.identity()) == []
    world.edit(authority.REQUIREMENTS, lambda d: d["requirements"][1].update(text=REQ_2 + " Also log it."))
    assert "normalized_text differs" in world.refusal()


def test_the_acceptance_source_changed_after_the_freeze_is_refused(world):
    frozen = world.identity()
    (world.directory / authority.ACCEPTANCE).write_text(ACCEPTANCE.replace("assertTrue(true);", "assertTrue(1 == 1);", 1))
    assert "approval" in world.refusal().lower()          # the approval bound the old acceptance digest: Kriya's loader refuses
    # ... and when the owner re-approves the new source, the identity is a different one: the frozen candidate no longer matches
    authority.draft(str(world.directory), str(world.goal), str(world.workspace))
    (world.directory / "acceptance-approval.PROPOSED.json").replace(world.directory / authority.APPROVAL)
    current = world.identity()
    assert current["digest"] != frozen["digest"] and "acceptance" in authority.compare(frozen, current)


@pytest.mark.parametrize("name,mutate", [
    ("goal", lambda d: d["approvals"][0].update(goal_sha256="0" * 64)),
    ("requirement text", lambda d: d["approvals"][0].update(requirement_text_sha256="1" * 64)),
    ("acceptance digest", lambda d: d["approvals"][0].update(acceptance_sha256="2" * 64)),
    ("case list", lambda d: d["approvals"][0].update(expected_cases=["not.a.case"])),
    ("base revision", lambda d: d["approvals"][0].update(base_revision="3" * 40)),
    ("requirement set", lambda d: d["approvals"][0].update(requirement_set_sha256="4" * 64)),
    ("not accepted as sufficient", lambda d: d["approvals"][0].update(accept_suite_as_sufficient=False)),
    ("another requirement", lambda d: d["approvals"][0].update(requirement_id="REQ-2")),
])
def test_a_wrong_approval_is_refused(world, name, mutate):
    world.edit(authority.APPROVAL, mutate)
    assert "approval" in world.refusal().lower(), name


def test_an_approval_that_omits_a_requirement_the_mapping_assigns_to_acceptance_approval_is_refused(world):
    world.edit(authority.MAPPING, lambda d: d["entries"][1].update(authority="acceptance_approval"))
    (world.directory / authority.AUTHORITY).unlink()
    (world.directory / authority.ACCEPTANCE).write_text(ACCEPTANCE_WITH_REQ_2)
    authority.draft(str(world.directory), str(world.goal), str(world.workspace))
    (world.directory / "acceptance-approval.PROPOSED.json").replace(world.directory / authority.APPROVAL)
    assert world.identity()["approval"]["requirement_ids"] == ["REQ-1", "REQ-2"]
    world.edit(authority.APPROVAL, lambda d: d["approvals"].pop())            # the owner approved only one of the two
    assert "approval covers ['REQ-1']" in world.refusal()


@pytest.mark.parametrize("artifact", [authority.REQUIREMENTS, authority.MAPPING, authority.ACCEPTANCE, authority.APPROVAL, authority.AUTHORITY])
def test_a_required_artifact_that_is_missing_is_refused(world, artifact):
    (world.directory / artifact).unlink()
    assert f"required artifact missing: {artifact}" in world.refusal()


def test_an_unneeded_approval_is_not_required_when_the_mapping_assigns_none(world):
    world.edit(authority.MAPPING, lambda d: d["entries"][0].update(authority="deterministic", deterministic_closers=["x"]))
    (world.directory / authority.APPROVAL).unlink()
    assert "acceptance cases cover" in world.refusal()    # the cases still exist but nothing maps REQ-1 to them: a mapping mismatch, not a pass


@pytest.mark.parametrize("name,mutate,expected", [
    ("a requirement unmapped", lambda d: d["entries"].pop(), "requirement ids"),
    ("an unknown requirement mapped", lambda d: d["entries"].append({**d["entries"][0], "requirement_id": "REQ-9"}), "requirement ids"),
    ("a duplicate mapping", lambda d: d["entries"].append(dict(d["entries"][0])), "requirement ids"),
    ("an original text that is not the goal's", lambda d: d["entries"][0].update(original_text="Something the goal never said."), "verbatim"),
    ("a normalized text that differs from the contract", lambda d: d["entries"][0].update(normalized_text="weaker"), "normalized_text differs"),
    ("no closure stated", lambda d: d["entries"][2].update(closure=" "), "states no closure"),
    ("an unknown authority kind", lambda d: d["entries"][2].update(authority="trust_me"), "unknown authority kind"),
    ("acceptance cases assigned to a requirement without cases", lambda d: d["entries"][1].update(authority="acceptance_approval"), "acceptance cases cover"),
    ("the verification authority assigned to the wrong requirement", lambda d: d["entries"][0].update(authority="verification_authority"), "verification authority covers"),
])
def test_a_req_mapping_mismatch_is_refused(world, name, mutate, expected):
    world.edit(authority.MAPPING, mutate)
    assert expected in world.refusal(), name


def test_the_verification_authority_binds_its_assets_and_the_goal(world):
    (world.directory / "oracle/verify.sh").write_text("#!/bin/sh\nexit 0\n")          # an asset changed after the manifest sealed its digest
    assert "verification authority" in world.refusal().lower()


def test_compare_names_every_drift_and_a_tampered_recorded_digest():
    recorded = {"version": 1, "a": 1, "b": {"x": 1}}
    recorded["digest"] = authority._digest(recorded)  # noqa: SLF001
    assert authority.compare(recorded, dict(recorded)) == []
    assert authority.compare(recorded, {**recorded, "b": {"x": 2}, "digest": "other"}) == ["b"]
    assert authority.compare({**recorded, "a": 2}, dict(recorded)) == ["a", "recorded.digest_invalid"]
    assert authority.compare({}, dict(recorded)) == ["the candidate records no canary_authority"]


# --- the candidate seals it ---------------------------------------------------------------------------------------------

def test_the_frozen_candidate_digest_includes_the_canary_authority(world, monkeypatch):
    module = _script("release_candidate")
    from kriya.core.release_candidate import candidate_digest_valid

    monkeypatch.chdir(world.workspace.parent)       # current_identity() changes into the release workspace: pytest restores the directory
    base = {"version": 1, "release": {"kriya": {"revision": "r"}}, "digest": "x"}
    monkeypatch.setattr(module, "load_config", lambda *_a, **_k: object())
    monkeypatch.setattr(module, "release_candidate_identity", lambda *_a, **_k: dict(base))
    monkeypatch.setenv("KRIYA_RELEASE_CONFIG", str(world.directory / authority.MAPPING))
    monkeypatch.setenv("KRIYA_RELEASE_TRUST_FILE", str(world.directory / authority.MAPPING))
    monkeypatch.setenv("KRIYA_RELEASE_WORKSPACE", str(world.workspace))
    monkeypatch.setenv("KRIYA_CANARY_AUTHORITY_DIR", str(world.directory))
    monkeypatch.setenv("KRIYA_CANARY_GOAL", str(world.goal))
    monkeypatch.setenv("KRIYA_CANARY_EXPECT", EXPECT)
    sealed = module.current_identity()
    assert sealed["canary_authority"]["digest"] == world.identity()["digest"] and candidate_digest_valid(sealed)
    world.edit(authority.MAPPING, lambda d: d["entries"][2].update(closure="a different closure mechanism"))
    assert module.current_identity()["digest"] != sealed["digest"]                      # a different candidate
    monkeypatch.delenv("KRIYA_CANARY_AUTHORITY_DIR")
    assert "canary_authority" not in module.current_identity()


def test_the_canary_script_refuses_an_authority_the_candidate_did_not_seal_and_passes_the_flags():
    script = (SCRIPTS / "prd036_canary.sh").read_text()
    assert "KRIYA_CANARY_AUTHORITY_DIR" in script and "prd036_canary_authority.py check" in script
    assert script.index("prd036_canary_authority.py check") < script.index("run_pass 1 --fresh")
    assert '"${AUTH_FLAGS[@]}"' in script and "canary-authority.json" in script and "exit 2" in script
