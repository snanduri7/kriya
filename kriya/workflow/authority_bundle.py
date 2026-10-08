"""VERIFICATION-CONTRACT-003: the operator's sealed external verification
authority (closer ``external_acceptance_command``; owner decision D2,
containment-first only).

An operator who holds an oracle Kriya cannot express as a B2 acceptance file
(a fresh-install test, a hidden upstream suite, a signature baseline, a
tamper check) registers it as a bundle: a JSON manifest OUTSIDE the
workspace (SEC-009's rule for operator authority), the oracle's assets by
digest, a ``prepare`` command (optional acquisition phase) and a ``verify``
command, a bounded timeout, the toolchain it needs, and - per requirement -
what it covers and with what strength. Loaded, validated and stored
content-addressed under ``<state>/verification-authority/<digest>/`` before
any model call, bound to the goal, the requirement set (ids and exact text),
the base revision and the project's language; bound to the engine by the
CLI only (``--verification-authority``). Its digest joins the verification
contract, so a changed manifest or asset changes the contract and never
reuses a candidate.

Execution never happens on the host and never inside the workspace: the
candidate is copied to a Kriya-owned scratch under the state root, the
assets are staged read-only beside it (digests checked before and after),
and both phases run through ``PolymorphicValidator._run_cmd_with_timeout``
- the one production containment boundary (OCI, the candidate's toolchain
identity, SEC-007 resource authority): ``prepare`` with
``DEPENDENCY_REGISTRY_ONLY`` (the SEC-006 scoped registry proxy, a managed
dependency cache mounted writable), ``verify`` with ``NetworkAuthority.DENIED``.
Without ``contained_execution_required`` the bundle cannot run
(AUTHORITY_EXECUTION_UNAVAILABLE); a containment or toolchain refusal is the
same typed outcome. Nothing broadens network or host authority.

Verdict protocol: ``verify`` exit 0 is PASS, exit 1 is FAIL (the oracle
contradicted the candidate), any other exit, a timeout, a failed ``prepare``,
a changed asset or a candidate changed during the run is INDETERMINATE. An
optional ``verdict.json`` the command writes beside the staged assets must
agree with the exit code, else INDETERMINATE (AUTHORITY_VERDICT_INCONSISTENT).

Closure: PASS records the covered claim SATISFIED with method
``external_acceptance_command`` - operator sufficiency (HUMAN_ACCEPTED class,
never "verified"), bound to the requirement's exact text like a B3
approval; FAIL is deterministic counter-evidence (VIOLATED); INDETERMINATE
closes nothing. The bundle's bytes are never in a prompt; its visibility is
``hidden`` and recorded on the contract.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from kriya.tools.containment import ContainmentSetupError, NetworkAuthority
from kriya.workflow.contract_compilation import (
    AUTHORITY_EXTERNAL_COMMAND,
    VISIBILITY_HIDDEN,
    ExternalAuthority,
    VerificationContract,
)
from kriya.workflow.obligations import ObligationLedger, ObligationStatus
from kriya.workflow.requirements import (
    API_PRESERVATION,
    BEHAVIOR,
    BEHAVIOR_EXACT,
    BEHAVIOR_GENERAL,
    DOCUMENTATION_CLAIM,
    EXTERNAL_ACCEPTANCE_METHOD,
    RequirementOutcome,
    RequirementSet,
    goal_identity,
    record_requirement_claim,
    requirement_obligation_id,
    requirement_outcomes,
)

AUTHORITY_FORMAT = "kriya.verification_authority/1"
AUTHORITY_TYPE = "external_acceptance_command"
AUTHORITY_STORE_DIR = "verification-authority"
AUTHORITY_RUNS_DIR = "authority-runs"
STAGE_RELATIVE = os.path.join(".kriya", "authority")
VERDICT_FILE = "verdict.json"
MAX_TIMEOUT_SECONDS = 3600
_MANIFEST_FIELDS = frozenset({"format", "authority_id", "type", "goal_sha256", "requirement_set_sha256", "base_revision",
                              "assets", "prepare", "verify", "timeout_seconds", "toolchain", "verdict_protocol",
                              "visibility", "covers"})
_OPTIONAL_FIELDS = frozenset({"original_oracle", "notes"})
_COVER_FIELDS = frozenset({"requirement_id", "requirement_text_sha256", "claim", "accepted_strength",
                           "accept_as_sufficient", "why"})
_COVERABLE_CLAIMS = frozenset({BEHAVIOR, API_PRESERVATION, DOCUMENTATION_CLAIM})
_LANGUAGES = frozenset({"python", "java"})
_BUILD_TOOLS = frozenset({"maven", "gradle", "pip", "none"})
_COPY_IGNORE = (".git", ".kriya", "target", "build", ".gradle", "node_modules", "__pycache__", ".pytest_cache")

AUTHORITY_INVALID = "AUTHORITY_INVALID"
AUTHORITY_GOAL_MISMATCH = "AUTHORITY_GOAL_MISMATCH"
AUTHORITY_REQUIREMENT_SET_MISMATCH = "AUTHORITY_REQUIREMENT_SET_MISMATCH"
AUTHORITY_BASE_MISMATCH = "AUTHORITY_BASE_MISMATCH"
AUTHORITY_TOOLCHAIN_MISMATCH = "AUTHORITY_TOOLCHAIN_MISMATCH"
AUTHORITY_EXECUTION_UNAVAILABLE = "AUTHORITY_EXECUTION_UNAVAILABLE"
AUTHORITY_PREPARE_FAILED = "AUTHORITY_PREPARE_FAILED"
AUTHORITY_TIMEOUT = "AUTHORITY_TIMEOUT"
AUTHORITY_ASSETS_CHANGED = "AUTHORITY_ASSETS_CHANGED"
AUTHORITY_CANDIDATE_CHANGED_DURING_RUN = "AUTHORITY_CANDIDATE_CHANGED_DURING_RUN"
AUTHORITY_VERDICT_INCONSISTENT = "AUTHORITY_VERDICT_INCONSISTENT"
AUTHORITY_PASSED = "AUTHORITY_PASSED"
AUTHORITY_FAILED = "AUTHORITY_FAILED"
AUTHORITY_INDETERMINATE = "AUTHORITY_INDETERMINATE"

VERDICT_PASS = "PASS"
VERDICT_FAIL = "FAIL"
VERDICT_INDETERMINATE = "INDETERMINATE"


class AuthorityBundleError(Exception):
    def __init__(self, reason_code: str, message: str) -> None:
        self.reason_code = reason_code
        super().__init__(f"{reason_code}: {message}")


@dataclass(frozen=True)
class CoverageEntry:
    requirement_id: str
    requirement_text_sha256: str
    claim: str
    accepted_strength: Optional[str]
    why: str

    def to_dict(self) -> Dict[str, Any]:
        return {"requirement_id": self.requirement_id, "requirement_text_sha256": self.requirement_text_sha256,
                "claim": self.claim, "accepted_strength": self.accepted_strength, "why": self.why,
                "accept_as_sufficient": True}


@dataclass(frozen=True)
class AuthorityBundle:
    digest: str
    stored_dir: str
    source_path: str
    authority_id: str
    goal_sha256: str
    requirement_set_sha256: str
    base_revision: str
    assets: Mapping[str, str]  # relative path -> sha256
    prepare: Optional[Tuple[str, ...]]
    verify: Tuple[str, ...]
    timeout_seconds: int
    language: str
    build_tool: str
    visibility: str
    covers: Mapping[str, CoverageEntry]
    original_oracle: Mapping[str, Any] = field(default_factory=dict)

    def authority(self) -> ExternalAuthority:
        coverage = {rid: {"claim": c.claim, "accepted_strength": c.accepted_strength, "why": c.why}
                    for rid, c in self.covers.items()}
        return ExternalAuthority(AUTHORITY_EXTERNAL_COMMAND, self.digest, coverage, visibility=self.visibility,
                                 provenance={"authority_id": self.authority_id, "format": AUTHORITY_FORMAT,
                                             "assets": dict(self.assets), "stored_dir": self.stored_dir,
                                             "toolchain": {"language": self.language, "build_tool": self.build_tool},
                                             "prepare": list(self.prepare or ()), "verify": list(self.verify),
                                             "timeout_seconds": self.timeout_seconds,
                                             "original_oracle": dict(self.original_oracle)})

    def to_dict(self) -> Dict[str, Any]:
        return {**self.authority().to_dict(), "base_revision": self.base_revision,
                "requirement_set_sha256": self.requirement_set_sha256, "goal_sha256": self.goal_sha256}


def _refuse(code: str, message: str) -> AuthorityBundleError:
    return AuthorityBundleError(code, message)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _text_sha256(text: str) -> str:
    return _sha256((text or "").encode("utf-8"))


# The project's own build launchers: a ``prepare`` starting with one of them
# runs through the validator's two-phase Maven/Gradle path (offline first,
# one registry-scoped acquisition with the JVM proxy plumbing and the
# identity-verified Gradle distribution seed, offline again) - the supported
# containment mechanism, never a raw launcher with its own network.
_PROJECT_LAUNCHERS = frozenset({"./gradlew", "gradlew", "mvn", "./mvnw", "mvnw"})


def _argv(value: Any, name: str, assets: Mapping[str, str]) -> Tuple[str, ...]:
    if not isinstance(value, list) or not value or not all(isinstance(t, str) and t for t in value):
        raise _refuse(AUTHORITY_INVALID, f"{name} must be a non-empty list of strings (argv)")
    for token in value:
        if token.startswith("/") or token.startswith("~") or ".." in token.split("/"):
            raise _refuse(AUTHORITY_INVALID, f"{name}: {token!r} is an absolute, home or parent path; only program "
                                             "names and bundle-relative asset paths are allowed")
    if "/" in value[0] and value[0] not in assets and value[0] not in _PROJECT_LAUNCHERS:
        raise _refuse(AUTHORITY_INVALID, f"{name}: {value[0]!r} is not a declared asset or project launcher")
    return tuple(value)


def _run_prepare(validator: Any, bundle: AuthorityBundle, export: str, cache: Optional[str]) -> Dict[str, Any]:
    """The acquisition phase: a Gradle or Maven launcher goes through the
    validator's own two-phase runner (the gates' mechanism); anything else
    is one registry-scoped contained command."""
    argv = _rewrite_argv(bundle.prepare or (), bundle.assets)
    if bundle.build_tool == "gradle" and argv and argv[0] in ("./gradlew", "gradlew"):
        return validator._run_gradle_cmd(argv[0], argv[1:], cwd=export, timeout=bundle.timeout_seconds)  # pylint: disable=protected-access
    if bundle.build_tool == "maven" and argv and argv[0] in ("mvn", "./mvnw", "mvnw"):
        return validator._run_maven_cmd(argv[1:], cwd=export, timeout=bundle.timeout_seconds)  # pylint: disable=protected-access
    return validator._run_cmd_with_timeout(  # pylint: disable=protected-access
        argv, cwd=export, timeout=bundle.timeout_seconds, network=NetworkAuthority.DEPENDENCY_REGISTRY_ONLY,
        acquisition=True, dependency_cache_path=cache, dependency_cache_writable=cache is not None, workspace_path=export,
    )


def load_authority_bundle(
    path: str, requirement_set: RequirementSet, goal: str, *, state_root: str, workspace: str,
    base_revision: Optional[str], project_language: Optional[str],
) -> AuthorityBundle:
    """Read, validate, store and bind the operator's bundle before any model
    call. Raises AuthorityBundleError (typed) for anything that does not bind
    exactly: the goal, the requirement set and each covered requirement's
    text, the base revision, the project language, every asset digest."""
    from kriya.platform.filesystem_semantics import PathRelation, path_relation

    real = os.path.realpath(path)
    if path_relation(os.path.realpath(workspace), real) is not PathRelation.OUTSIDE:
        raise _refuse(AUTHORITY_INVALID, "the verification authority must live outside the workspace (a repository "
                                         "can never ship its own oracle)")
    try:
        with open(real, "rb") as handle:
            data = handle.read()
        manifest = json.loads(data.decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise _refuse(AUTHORITY_INVALID, f"unreadable manifest: {error}") from error
    if not isinstance(manifest, dict):
        raise _refuse(AUTHORITY_INVALID, "the manifest must be a JSON object")
    fields = set(manifest)
    if not _MANIFEST_FIELDS <= fields or fields - _MANIFEST_FIELDS - _OPTIONAL_FIELDS:
        raise _refuse(AUTHORITY_INVALID, f"the manifest must have exactly the fields {sorted(_MANIFEST_FIELDS)} "
                                         f"(optional: {sorted(_OPTIONAL_FIELDS)}); got {sorted(fields)}")
    if manifest["format"] != AUTHORITY_FORMAT or manifest["type"] != AUTHORITY_TYPE:
        raise _refuse(AUTHORITY_INVALID, f"format must be {AUTHORITY_FORMAT!r} and type {AUTHORITY_TYPE!r}")
    if not isinstance(manifest["authority_id"], str) or not manifest["authority_id"].strip():
        raise _refuse(AUTHORITY_INVALID, "authority_id must be a non-empty string")
    if manifest["goal_sha256"] != goal_identity(goal) or manifest["goal_sha256"] != requirement_set.goal_digest:
        raise _refuse(AUTHORITY_GOAL_MISMATCH, "the authority was made for another goal")
    if manifest["requirement_set_sha256"] != requirement_set.digest:
        raise _refuse(AUTHORITY_REQUIREMENT_SET_MISMATCH, "the authority was made for another requirement set")
    if not base_revision or manifest["base_revision"] != base_revision:
        raise _refuse(AUTHORITY_BASE_MISMATCH, f"the authority binds base revision {manifest['base_revision']!r}, the "
                                               f"workspace is at {base_revision!r}")
    toolchain = manifest["toolchain"]
    if (not isinstance(toolchain, dict) or set(toolchain) != {"language", "build_tool"}
            or toolchain["language"] not in _LANGUAGES or toolchain["build_tool"] not in _BUILD_TOOLS):
        raise _refuse(AUTHORITY_INVALID, "toolchain must be {language: python|java, build_tool: maven|gradle|pip|none}")
    if toolchain["language"] != project_language:
        raise _refuse(AUTHORITY_TOOLCHAIN_MISMATCH, f"the authority needs a {toolchain['language']} toolchain, the "
                                                    f"project is {project_language or 'of unknown language'}")
    if manifest["verdict_protocol"] != "exit_code" or manifest["visibility"] != VISIBILITY_HIDDEN:
        raise _refuse(AUTHORITY_INVALID, "verdict_protocol must be 'exit_code' and visibility 'hidden'")
    timeout = manifest["timeout_seconds"]
    if not isinstance(timeout, int) or isinstance(timeout, bool) or not 0 < timeout <= MAX_TIMEOUT_SECONDS:
        raise _refuse(AUTHORITY_INVALID, f"timeout_seconds must be an integer in (0, {MAX_TIMEOUT_SECONDS}]")
    assets = manifest["assets"]
    if not isinstance(assets, dict) or not assets:
        raise _refuse(AUTHORITY_INVALID, "assets must map at least one bundle-relative path to its sha256")
    bundle_dir = os.path.dirname(real)
    asset_bytes: Dict[str, bytes] = {}
    for rel, digest in assets.items():
        if (not isinstance(rel, str) or rel.startswith("/") or ".." in rel.split("/") or not rel
                or not isinstance(digest, str) or len(digest) != 64):
            raise _refuse(AUTHORITY_INVALID, f"asset {rel!r}: a bundle-relative path and a sha256 are required")
        full = os.path.realpath(os.path.join(bundle_dir, rel))
        if path_relation(bundle_dir, full) is not PathRelation.WITHIN:
            raise _refuse(AUTHORITY_INVALID, f"asset {rel!r} escapes the bundle directory")
        try:
            with open(full, "rb") as handle:
                content = handle.read()
        except OSError as error:
            raise _refuse(AUTHORITY_INVALID, f"asset {rel!r} unreadable: {error}") from error
        if _sha256(content) != digest:
            raise _refuse(AUTHORITY_INVALID, f"asset {rel!r} does not match its declared sha256")
        asset_bytes[rel] = content
    prepare = _argv(manifest["prepare"], "prepare", assets) if manifest["prepare"] is not None else None
    verify = _argv(manifest["verify"], "verify", assets)
    covers_raw = manifest["covers"]
    if not isinstance(covers_raw, list) or not covers_raw:
        raise _refuse(AUTHORITY_INVALID, "covers must list at least one requirement")
    covers: Dict[str, CoverageEntry] = {}
    for raw in covers_raw:
        if not isinstance(raw, dict) or set(raw) != _COVER_FIELDS:
            raise _refuse(AUTHORITY_INVALID, f"every coverage entry has exactly the fields {sorted(_COVER_FIELDS)}")
        rid = raw["requirement_id"]
        requirement = requirement_set.get(rid) if isinstance(rid, str) else None
        if requirement is None:
            raise _refuse(AUTHORITY_INVALID, f"coverage names a requirement outside the set: {rid!r}")
        if rid in covers:
            raise _refuse(AUTHORITY_INVALID, f"duplicate coverage for {rid}")
        if raw["requirement_text_sha256"] != _text_sha256(requirement.text):
            raise _refuse(AUTHORITY_INVALID, f"{rid}: requirement_text_sha256 does not match the requirement's exact text")
        if raw["claim"] not in _COVERABLE_CLAIMS:
            raise _refuse(AUTHORITY_INVALID, f"{rid}: claim must be one of {sorted(_COVERABLE_CLAIMS)}")
        strength = raw["accepted_strength"]
        if raw["claim"] == BEHAVIOR and strength not in (BEHAVIOR_EXACT, BEHAVIOR_GENERAL):
            raise _refuse(AUTHORITY_INVALID, f"{rid}: accepted_strength must be EXACT or GENERAL for a behaviour claim")
        if raw["claim"] != BEHAVIOR and strength is not None:
            raise _refuse(AUTHORITY_INVALID, f"{rid}: accepted_strength applies to behaviour claims only")
        if raw["accept_as_sufficient"] is not True:
            raise _refuse(AUTHORITY_INVALID, f"{rid}: accept_as_sufficient must be the literal true")
        if not isinstance(raw["why"], str) or not raw["why"].strip():
            raise _refuse(AUTHORITY_INVALID, f"{rid}: why must say what the oracle proves for this requirement")
        covers[rid] = CoverageEntry(rid, raw["requirement_text_sha256"], raw["claim"], strength, raw["why"])
    digest = _sha256(data)
    stored_dir = os.path.join(state_root, AUTHORITY_STORE_DIR, digest)
    if not os.path.isdir(stored_dir):
        temporary = f"{stored_dir}.{uuid.uuid4().hex}.tmp"
        os.makedirs(temporary)
        with open(os.path.join(temporary, "manifest.json"), "wb") as handle:
            handle.write(data)
        for rel, content in asset_bytes.items():
            target = os.path.join(temporary, "assets", rel)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as handle:
                handle.write(content)
        os.replace(temporary, stored_dir)
    original = manifest.get("original_oracle") or {}
    return AuthorityBundle(
        digest=digest, stored_dir=stored_dir, source_path=real, authority_id=manifest["authority_id"],
        goal_sha256=manifest["goal_sha256"], requirement_set_sha256=manifest["requirement_set_sha256"],
        base_revision=manifest["base_revision"], assets=dict(assets), prepare=prepare, verify=verify,
        timeout_seconds=timeout, language=toolchain["language"], build_tool=toolchain["build_tool"],
        visibility=manifest["visibility"], covers=covers, original_oracle=dict(original) if isinstance(original, dict) else {},
    )


def bound_authority_bundle(engine: Any) -> Optional[AuthorityBundle]:
    """The bundle bound to ``engine`` for this run (the CLI sets
    ``WorkflowEngine.verification_authority_bundle``), or None."""
    bundle = getattr(engine, "verification_authority_bundle", None)
    return bundle if isinstance(bundle, AuthorityBundle) else None


# ------------------------------------------------------------ execution

@dataclass
class AuthorityRun:
    verdict: str = VERDICT_INDETERMINATE
    reason_code: str = AUTHORITY_INDETERMINATE
    reason: str = ""
    prepare_result: Optional[Dict[str, Any]] = None
    verify_result: Optional[Dict[str, Any]] = None
    verdict_file: Optional[Dict[str, Any]] = None
    candidate_digest: Optional[str] = None
    scratch: Optional[str] = None
    egress: Dict[str, Any] = field(default_factory=dict)

    def evidence(self) -> Dict[str, Any]:
        def summary(result: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
            if result is None:
                return None
            return {"returncode": result.get("returncode"), "timeout": result.get("timeout"),
                    "stdout_tail": str(result.get("stdout") or "")[-2000:],
                    "stderr_tail": str(result.get("stderr") or "")[-2000:], "egress": result.get("egress"),
                    "toolchain_identity": result.get("toolchain_identity")}
        return {"verdict": self.verdict, "reason_code": self.reason_code, "reason": self.reason,
                "prepare": summary(self.prepare_result), "verify": summary(self.verify_result),
                "verdict_file": self.verdict_file, "candidate_digest": self.candidate_digest}


def _tree_digest(root: str) -> str:
    digests: Dict[str, str] = {}
    for current, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in _COPY_IGNORE)
        for name in sorted(files):
            full = os.path.join(current, name)
            rel = os.path.relpath(full, root)
            if rel.startswith(STAGE_RELATIVE):
                continue
            try:
                with open(full, "rb") as handle:
                    digests[rel] = _sha256(handle.read())
            except OSError:
                digests[rel] = "unreadable"
    return _sha256(json.dumps(digests, sort_keys=True).encode("utf-8"))


def _stage_assets(bundle: AuthorityBundle, workspace: str) -> str:
    stage = os.path.join(workspace, STAGE_RELATIVE)
    os.makedirs(stage)
    for rel in bundle.assets:
        source = os.path.join(bundle.stored_dir, "assets", rel)
        target = os.path.join(stage, rel)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copyfile(source, target)
        os.chmod(target, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return stage


def _assets_intact(bundle: AuthorityBundle, stage: str) -> Optional[str]:
    for rel, digest in bundle.assets.items():
        try:
            with open(os.path.join(stage, rel), "rb") as handle:
                if _sha256(handle.read()) != digest:
                    return rel
        except OSError:
            return rel
    return None


def _rewrite_argv(argv: Sequence[str], assets: Mapping[str, str]) -> List[str]:
    """Asset paths in the argv become their staged, workspace-relative paths."""
    return [os.path.join(STAGE_RELATIVE, token) if token in assets else token for token in argv]


def _cache_for(validator: Any, build_tool: str) -> Optional[str]:
    """The Kriya-managed dependency cache the validator mounts for the build
    tool (the same path its own gates use), None for pip/none."""
    if build_tool in ("maven", "gradle"):
        return validator._dependency_cache_dir(build_tool)  # pylint: disable=protected-access
    return None


def run_authority_bundle(
    bundle: AuthorityBundle, candidate_root: str, *, state_root: str,
    validator_factory: Callable[[str], Any],
) -> AuthorityRun:
    """Execute the bundle once against a Kriya-owned copy of ``candidate_root``
    through the validator's containment boundary. ``validator_factory(root)``
    builds the production PolymorphicValidator for the copy."""
    run = AuthorityRun()
    scratch = os.path.join(state_root, AUTHORITY_RUNS_DIR, uuid.uuid4().hex)
    export = os.path.join(scratch, "workspace")
    run.scratch = scratch
    try:
        os.makedirs(scratch)
        shutil.copytree(candidate_root, export, symlinks=True, ignore=shutil.ignore_patterns(*_COPY_IGNORE))
        run.candidate_digest = _tree_digest(export)
        try:
            validator = validator_factory(export)
        except ContainmentSetupError as error:
            run.reason_code, run.reason = AUTHORITY_EXECUTION_UNAVAILABLE, f"containment refused: {error}"
            return run
        autonomy = getattr(validator, "autonomy_cfg", None)
        if not getattr(autonomy, "contained_execution_required", False):
            run.reason_code = AUTHORITY_EXECUTION_UNAVAILABLE
            run.reason = ("an external acceptance command runs only under Kriya's containment "
                          "(autonomy.contained_execution_required); never on the host")
            return run
        stage = _stage_assets(bundle, export)
        cache = _cache_for(validator, bundle.build_tool)
        try:
            if bundle.prepare is not None:
                run.prepare_result = _run_prepare(validator, bundle, export, cache)
                if run.prepare_result.get("timeout") or run.prepare_result.get("returncode") != 0:
                    run.reason_code = AUTHORITY_PREPARE_FAILED
                    run.reason = "the acquisition phase did not complete (environment outcome, never a verdict)"
                    return run
            run.verify_result = validator._run_cmd_with_timeout(  # pylint: disable=protected-access
                _rewrite_argv(bundle.verify, bundle.assets), cwd=export, timeout=bundle.timeout_seconds,
                network=NetworkAuthority.DENIED, dependency_cache_path=cache, dependency_cache_writable=False,
                workspace_path=export,
            )
        except ContainmentSetupError as error:
            run.reason_code, run.reason = AUTHORITY_EXECUTION_UNAVAILABLE, f"containment refused: {error}"
            return run
        changed = _assets_intact(bundle, stage)
        if changed is not None:
            run.reason_code, run.reason = AUTHORITY_ASSETS_CHANGED, f"asset {changed} changed during the run"
            return run
        if _tree_digest(export) != run.candidate_digest:
            run.reason_code = AUTHORITY_CANDIDATE_CHANGED_DURING_RUN
            run.reason = "candidate files changed while the authority ran"
            return run
        result = run.verify_result
        if result.get("timeout"):
            run.reason_code, run.reason = AUTHORITY_TIMEOUT, f"verify exceeded {bundle.timeout_seconds}s"
            return run
        verdict_path = os.path.join(stage, VERDICT_FILE)
        if os.path.isfile(verdict_path):
            try:
                with open(verdict_path, "rb") as handle:
                    run.verdict_file = json.loads(handle.read().decode("utf-8"))
            except (OSError, UnicodeDecodeError, ValueError):
                run.reason_code, run.reason = AUTHORITY_VERDICT_INCONSISTENT, "verdict.json is unreadable"
                return run
        code = result.get("returncode")
        exit_verdict = VERDICT_PASS if code == 0 else VERDICT_FAIL if code == 1 else VERDICT_INDETERMINATE
        declared = (run.verdict_file or {}).get("verdict") if isinstance(run.verdict_file, dict) else None
        if run.verdict_file is not None and declared != exit_verdict:
            run.reason_code = AUTHORITY_VERDICT_INCONSISTENT
            run.reason = f"verdict.json says {declared!r}, the exit code {code} says {exit_verdict}"
            return run
        run.verdict = exit_verdict
        run.reason_code = {VERDICT_PASS: AUTHORITY_PASSED, VERDICT_FAIL: AUTHORITY_FAILED}.get(exit_verdict, AUTHORITY_INDETERMINATE)
        run.reason = f"verify exited {code}"
        return run
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


# ------------------------------------------------------------ closure

def close_requirements_with_authority_bundle(
    ledger: ObligationLedger, requirements: RequirementSet, bundle: AuthorityBundle, contract: VerificationContract, *,
    execute: Callable[[], AuthorityRun], source: str, revision: Any, base_revision: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Judge every requirement the contract bound to this bundle, from one
    run (``execute()``, lazily, only when a bound requirement is open):
    PASS -> the covered claim SATISFIED (operator sufficiency, HUMAN_ACCEPTED
    class, bound to the exact requirement text like a B3 approval); FAIL ->
    VIOLATED counter-evidence; anything else -> INDETERMINATE (nothing closes).
    The record carries the contract's required claims."""
    from kriya.workflow.contract_compilation import CLOSER_EXTERNAL_ACCEPTANCE

    bound = [(rid, binding) for rid, binding in contract.binding_closers(CLOSER_EXTERNAL_ACCEPTANCE)
             if binding.authority_digest == bundle.digest]
    outcomes = requirement_outcomes(ledger, requirements)
    pending = [(rid, binding) for rid, binding in bound
               if outcomes.get(rid) in (RequirementOutcome.UNVERIFIED, RequirementOutcome.PENDING)]
    if not pending:
        return []
    if base_revision is not None and base_revision != bundle.base_revision:
        # The bundle binds the workspace revision it was made for (review: a
        # milestone's later units move HEAD); another base never consumes it.
        return [{"requirement": rid, "kind": EXTERNAL_ACCEPTANCE_METHOD, "closed": False,
                 "authority_digest": bundle.digest, "reason_code": AUTHORITY_BASE_MISMATCH,
                 "reason": f"the authority binds base {bundle.base_revision[:12]}, the run's base is {base_revision[:12]}"}
                for rid, _binding in pending]
    run = execute()
    claims_map = contract.required_claims_by_requirement()
    attempts: List[Dict[str, Any]] = []
    for rid, _binding in pending:
        coverage = bundle.covers.get(rid)
        requirement = requirements.get(rid)
        record = ledger.current(requirement_obligation_id(rid))
        evidence_id = (record.evidence or {}).get("evidence_id") if record is not None else None
        entry: Dict[str, Any] = {"requirement": rid, "kind": EXTERNAL_ACCEPTANCE_METHOD, "closed": False,
                                 "authority_digest": bundle.digest, "authority_id": bundle.authority_id,
                                 **run.evidence()}
        if coverage is None or requirement is None:
            entry["reason"] = "the bundle declares no coverage for this requirement"
        elif not evidence_id:
            entry["reason"] = "the verdict has no evidence id to bind to"
        elif coverage.requirement_text_sha256 != _text_sha256(requirement.text):
            entry["reason"] = "the coverage was declared for other words"
        else:
            status = (ObligationStatus.SATISFIED if run.verdict == VERDICT_PASS
                      else ObligationStatus.VIOLATED if run.verdict == VERDICT_FAIL else ObligationStatus.INDETERMINATE)
            detail = {**run.evidence(), "required_claims": list(claims_map.get(rid) or (coverage.claim,)),
                      "approval_digest": bundle.digest, "closure_method": EXTERNAL_ACCEPTANCE_METHOD,
                      "approval": {"requirement_id": rid, "requirement_text_sha256": coverage.requirement_text_sha256,
                                   "accepted_strength": coverage.accepted_strength, "why": coverage.why},
                      "authority_id": bundle.authority_id, "visibility": bundle.visibility}
            record_requirement_claim(ledger, requirements, rid, coverage.claim, evidence_id=evidence_id,
                                     method=EXTERNAL_ACCEPTANCE_METHOD, detail=detail, source=source,
                                     revision=revision, status=status)
            after = requirement_outcomes(ledger, requirements)[rid]
            entry.update({"claim": coverage.claim, "status": status.value, "outcome": after.value,
                          "closed": after in (RequirementOutcome.HUMAN_ACCEPTED, RequirementOutcome.CLOSED_BY_EVIDENCE),
                          "violated": after is RequirementOutcome.VIOLATED})
        attempts.append(entry)
    return attempts


def authority_template(bundle_dir: str, requirement_set: RequirementSet, goal: str, *, base_revision: str,
                       language: str, build_tool: str, verify: Sequence[str], prepare: Optional[Sequence[str]],
                       covers: Iterable[Tuple[str, str, Optional[str], str]], authority_id: str,
                       timeout_seconds: int = 900, original_oracle: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """An operator's manifest skeleton for the assets present under
    ``bundle_dir`` (digests computed) and the given coverage tuples
    (requirement id, claim, accepted strength, why); the operator reviews and
    saves it beside the assets. Kriya never authors coverage itself."""
    assets: Dict[str, str] = {}
    for current, _dirs, files in os.walk(bundle_dir):
        for name in sorted(files):
            full = os.path.join(current, name)
            rel = os.path.relpath(full, bundle_dir)
            if rel == "manifest.json" or rel.endswith(".json") and rel.startswith("manifest"):
                continue
            with open(full, "rb") as handle:
                assets[rel] = _sha256(handle.read())
    return {"format": AUTHORITY_FORMAT, "authority_id": authority_id, "type": AUTHORITY_TYPE,
            "goal_sha256": goal_identity(goal), "requirement_set_sha256": requirement_set.digest,
            "base_revision": base_revision, "assets": assets, "prepare": list(prepare) if prepare else None,
            "verify": list(verify), "timeout_seconds": timeout_seconds,
            "toolchain": {"language": language, "build_tool": build_tool}, "verdict_protocol": "exit_code",
            "visibility": VISIBILITY_HIDDEN,
            "covers": [{"requirement_id": rid, "requirement_text_sha256": _text_sha256(requirement_set.get(rid).text),
                        "claim": claim, "accepted_strength": strength, "accept_as_sufficient": True, "why": why}
                       for rid, claim, strength, why in covers],
            **({"original_oracle": dict(original_oracle)} if original_oracle else {})}
