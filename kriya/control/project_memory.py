"""VERIFIED PROJECT MEMORY: safe, validated facts about ONE repository that later runs reuse (product/demo-72h).

What it is. A PROJECTION of authoritative run evidence onto the repository as it is NOW. A fact is a small, closed-vocabulary statement (the language, the build
system, the Java release, the modules, the verification gates a run passed) together with the exact evidence that supports it (the SHA-256 of the build file it was
read from). The stored record is a CACHE of that derivation: it is never higher authority than the current repository bytes, the run evidence it came from, or the
current configuration/security authority. Nothing here trains a model; nothing here is shared between repositories.

OBSERVE -> VERIFY -> STORE -> RETRIEVE -> REUSE -> INVALIDATE
  OBSERVE    deterministic readers over the build files of the repository (never over model text, a README, or "the file exists, so it is true").
  VERIFY     a fact is stored only after a run reached a verified SUCCESS: the terminal status is success AND the run's own deterministic gate evidence shows a passing
             compile gate. A run that did not, learns nothing; the facts it could have taught are counted as REJECTED (RUN_NOT_VERIFIED).
  STORE      ``<authority root>/<control directory>/control/project-memory/memory.json`` through ``control_store.write_control_file`` (private 0700/0600, revision-grounded, atomic);
             every stored text passes the canonical redactor first, and a value the redactor would change (a credential-shaped string) is REJECTED, never stored.
  RETRIEVE   ``project(workspace)`` re-reads the evidence files NOW: a fact whose evidence digest differs, or whose file is gone, is STALE (reason code) and is never reused.
  REUSE      ``reference_text(...)``: CURRENT facts only, rendered as reference material for ``reference_context`` - fenced as untrusted by its consumer, never joined to
             the goal, never an instruction, never an approval. A remembered fact never runs anything: commands are not facts here, and every command still goes through
             command policy, approval and containment.
  INVALIDATE a changed or missing evidence file turns the fact STALE at once; the next verified run replaces it (the old value is not kept as truth).

Fail closed: an unreadable, oversized, foreign (another repository's) or wrong-schema store contributes nothing and says why; a run is never blocked or altered by memory.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from kriya.context.workspace_identity import workspace_identity
from kriya.control.control_store import CONTROL_DIRECTORY, control_roots, write_control_file
from kriya.foundation.redaction import redact_text
from kriya.repository.commit import read_file_revision

SCHEMA_VERSION = 1
STORE_DIRECTORY = "project-memory"
STORE_FILE = "memory.json"
MAX_STORE_BYTES = 256 * 1024
MAX_EVIDENCE_FILE_BYTES = 1024 * 1024
MAX_VALUE_CHARS = 160
MAX_REJECTED_KEPT = 50
MAX_RUNS_KEPT = 30
MAX_EXTENSION_WALK = 4000
_SKIPPED_DIRECTORIES = frozenset({".git", CONTROL_DIRECTORY, "node_modules", ".venv", "venv", "target", "build", "__pycache__", ".gradle", ".idea"})

# Fact kinds: a closed vocabulary (one fact per kind per repository).
KIND_LANGUAGE = "language"
KIND_BUILD_SYSTEM = "build_system"
KIND_JAVA_RELEASE = "java_release"
KIND_MODULES = "modules"
KIND_GATES = "verified_gates"
FACT_KINDS = (KIND_LANGUAGE, KIND_BUILD_SYSTEM, KIND_JAVA_RELEASE, KIND_MODULES, KIND_GATES)
LABELS = {KIND_LANGUAGE: "Language", KIND_BUILD_SYSTEM: "Build system", KIND_JAVA_RELEASE: "Java release", KIND_MODULES: "Modules", KIND_GATES: "Verification that passed"}

# Currency states and the closed reason codes of a STALE fact.
STATUS_CURRENT = "CURRENT"
STATUS_STALE = "STALE"
STALE_EVIDENCE_CHANGED = "EVIDENCE_CHANGED"
STALE_EVIDENCE_MISSING = "EVIDENCE_MISSING"
STALE_EVIDENCE_UNREADABLE = "EVIDENCE_UNREADABLE"
# Closed reason codes of a REJECTED candidate / unusable store.
REJECT_RUN_NOT_VERIFIED = "RUN_NOT_VERIFIED"
REJECT_UNSAFE_VALUE = "UNSAFE_VALUE"
STORE_OK = "OK"
STORE_ABSENT = "ABSENT"
STORE_UNREADABLE = "STORE_UNREADABLE"
STORE_TOO_LARGE = "STORE_TOO_LARGE"
STORE_SCHEMA_UNSUPPORTED = "STORE_SCHEMA_UNSUPPORTED"
STORE_FOREIGN_REPOSITORY = "STORE_FOREIGN_REPOSITORY"

# The gate types a run's deterministic evidence can name (kriya/workflow, ``deterministic_gate_evidence``); only these are teachable, in this display order.
GATE_ORDER = ("compile", "targeted_test", "regression_test", "run_verification")
COMPILE_GATE = "compile"

EVIDENCE_FILE = "file"
EVIDENCE_EXTENSION = "extension_present"


@dataclass(frozen=True)
class Evidence:
    kind: str                  # EVIDENCE_FILE or EVIDENCE_EXTENSION
    path: str                  # repository-relative file, or a ".ext" suffix
    sha256: Optional[str]      # EVIDENCE_FILE only

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "path": self.path, "sha256": self.sha256}


@dataclass(frozen=True)
class Candidate:
    """A fact a deterministic reader derived from the repository, before verification."""
    kind: str
    value: str
    evidence: Tuple[Evidence, ...]


@dataclass(frozen=True)
class Fact:
    kind: str
    label: str
    value: str
    status: str
    stale_reason: Optional[str]
    evidence: Tuple[Evidence, ...]
    learned_run_id: str
    learned_at: str
    confirmed_runs: int
    reused_runs: int

    @property
    def fact_id(self) -> str:
        return self.kind

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.fact_id, "kind": self.kind, "label": self.label, "value": self.value, "status": self.status, "stale_reason": self.stale_reason,
                "evidence": [e.to_dict() for e in self.evidence], "learned_run_id": self.learned_run_id, "learned_at": self.learned_at,
                "confirmed_runs": self.confirmed_runs, "reused_runs": self.reused_runs,
                "why": why(self)}


def why(fact: "Fact") -> str:
    """"Why does Kriya know this?" - the provenance, from the stored record only (never a model answer)."""
    sources = ", ".join(e.path if e.kind == EVIDENCE_FILE else f"a {e.path} file in the repository" for e in fact.evidence) or "the run's own gate evidence"
    base = (f"Read from {sources}, and verified by run {fact.learned_run_id}: it ended in success with a passing compile gate. "
            f"Confirmed by {fact.confirmed_runs} verified run(s); reused as reference in {fact.reused_runs} later run(s).")
    if fact.status == STATUS_STALE:
        base += f" NOT reused now: {fact.stale_reason}."
    return base


@dataclass(frozen=True)
class RunEntry:
    run_id: str
    at: str
    outcome: str               # "learned" | "rejected"
    learned: Tuple[str, ...]
    refreshed: Tuple[str, ...]
    reused: Tuple[str, ...]
    rejected: int
    evidence_run_id: Optional[str] = None      # the control-plane run id the run's evidence store and RunRecord carry (what the cockpit knows the run by)

    def to_dict(self) -> Dict[str, Any]:
        return {"run_id": self.run_id, "at": self.at, "outcome": self.outcome, "learned": list(self.learned), "refreshed": list(self.refreshed),
                "reused": list(self.reused), "rejected": self.rejected, "evidence_run_id": self.evidence_run_id}


@dataclass(frozen=True)
class Projection:
    """The memory of one repository as it is NOW."""
    repository: str
    store_state: str
    store_detail: Optional[str]
    facts: Tuple[Fact, ...] = ()
    rejected: Tuple[Dict[str, Any], ...] = ()
    runs: Tuple[RunEntry, ...] = ()

    def current(self) -> Tuple[Fact, ...]:
        return tuple(f for f in self.facts if f.status == STATUS_CURRENT)

    def stale(self) -> Tuple[Fact, ...]:
        return tuple(f for f in self.facts if f.status == STATUS_STALE)

    def counts(self) -> Dict[str, int]:
        last = self.runs[-1] if self.runs else None
        return {"current": len(self.current()), "stale": len(self.stale()), "rejected_total": len(self.rejected),
                "learned_last_run": len(last.learned) + len(last.refreshed) if last else 0,
                "reused_last_run": len(last.reused) if last else 0,
                "rejected_last_run": last.rejected if last else 0}

    def to_dict(self) -> Dict[str, Any]:
        return {"schema_version": SCHEMA_VERSION, "repository": self.repository, "store_state": self.store_state, "store_detail": self.store_detail,
                "counts": self.counts(), "facts": [f.to_dict() for f in self.facts],
                "rejected": list(self.rejected), "runs": [r.to_dict() for r in self.runs]}


# --------------------------------------------------------------------------------------------------------------------------------------- paths and bytes

def store_path(workspace_path: str) -> str:
    """The store of the repository ``workspace_path`` belongs to: beneath the AUTHORITY root's ``.kriya/control`` (the one root every control store keys by)."""
    return os.path.join(control_roots(workspace_path)[1], "control", STORE_DIRECTORY, STORE_FILE)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_regular_file(root: str, relative: str, limit: int = MAX_EVIDENCE_FILE_BYTES) -> Tuple[Optional[bytes], Optional[str]]:
    """(bytes, None) for a regular, non-symlink file of at most ``limit`` bytes strictly inside ``root``; (None, reason) otherwise. Never follows a link out of the repository."""
    if os.path.isabs(relative) or ".." in relative.replace("\\", "/").split("/"):
        return None, STALE_EVIDENCE_UNREADABLE
    path = os.path.join(root, relative)
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None, STALE_EVIDENCE_MISSING
    except OSError:
        return None, STALE_EVIDENCE_UNREADABLE
    if not os.path.isfile(path) or os.path.islink(path) or info.st_size > limit:
        return None, STALE_EVIDENCE_UNREADABLE
    try:
        with open(path, "rb") as handle:
            return handle.read(limit + 1), None
    except OSError:
        return None, STALE_EVIDENCE_UNREADABLE


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _extension_present(root: str, suffix: str) -> bool:
    seen = 0
    for directory, subdirectories, filenames in os.walk(root):
        subdirectories[:] = [d for d in subdirectories if d not in _SKIPPED_DIRECTORIES and not os.path.islink(os.path.join(directory, d))]
        for name in filenames:
            seen += 1
            if seen > MAX_EXTENSION_WALK:
                return False
            if name.endswith(suffix) and not name.startswith("."):
                return True
    return False


# --------------------------------------------------------------------------------------------------------------------------------------- OBSERVE

_MAVEN_RELEASE_RES = (re.compile(r"<maven\.compiler\.release>\s*(\d{1,2})\s*<"), re.compile(r"<release>\s*(\d{1,2})\s*</release>"),
                      re.compile(r"<maven\.compiler\.(?:source|target)>\s*(?:1\.)?(\d{1,2})\s*<"), re.compile(r"<java\.version>\s*(?:1\.)?(\d{1,2})\s*<"))
_MAVEN_MODULE_RE = re.compile(r"<module>\s*([A-Za-z0-9_.\-/]{1,80})\s*</module>")
_GRADLE_RELEASE_RES = (re.compile(r"JavaLanguageVersion\.of\(\s*(\d{1,2})\s*\)"), re.compile(r"JavaVersion\.VERSION_(?:1_)?(\d{1,2})\b"),
                       re.compile(r"(?:source|target)Compatibility\s*=\s*['\"]?(?:1\.)?(\d{1,2})\b"))
_BUILD_FILES = ("pom.xml", "build.gradle", "build.gradle.kts", "pyproject.toml", "setup.py", "requirements.txt", "package.json")


def _file_evidence(root: str, relative: str) -> Optional[Tuple[Evidence, str]]:
    data, _reason = _read_regular_file(root, relative)
    if data is None or len(data) > MAX_EVIDENCE_FILE_BYTES:
        return None
    return Evidence(EVIDENCE_FILE, relative, _digest(data)), data.decode("utf-8", errors="replace")


def derive_candidates(workspace_path: str, written_files: Sequence[str] = ()) -> List[Candidate]:
    """The facts the repository's build files support, read deterministically; nothing here is verified yet. ``written_files`` (the run's own committed files)
    lets a repository without a build file still teach its language from the extensions it really contains."""
    root = os.path.realpath(workspace_path)
    out: List[Candidate] = []
    present = {name: _file_evidence(root, name) for name in _BUILD_FILES}

    pom, gradle_file = present["pom.xml"], present["build.gradle"] or present["build.gradle.kts"]
    if pom is not None:
        evidence, text = pom
        out.append(Candidate(KIND_BUILD_SYSTEM, "Maven", (evidence,)))
        out.append(Candidate(KIND_LANGUAGE, "Java", (evidence,)))
        release = next((m.group(1) for r in _MAVEN_RELEASE_RES for m in [r.search(text)] if m), None)
        if release:
            out.append(Candidate(KIND_JAVA_RELEASE, release, (evidence,)))
        modules = sorted(set(_MAVEN_MODULE_RE.findall(text)))[:12]
        if modules:
            out.append(Candidate(KIND_MODULES, ", ".join(modules), (evidence,)))
    elif gradle_file is not None:
        evidence, text = gradle_file
        out.append(Candidate(KIND_BUILD_SYSTEM, "Gradle", (evidence,)))
        out.append(Candidate(KIND_LANGUAGE, "Java" if "java" in text.lower() or "kotlin" not in text.lower() else "Kotlin", (evidence,)))
        release = next((m.group(1) for r in _GRADLE_RELEASE_RES for m in [r.search(text)] if m), None)
        if release:
            out.append(Candidate(KIND_JAVA_RELEASE, release, (evidence,)))
    elif present["pyproject.toml"] is not None or present["setup.py"] is not None or present["requirements.txt"] is not None:
        evidence = (present["pyproject.toml"] or present["setup.py"] or present["requirements.txt"])[0]
        out.append(Candidate(KIND_LANGUAGE, "Python", (evidence,)))
        out.append(Candidate(KIND_BUILD_SYSTEM, "pyproject" if present["pyproject.toml"] is not None else "pip", (evidence,)))
    elif present["package.json"] is not None:
        evidence = present["package.json"][0]
        out.append(Candidate(KIND_LANGUAGE, "JavaScript/TypeScript", (evidence,)))
        out.append(Candidate(KIND_BUILD_SYSTEM, "npm", (evidence,)))
    else:
        for suffix, language in ((".py", "Python"), (".java", "Java"), (".go", "Go"), (".rs", "Rust")):
            if any(f.endswith(suffix) for f in written_files):
                out.append(Candidate(KIND_LANGUAGE, language, (Evidence(EVIDENCE_EXTENSION, suffix, None),)))
                break
    return out


# --------------------------------------------------------------------------------------------------------------------------------------- RETRIEVE

def _evidence_current(root: str, evidence: Evidence) -> Optional[str]:
    """None when the evidence still holds NOW, else the STALE reason code."""
    if evidence.kind == EVIDENCE_EXTENSION:
        return None if _extension_present(root, evidence.path) else STALE_EVIDENCE_MISSING
    if evidence.kind != EVIDENCE_FILE or not evidence.sha256:
        return STALE_EVIDENCE_UNREADABLE
    data, reason = _read_regular_file(root, evidence.path)
    if data is None:
        return reason
    return None if _digest(data) == evidence.sha256 else STALE_EVIDENCE_CHANGED


def _load_store(workspace_path: str) -> Tuple[Dict[str, Any], str, Optional[str]]:
    path = store_path(workspace_path)
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return {}, STORE_ABSENT, None
    except OSError as error:
        return {}, STORE_UNREADABLE, type(error).__name__
    if os.path.islink(path) or not os.path.isfile(path):
        return {}, STORE_UNREADABLE, "not a regular file"
    if info.st_size > MAX_STORE_BYTES:
        return {}, STORE_TOO_LARGE, f"{info.st_size} bytes"
    try:
        with open(path, "rb") as handle:
            document = json.loads(handle.read().decode("utf-8"))
    except (OSError, ValueError) as error:
        return {}, STORE_UNREADABLE, type(error).__name__
    if not isinstance(document, dict) or document.get("schema_version") != SCHEMA_VERSION:
        return {}, STORE_SCHEMA_UNSUPPORTED, str(document.get("schema_version") if isinstance(document, dict) else None)
    if document.get("repository") != workspace_identity(workspace_path):
        return {}, STORE_FOREIGN_REPOSITORY, None
    return document, STORE_OK, None


def _evidence_from(raw: Any) -> Tuple[Evidence, ...]:
    out = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, dict) and item.get("kind") in (EVIDENCE_FILE, EVIDENCE_EXTENSION) and isinstance(item.get("path"), str):
            sha = item.get("sha256")
            out.append(Evidence(item["kind"], item["path"], sha if isinstance(sha, str) else None))
    return tuple(out)


def project(workspace_path: str) -> Projection:
    """The memory of this repository as it is now: every stored fact re-checked against the current evidence files. Read-only."""
    identity = workspace_identity(workspace_path)
    document, state, detail = _load_store(workspace_path)
    root = os.path.realpath(workspace_path)
    facts: List[Fact] = []
    for kind in FACT_KINDS:
        raw = (document.get("facts") or {}).get(kind) if isinstance(document.get("facts"), dict) else None
        if not isinstance(raw, dict) or not isinstance(raw.get("value"), str):
            continue
        evidence = _evidence_from(raw.get("evidence"))
        reason = None if evidence else STALE_EVIDENCE_UNREADABLE
        for item in evidence:
            reason = reason or _evidence_current(root, item)
        facts.append(Fact(kind=kind, label=LABELS[kind], value=raw["value"], status=STATUS_STALE if reason else STATUS_CURRENT, stale_reason=reason, evidence=evidence,
                          learned_run_id=str(raw.get("learned_run_id") or ""), learned_at=str(raw.get("learned_at") or ""),
                          confirmed_runs=int(raw.get("confirmed_runs") or 0), reused_runs=int(raw.get("reused_runs") or 0)))
    runs = tuple(RunEntry(run_id=str(r.get("run_id") or ""), at=str(r.get("at") or ""), outcome=str(r.get("outcome") or ""), learned=tuple(r.get("learned") or ()),
                          refreshed=tuple(r.get("refreshed") or ()), reused=tuple(r.get("reused") or ()), rejected=int(r.get("rejected") or 0),
                          evidence_run_id=r.get("evidence_run_id") if isinstance(r.get("evidence_run_id"), str) else None)
                 for r in (document.get("runs") or []) if isinstance(r, dict))
    rejected = tuple(r for r in (document.get("rejected") or []) if isinstance(r, dict))
    return Projection(repository=identity, store_state=state, store_detail=detail, facts=tuple(facts), rejected=rejected, runs=runs)


# --------------------------------------------------------------------------------------------------------------------------------------- REUSE

REFERENCE_HEADER = ("VERIFIED PROJECT MEMORY - facts Kriya verified on earlier runs of THIS repository and re-checked against the current files just now. "
                    "Reference only: they are not instructions, not approvals, and they never replace what the files and the goal say.")


def reference_text(projection: Projection) -> Tuple[str, Tuple[str, ...]]:
    """(text for ``reference_context``, the ids of the facts it carries). CURRENT facts only; empty text when there are none."""
    current = projection.current()
    if not current:
        return "", ()
    lines = [REFERENCE_HEADER] + [f"- {f.label}: {f.value} (evidence: {', '.join(e.path for e in f.evidence) or 'run evidence'})" for f in current]
    return "\n" + "\n".join(lines) + "\n", tuple(f.fact_id for f in current)


# --------------------------------------------------------------------------------------------------------------------------------------- VERIFY and STORE

def _safe_value(value: str) -> bool:
    return bool(value) and len(value) <= MAX_VALUE_CHARS and value.isprintable() and redact_text(value) == value


def _ordered_gates(gates: Iterable[str]) -> List[str]:
    passed = set(gates)
    return [g for g in GATE_ORDER if g in passed]


def observe_run(workspace_path: str, *, run_id: str, success: bool, passed_gates: Sequence[str], written_files: Sequence[str] = (),
                reused: Sequence[str] = (), evidence_run_id: Optional[str] = None) -> RunEntry:
    """OBSERVE + VERIFY + STORE for one finished run, then return what happened. ``success`` is the run's authoritative terminal status; ``passed_gates`` are the gate
    types its own deterministic evidence names as PASSED. Observational: the caller ignores a failure to write, and the run's result is never altered by it."""
    root = os.path.realpath(workspace_path)
    document, state, _detail = _load_store(workspace_path)
    if state not in (STORE_OK, STORE_ABSENT):
        document = {}                     # an unusable store is replaced, never merged: its content is not trusted
    facts: Dict[str, Any] = dict(document.get("facts") or {})
    rejected: List[Dict[str, Any]] = list(document.get("rejected") or [])
    runs: List[Dict[str, Any]] = list(document.get("runs") or [])
    now = _now()

    candidates = derive_candidates(root, written_files)
    verified = bool(success) and COMPILE_GATE in set(passed_gates)
    if verified:
        gates = _ordered_gates(passed_gates)
        gate_evidence = tuple(c.evidence[0] for c in candidates if c.kind == KIND_BUILD_SYSTEM) or tuple(c.evidence[0] for c in candidates if c.kind == KIND_LANGUAGE)
        if gates and gate_evidence:
            candidates.append(Candidate(KIND_GATES, ", ".join(g.replace("_", " ") for g in gates), gate_evidence))
    learned: List[str] = []
    refreshed: List[str] = []
    rejected_now = 0
    for candidate in candidates:
        if not verified:
            rejected_now += 1
            rejected.append({"run_id": run_id, "kind": candidate.kind, "reason": REJECT_RUN_NOT_VERIFIED, "at": now})
            continue
        if not _safe_value(candidate.value):
            rejected_now += 1
            rejected.append({"run_id": run_id, "kind": candidate.kind, "reason": REJECT_UNSAFE_VALUE, "at": now})
            continue
        previous = facts.get(candidate.kind)
        same = isinstance(previous, dict) and previous.get("value") == candidate.value and previous.get("evidence") == [e.to_dict() for e in candidate.evidence]
        facts[candidate.kind] = {
            "value": candidate.value, "evidence": [e.to_dict() for e in candidate.evidence],
            "learned_run_id": previous["learned_run_id"] if same else run_id, "learned_at": previous["learned_at"] if same else now,
            "confirmed_runs": (int(previous.get("confirmed_runs") or 0) + 1) if same else 1,
            "reused_runs": int(previous.get("reused_runs") or 0) if isinstance(previous, dict) and same else 0,
        }
        (refreshed if same else learned).append(candidate.kind)
    for kind in reused:                                           # the reuse is recorded on the facts that were really carried into this run
        if kind in facts and isinstance(facts[kind], dict):
            facts[kind]["reused_runs"] = int(facts[kind].get("reused_runs") or 0) + 1
    entry = RunEntry(run_id=run_id, at=now, outcome="learned" if verified else "rejected", learned=tuple(learned), refreshed=tuple(refreshed),
                     reused=tuple(sorted(set(reused))), rejected=rejected_now, evidence_run_id=evidence_run_id)
    runs.append(entry.to_dict())
    document = {"schema_version": SCHEMA_VERSION, "repository": workspace_identity(workspace_path), "facts": facts,
                "rejected": rejected[-MAX_REJECTED_KEPT:], "runs": runs[-MAX_RUNS_KEPT:]}
    text = json.dumps(document, sort_keys=True, indent=1)
    path = store_path(workspace_path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    write_control_file(workspace_path, path, text, expected_revision=read_file_revision(path))
    return entry


def control_run_id_since(workspace_path: str, since_iso: str) -> Optional[str]:
    """The control-plane run id (the id the run's evidence store and RunRecord carry) of the RunRecord of this repository LAST UPDATED at or after ``since_iso`` (the moment the
    finished run's process started). One run owns a repository at a time, so a record touched during this process is this run's. It is keyed on the update time, not creation:
    a cockpit-launched process creates its RunRecord before the workflow takes its start time (MEASURED 2026-11-07) but keeps updating it until the run ends. A record with no
    update time falls back to its creation time. None when none can be read."""
    directory = os.path.join(control_roots(workspace_path)[1], "control", "runs")
    try:
        since = datetime.fromisoformat(since_iso)
        names = sorted(os.listdir(directory))
    except (OSError, ValueError):
        return None
    best: Optional[Tuple[datetime, str]] = None
    for name in names:
        if not name.endswith(".json"):
            continue
        data, _reason = _read_regular_file(directory, name, limit=MAX_EVIDENCE_FILE_BYTES * 4)
        try:
            record = json.loads(data.decode("utf-8")) if data else None
            touched, run_id = datetime.fromisoformat(record.get("updated_at") or record["created_at"]), record["run_id"]
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
        if isinstance(run_id, str) and touched >= since and (best is None or touched > best[0]):
            best = (touched, run_id)
    return best[1] if best else None


def passed_gate_types(result: Dict[str, Any]) -> List[str]:
    """The gate types the run's own deterministic evidence names as PASSED (a result dict of ``run_generation_workflow``)."""
    out = []
    for item in result.get("deterministic_gate_evidence") or []:
        if isinstance(item, dict) and item.get("passed") is True and isinstance(item.get("type"), str):
            out.append(item["type"])
    return out


def result_is_verified_success(result: Dict[str, Any]) -> bool:
    """The run ended in verified success: its terminal metrics say success and every quality gate passed. A missing or malformed field is NOT success."""
    metrics = result.get("generation_metrics") if isinstance(result.get("generation_metrics"), dict) else {}
    return result.get("quality_gates_passed") is True and metrics.get("terminal_status") == "success"
