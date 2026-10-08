"""VERIFICATION-CONTRACT-003 slice 3: the operator's sealed external verification authority (owner decision D2,
containment-first only): manifest binding and refusals, the contained two-phase execution adapter (verdict protocol,
integrity, typed unavailability), closure as operator sufficiency (HUMAN_ACCEPTED, never 'verified'), VIOLATED on
FAIL, nothing on INDETERMINATE, and the negative controls the owner required (oracle changes after seal, authority
bound to another workspace/revision, acceptance command exits non-zero, external oracle unavailable).

The execution adapter is exercised with a scripted validator double: it records exactly which argv, network authority,
acquisition flag and cache the adapter asked the production containment boundary to run, and returns scripted results.
No container is started here; the real-container path is the live cohort's.
"""
import hashlib
import json
import os
import subprocess

import pytest

from kriya.config.config import AutonomyConfig
from kriya.tools.containment import ContainmentSetupError, NetworkAuthority
from kriya.workflow import authority_bundle as ab
from kriya.workflow.contract_compilation import CLOSER_EXTERNAL_ACCEPTANCE, compile_verification_contract
from kriya.workflow.obligations import ObligationLedger
from kriya.workflow.requirements import (
    API_PRESERVATION,
    BEHAVIOR,
    RequirementOutcome,
    derive_requirements,
    record_requirement_verdicts,
    requirement_evidence,
    requirement_outcomes,
    seed_requirement_obligations,
    statement_origins,
)

GOAL = "TTLCache keeps entries alive one tick too long at the expiry boundary.\nDo not change the public API.\n"


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def _workspace(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "cache.py").write_text("def expire():\n    return []\n")
    (root / "pyproject.toml").write_text("[project]\nname='cache'\nversion='0'\n")
    _git(root, "init", "-q")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "base")
    return root, _git(root, "rev-parse", "HEAD")


def _bundle_dir(tmp_path, reqs, base, goal=GOAL, **overrides):
    bundle = tmp_path / "authority"
    (bundle / "oracle").mkdir(parents=True)
    (bundle / "oracle" / "verify.sh").write_text("#!/bin/sh\nexit 0\n")
    (bundle / "hidden").mkdir()
    (bundle / "hidden" / "test_hidden.py").write_text("def test_x():\n    assert True\n")
    manifest = ab.authority_template(
        str(bundle), reqs, goal, base_revision=base, language="python", build_tool="pip",
        verify=["sh", "oracle/verify.sh"], prepare=["sh", "oracle/verify.sh"],
        covers=[("REQ-1", BEHAVIOR, "GENERAL", "the hidden test asserts the expiry boundary")],
        authority_id="t1-oracle", original_oracle={"digest": "orig" * 16, "files": ["accept.sh"]})
    manifest.update(overrides)
    (bundle / "manifest.json").write_text(json.dumps(manifest, indent=1))
    return bundle


def _load(tmp_path, bundle, reqs, base, workspace, language="python", goal=GOAL):
    return ab.load_authority_bundle(str(bundle / "manifest.json"), reqs, goal, state_root=str(tmp_path / "state"),
                                    workspace=str(workspace), base_revision=base, project_language=language)


# ---------------------------------------------------------------- binding
def test_01_a_valid_bundle_binds_and_is_stored_content_addressed_outside_the_workspace(tmp_path):
    ws, base = _workspace(tmp_path)
    reqs = derive_requirements(GOAL)
    bundle = _load(tmp_path, _bundle_dir(tmp_path, reqs, base), reqs, base, ws)
    assert bundle.authority_id == "t1-oracle" and bundle.covers["REQ-1"].accepted_strength == "GENERAL"
    assert os.path.isfile(os.path.join(bundle.stored_dir, "manifest.json"))
    assert os.path.isfile(os.path.join(bundle.stored_dir, "assets", "hidden", "test_hidden.py"))
    assert bundle.stored_dir.startswith(str(tmp_path / "state")) and bundle.digest == hashlib.sha256(
        (tmp_path / "authority" / "manifest.json").read_bytes()).hexdigest()
    authority = bundle.authority()
    assert authority.kind == "external_acceptance_command" and authority.visibility == "hidden"
    assert authority.covers("REQ-1", BEHAVIOR, "GENERAL") and not authority.covers("REQ-2", API_PRESERVATION, None)
    assert authority.provenance["original_oracle"]["digest"] == "orig" * 16
    # the contract binds the covered requirement to the external closer; the API constraint has its own predicate
    contract = compile_verification_contract(reqs, origins=statement_origins(GOAL), test_files=[],
                                             external_authorities=[authority], project_language="python")
    assert contract.entry("REQ-1").closers == [CLOSER_EXTERNAL_ACCEPTANCE] and contract.refusal() is None
    assert contract.digest != compile_verification_contract(reqs, origins=statement_origins(GOAL), test_files=[],
                                                            project_language="python").digest


@pytest.mark.parametrize("mutate, code", [
    ("inside_workspace", ab.AUTHORITY_INVALID),
    ({"goal_sha256": "0" * 64}, ab.AUTHORITY_GOAL_MISMATCH),
    ({"requirement_set_sha256": "0" * 64}, ab.AUTHORITY_REQUIREMENT_SET_MISMATCH),
    ({"base_revision": "0" * 40}, ab.AUTHORITY_BASE_MISMATCH),
    ("other_language", ab.AUTHORITY_TOOLCHAIN_MISMATCH),
    ("tampered_asset", ab.AUTHORITY_INVALID),
    ({"timeout_seconds": 0}, ab.AUTHORITY_INVALID),
    ({"timeout_seconds": 99999}, ab.AUTHORITY_INVALID),
    ({"verify": ["/bin/sh", "oracle/verify.sh"]}, ab.AUTHORITY_INVALID),
    ({"verify": ["sh", "../escape.sh"]}, ab.AUTHORITY_INVALID),
    ({"visibility": "developer"}, ab.AUTHORITY_INVALID),
    ("cover_wrong_text", ab.AUTHORITY_INVALID),
    ("cover_unknown_requirement", ab.AUTHORITY_INVALID),
    ("cover_not_sufficient", ab.AUTHORITY_INVALID),
    ("cover_bad_strength", ab.AUTHORITY_INVALID),
    ("extra_field", ab.AUTHORITY_INVALID),
])
def test_02_every_binding_mismatch_is_a_typed_refusal(tmp_path, mutate, code):
    ws, base = _workspace(tmp_path)
    reqs = derive_requirements(GOAL)
    overrides = mutate if isinstance(mutate, dict) else {}
    bundle_dir = _bundle_dir(tmp_path, reqs, base, **overrides)
    manifest = json.loads((bundle_dir / "manifest.json").read_text())
    language = "python"
    workspace = ws
    if mutate == "inside_workspace":
        inside = ws / "authority"
        inside.mkdir()
        (inside / "manifest.json").write_text(json.dumps(manifest))
        bundle_dir = inside
    elif mutate == "other_language":
        language = "java"
    elif mutate == "tampered_asset":
        (bundle_dir / "oracle" / "verify.sh").write_text("#!/bin/sh\nexit 1\n")
    elif mutate == "cover_wrong_text":
        manifest["covers"][0]["requirement_text_sha256"] = "1" * 64
    elif mutate == "cover_unknown_requirement":
        manifest["covers"][0]["requirement_id"] = "REQ-9"
    elif mutate == "cover_not_sufficient":
        manifest["covers"][0]["accept_as_sufficient"] = False
    elif mutate == "cover_bad_strength":
        manifest["covers"][0]["accepted_strength"] = "ALL"
    elif mutate == "extra_field":
        manifest["extra"] = 1
    if mutate != "inside_workspace":
        (bundle_dir / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ab.AuthorityBundleError) as refused:
        _load(tmp_path, bundle_dir, reqs, base, workspace, language=language)
    assert refused.value.reason_code == code


def test_03_a_bundle_bound_to_another_workspace_revision_is_refused(tmp_path):
    """Owner negative test: operator authority bound to another workspace/revision -> refused."""
    ws, base = _workspace(tmp_path)
    reqs = derive_requirements(GOAL)
    bundle_dir = _bundle_dir(tmp_path, reqs, base)
    (ws / "cache.py").write_text("def expire():\n    return [1]\n")
    _git(ws, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-am", "moved")
    moved = _git(ws, "rev-parse", "HEAD")
    with pytest.raises(ab.AuthorityBundleError) as refused:
        _load(tmp_path, bundle_dir, reqs, moved, ws)
    assert refused.value.reason_code == ab.AUTHORITY_BASE_MISMATCH


# ---------------------------------------------------------------- execution adapter (scripted containment boundary)
class _Validator:
    """Records what the adapter asks the production boundary to run; answers from a script."""

    def __init__(self, root, script, contained=True, raise_setup=False):
        self.workspace_path = root
        self.autonomy_cfg = AutonomyConfig(contained_execution_required=contained)
        self.calls = []
        self.script = list(script)
        self.raise_setup = raise_setup
        self.tree_binding = None

    def _dependency_cache_dir(self, tool):
        return f"/cache/{tool}"

    def _run_cmd_with_timeout(self, cmd, cwd, timeout=300, stdin_payload=None, network=NetworkAuthority.DENIED,
                              dependency_cache_path=None, dependency_cache_writable=False, acquisition=False,
                              workspace_path=None):
        if self.raise_setup:
            raise ContainmentSetupError("no backend")
        self.calls.append({"cmd": list(cmd), "cwd": cwd, "timeout": timeout, "network": network,
                           "cache": dependency_cache_path, "writable": dependency_cache_writable,
                           "acquisition": acquisition, "workspace": workspace_path})
        action = self.script.pop(0)
        if callable(action):
            return action(cwd)
        return action


def _ok(cwd):
    return {"returncode": 0, "stdout": "ok", "stderr": "", "timeout": False}


def _fail(cwd):
    return {"returncode": 1, "stdout": "ACCEPT_EXIT=1", "stderr": "", "timeout": False}


def _loaded(tmp_path, **overrides):
    ws, base = _workspace(tmp_path)
    reqs = derive_requirements(GOAL)
    bundle = _load(tmp_path, _bundle_dir(tmp_path, reqs, base, **overrides), reqs, base, ws)
    return ws, reqs, bundle


def test_04_two_phase_execution_goes_through_the_containment_boundary_with_the_right_authority(tmp_path):
    ws, reqs, bundle = _loaded(tmp_path)
    recorder = {}

    def factory(root):
        recorder["validator"] = _Validator(root, [_ok, _ok])
        return recorder["validator"]

    run = ab.run_authority_bundle(bundle, str(ws), state_root=str(tmp_path / "state"), validator_factory=factory)
    assert run.verdict == ab.VERDICT_PASS and run.reason_code == ab.AUTHORITY_PASSED
    prepare, verify = recorder["validator"].calls
    assert prepare["network"] is NetworkAuthority.DEPENDENCY_REGISTRY_ONLY and prepare["acquisition"] is True
    assert verify["network"] is NetworkAuthority.DENIED and verify["acquisition"] is False
    assert verify["cmd"] == ["sh", os.path.join(".kriya", "authority", "oracle", "verify.sh")]
    assert prepare["cache"] is None and verify["cache"] is None  # pip: no managed cache mount
    assert verify["timeout"] == 900 and verify["workspace"] == verify["cwd"]
    # the run happened on a Kriya-owned copy under the state root, never in the workspace, and was cleaned up
    assert verify["cwd"].startswith(str(tmp_path / "state")) and not os.path.exists(verify["cwd"])
    assert not (ws / ".kriya").exists()
    assert run.evidence()["verify"]["returncode"] == 0


def test_05_verdict_protocol_and_integrity(tmp_path):
    ws, reqs, bundle = _loaded(tmp_path)
    state = str(tmp_path / "state")
    # FAIL: exit 1
    run = ab.run_authority_bundle(bundle, str(ws), state_root=state, validator_factory=lambda r: _Validator(r, [_ok, _fail]))
    assert run.verdict == ab.VERDICT_FAIL and run.reason_code == ab.AUTHORITY_FAILED
    # any other exit: INDETERMINATE, never PASS
    run = ab.run_authority_bundle(bundle, str(ws), state_root=state, validator_factory=lambda r: _Validator(
        r, [_ok, lambda c: {"returncode": 2, "stdout": "INSTALL_FAILED", "stderr": "", "timeout": False}]))
    assert run.verdict == ab.VERDICT_INDETERMINATE and run.reason_code == ab.AUTHORITY_INDETERMINATE
    # timeout
    run = ab.run_authority_bundle(bundle, str(ws), state_root=state, validator_factory=lambda r: _Validator(
        r, [_ok, lambda c: {"returncode": 0, "stdout": "", "stderr": "", "timeout": True}]))
    assert run.verdict == ab.VERDICT_INDETERMINATE and run.reason_code == ab.AUTHORITY_TIMEOUT
    # a failed prepare is an environment outcome, verify never runs
    recorder = {}

    def factory(root):
        recorder["v"] = _Validator(root, [_fail, _ok])
        return recorder["v"]
    run = ab.run_authority_bundle(bundle, str(ws), state_root=state, validator_factory=factory)
    assert run.reason_code == ab.AUTHORITY_PREPARE_FAILED and len(recorder["v"].calls) == 1

    # verdict.json must agree with the exit code
    def writes_verdict(verdict):
        def action(cwd):
            with open(os.path.join(cwd, ".kriya", "authority", ab.VERDICT_FILE), "w") as handle:
                json.dump({"verdict": verdict}, handle)
            return _ok(cwd)
        return action
    run = ab.run_authority_bundle(bundle, str(ws), state_root=state, validator_factory=lambda r: _Validator(r, [_ok, writes_verdict("PASS")]))
    assert run.verdict == ab.VERDICT_PASS and run.verdict_file == {"verdict": "PASS"}
    run = ab.run_authority_bundle(bundle, str(ws), state_root=state, validator_factory=lambda r: _Validator(r, [_ok, writes_verdict("FAIL")]))
    assert run.verdict == ab.VERDICT_INDETERMINATE and run.reason_code == ab.AUTHORITY_VERDICT_INCONSISTENT

    # an asset changed during the run (the oracle changed after seal): INDETERMINATE, never PASS
    def tampers(cwd):
        target = os.path.join(cwd, ".kriya", "authority", "oracle", "verify.sh")
        os.chmod(target, 0o644)
        with open(target, "w") as handle:
            handle.write("#!/bin/sh\nexit 0\n# changed\n")
        return _ok(cwd)
    run = ab.run_authority_bundle(bundle, str(ws), state_root=state, validator_factory=lambda r: _Validator(r, [_ok, tampers]))
    assert run.verdict == ab.VERDICT_INDETERMINATE and run.reason_code == ab.AUTHORITY_ASSETS_CHANGED

    # the candidate changed while the authority ran
    def mutates(cwd):
        with open(os.path.join(cwd, "cache.py"), "a") as handle:
            handle.write("# mutated\n")
        return _ok(cwd)
    run = ab.run_authority_bundle(bundle, str(ws), state_root=state, validator_factory=lambda r: _Validator(r, [_ok, mutates]))
    assert run.reason_code == ab.AUTHORITY_CANDIDATE_CHANGED_DURING_RUN and run.verdict == ab.VERDICT_INDETERMINATE


def test_06_never_on_the_host_and_typed_when_containment_is_unavailable(tmp_path):
    ws, reqs, bundle = _loaded(tmp_path)
    state = str(tmp_path / "state")
    recorder = {}

    def host(root):
        recorder["v"] = _Validator(root, [_ok, _ok], contained=False)
        return recorder["v"]
    run = ab.run_authority_bundle(bundle, str(ws), state_root=state, validator_factory=host)
    assert run.reason_code == ab.AUTHORITY_EXECUTION_UNAVAILABLE and run.verdict == ab.VERDICT_INDETERMINATE
    assert recorder["v"].calls == []  # nothing ran
    run = ab.run_authority_bundle(bundle, str(ws), state_root=state,
                                  validator_factory=lambda r: _Validator(r, [_ok, _ok], raise_setup=True))
    assert run.reason_code == ab.AUTHORITY_EXECUTION_UNAVAILABLE

    def refuses(root):
        raise ContainmentSetupError("toolchain mismatch")
    assert ab.run_authority_bundle(bundle, str(ws), state_root=state, validator_factory=refuses).reason_code == ab.AUTHORITY_EXECUTION_UNAVAILABLE


def test_07_maven_and_gradle_bundles_mount_the_managed_cache(tmp_path):
    ws, base = _workspace(tmp_path)
    (ws / "pom.xml").write_text("<project/>")
    _git(ws, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    _git(ws, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "pom")
    base = _git(ws, "rev-parse", "HEAD")
    reqs = derive_requirements(GOAL)
    bundle_dir = _bundle_dir(tmp_path, reqs, base, toolchain={"language": "java", "build_tool": "maven"})
    bundle = _load(tmp_path, bundle_dir, reqs, base, ws, language="java")
    recorder = {}

    def factory(root):
        recorder["v"] = _Validator(root, [_ok, _ok])
        return recorder["v"]
    assert ab.run_authority_bundle(bundle, str(ws), state_root=str(tmp_path / "state"), validator_factory=factory).verdict == "PASS"
    prepare, verify = recorder["v"].calls
    assert prepare["cache"] == "/cache/maven" and prepare["writable"] is True
    assert verify["cache"] == "/cache/maven" and verify["writable"] is False


# ---------------------------------------------------------------- closure: operator sufficiency, never a proof
def _ledger(reqs, candidate="cand"):
    ledger = ObligationLedger()
    seed_requirement_obligations(ledger, reqs)
    record_requirement_verdicts(ledger, reqs, {r.id: (RequirementOutcome.UNVERIFIED, "x") for r in reqs.requirements},
                                revision=1, evidence_fingerprint=candidate, source="test")
    return ledger


def _contract(reqs, bundle):
    return compile_verification_contract(reqs, origins=statement_origins(GOAL), test_files=[],
                                         external_authorities=[bundle.authority()], project_language="python")


@pytest.mark.parametrize("verdict, outcome", [(ab.VERDICT_PASS, RequirementOutcome.HUMAN_ACCEPTED),
                                              (ab.VERDICT_FAIL, RequirementOutcome.VIOLATED),
                                              (ab.VERDICT_INDETERMINATE, RequirementOutcome.UNVERIFIED)])
def test_08_closure_outcomes(tmp_path, verdict, outcome):
    ws, reqs, bundle = _loaded(tmp_path)
    contract = _contract(reqs, bundle)
    ledger = _ledger(reqs)
    run = ab.AuthorityRun(verdict=verdict, reason_code="x", reason="scripted")
    [entry] = ab.close_requirements_with_authority_bundle(ledger, reqs, bundle, contract, execute=lambda: run,
                                                          source="t", revision=1)
    assert entry["requirement"] == "REQ-1" and entry["authority_digest"] == bundle.digest
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is outcome
    assert entry["closed"] is (outcome is RequirementOutcome.HUMAN_ACCEPTED)
    if outcome is RequirementOutcome.HUMAN_ACCEPTED:
        evidence = requirement_evidence(ledger, reqs)["REQ-1"]
        assert evidence["claims"][BEHAVIOR]["method"] == "external_acceptance_command"
        assert evidence["claims"][BEHAVIOR]["approval"]["requirement_id"] == "REQ-1"
    # REQ-2 (the API constraint) is never touched by the bundle
    assert requirement_outcomes(ledger, reqs)["REQ-2"] is RequirementOutcome.UNVERIFIED


def test_09_the_run_executes_once_and_only_when_a_bound_requirement_is_open(tmp_path):
    ws, reqs, bundle = _loaded(tmp_path)
    contract = _contract(reqs, bundle)
    ledger = _ledger(reqs)
    calls = []

    def execute():
        calls.append(1)
        return ab.AuthorityRun(verdict=ab.VERDICT_PASS, reason_code="x")
    ab.close_requirements_with_authority_bundle(ledger, reqs, bundle, contract, execute=execute, source="t", revision=1)
    ab.close_requirements_with_authority_bundle(ledger, reqs, bundle, contract, execute=execute, source="t", revision=1)
    assert calls == [1]  # closed: no second run
    # a contract that binds another bundle digest never consumes this one's run
    other = compile_verification_contract(reqs, origins=statement_origins(GOAL), test_files=[], project_language="python")
    assert ab.close_requirements_with_authority_bundle(_ledger(reqs), reqs, bundle, other, execute=execute, source="t", revision=1) == []
    assert calls == [1]


def test_10_a_pass_recorded_for_other_words_never_closes(tmp_path):
    """Read-time binding (like B3): a record whose coverage text differs from the requirement's words is ignored."""
    ws, reqs, bundle = _loaded(tmp_path)
    contract = _contract(reqs, bundle)
    ledger = _ledger(reqs)
    ab.close_requirements_with_authority_bundle(ledger, reqs, bundle, contract,
                                                execute=lambda: ab.AuthorityRun(verdict=ab.VERDICT_PASS, reason_code="x"),
                                                source="t", revision=1)
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.HUMAN_ACCEPTED
    altered = derive_requirements(GOAL.replace("one tick", "two ticks"))
    assert requirement_outcomes(ledger, altered)["REQ-1"] is RequirementOutcome.UNVERIFIED
