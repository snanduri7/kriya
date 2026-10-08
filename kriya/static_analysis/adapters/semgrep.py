"""The Semgrep Community Edition adapter (PRD-031A §5.2, §5.3, §5.5, §8.4, §8.5).

The only module that knows Semgrep: its flags, environment, JSON shape,
error kinds, rule-pack layout, language table and severity vocabulary. It
normalizes everything into ``kriya.static_analysis.model`` types before
anything above it sees a result, and it never decides policy.

Pinned behaviour this module relies on was observed on Semgrep 1.178.0
(pipx and the ``semgrep/semgrep`` OCI image, same JSON on the same inputs);
the recorded outputs live in ``tests/fixtures/static_analysis/semgrep/1.178.0``.
Classification is by structured evidence (``errors[]`` kinds, ``paths``),
never by documented exit codes, which 1.178.0 does not follow (§5.5 V14).
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import posixpath
import re
import shutil
import tempfile
import time
from dataclasses import dataclass, replace
from typing import Any, Dict, FrozenSet, List, Literal, Mapping, Optional, Sequence, Set, Tuple, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from kriya.platform.filesystem_semantics import PathRelation, path_relation
from kriya.static_analysis.model import (
    CAPABILITY_UNKNOWN,
    CONTAINMENT_UNAVAILABLE,
    PROVIDER_PROBE_FAILED,
    PROVIDER_VERSION_MISMATCH,
    RULE_PACK_DIGEST_MISMATCH,
    RULE_PACK_INVALID,
    RULE_PACK_UNPINNED,
    SCAN_FAILED,
    SCAN_INCOMPLETE,
    SCAN_OUTPUT_MALFORMED,
    SCAN_TIMEOUT,
    Finding,
    LanguageSupport,
    PrerequisiteResult,
    ProviderCapability,
    ProviderIdentity,
    ProviderProbe,
    RulePackIdentity,
    ScanError,
    ScanResult,
    ScanScope,
    ScanStatus,
    Severity,
    bytes_digest,
    canonical_digest,
)
from kriya.static_analysis.port import ExecutionContext, ScanRequest
from kriya.static_analysis.registry import register_provider
from kriya.tools.containment import (
    ContainmentProfile,
    ContainmentSetupError,
    MountSpec,
    NetworkAuthority,
    TrustClass,
)
from kriya.tools.process import ProcessController, ProcessResult
from kriya.tools.sandbox import build_restricted_env

PROVIDER = "semgrep"
EDITION = "community"
# Part of every identity: bump when the severity mapping below changes.
SEVERITY_MAP_VERSION = 1

_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
# Same shape the OCI backend accepts for ContainmentProfile.image_reference.
_PINNED_IMAGE_RE = re.compile(r"^[a-z0-9][a-z0-9._/:-]*@sha256:([0-9a-f]{64})$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REGISTRY_PREFIXES = ("p/", "r/", "s/")

_PROBE_TIMEOUT_SECONDS = 60
# The whole JSON document must survive capture intact: a truncated stdout is
# malformed output, never a shorter result.
_MAX_OUTPUT_CHARS = 256_000_000
_MESSAGE_LIMIT = 2048
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f]")

# Container layout (the OCI backend mounts the snapshot at its workspace
# path and makes it the working directory; /kriya/tmp is a per-container
# tmpfs, writable by uid 65534 - verified with the pinned image).
_CONTAINER_EXECUTABLE = "semgrep"
_CONTAINER_HOME = "/kriya/tmp/home"
_CONTAINER_RULES = "/kriya/rules"
_CONTAINER_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
_CONTAINER_UID = 65534
_CONTAINER_GID = 65534

# Semgrep state goes to $HOME/.semgrep (settings.yml, semgrep.log) and
# $HOME/.gitconfig (§5.5 V16): always a Kriya-owned scratch HOME.
_FIXED_ENV = {"SEMGREP_SEND_METRICS": "off", "SEMGREP_ENABLE_VERSION_CHECK": "0"}
# Semgrep writes temporary rule files under TMPDIR (observed under /tmp when
# unset): a Kriya-owned directory on the host, the container's tmpfs inside.
_CONTAINER_TMPDIR = "/kriya/tmp"

# Where Semgrep finds its engine first: the binary packaged in the
# ``semgrep.bin`` package of the interpreter its entry point runs
# (semgrep/semgrep_core.py compute_executable_path, 1.178.0), relative to
# that interpreter's prefix. Semgrep would fall back to PATH; Kriya never
# does - an engine it cannot locate here is a probe failure.
_ENGINE_GLOBS = (
    "lib/python3*/site-packages/semgrep/bin/semgrep-core",
    "lib/python3*/dist-packages/semgrep/bin/semgrep-core",
)

# Semgrep's own control files: never copied into a Kriya snapshot (§8.4).
CONTROL_FILES = (".semgrepignore", ".gitignore", ".semgrep", ".semgrep.yml", ".semgrep.yaml", ".semgrepconfig")

LIMITATION_SILENT_SYNTAX_RECOVERY = (
    "Semgrep CE may recover from a syntax error silently, reporting the file as scanned with no error "
    "(observed with Semgrep 1.178.0 on Python); parse completeness is therefore not claimed."
)
LIMITATION_FILE_LOCAL = (
    "Semgrep CE (--oss-only) analyzes within one file; cross-file analysis needs the Pro engine and is not claimed."
)
LIMITATION_HOST_NETWORK = (
    "Host mode: network is not enforced by the OS; network discipline comes from the fixed flags and environment."
)

# Semgrep CE language maturity, keyed on the exact pinned version
# (docs.semgrep.dev/supported-languages; C# is Pro-only and omitted). An
# unlisted version is CAPABILITY_UNKNOWN, never a guess.
_LANGUAGE_TABLE: Mapping[str, Mapping[str, str]] = {
    "1.178.0": {
        lang: "ga" for lang in (
            "java", "python", "javascript", "typescript", "go", "ruby", "kotlin",
            "c", "cpp", "rust", "php", "swift", "scala",
        )
    },
}

# A rule's ``languages:`` value -> Kriya language id. Anything else
# (generic, regex, yaml, json, dockerfile, bash, csharp, ...) targets no
# language of the table above and is ignored for coverage.
_LANGUAGE_ALIASES: Mapping[str, str] = {
    "java": "java",
    "python": "python", "python2": "python", "python3": "python", "py": "python",
    "javascript": "javascript", "js": "javascript",
    "typescript": "typescript", "ts": "typescript",
    "go": "go", "golang": "go",
    "ruby": "ruby", "rb": "ruby",
    "kotlin": "kotlin", "kt": "kotlin",
    "c": "c",
    "cpp": "cpp", "c++": "cpp",
    "rust": "rust", "rs": "rust",
    "php": "php",
    "swift": "swift",
    "scala": "scala",
}

_SECURITY_SEVERITY = {"critical": Severity.CRITICAL}
_IMPACT = {"HIGH": Severity.HIGH, "MEDIUM": Severity.MEDIUM, "LOW": Severity.LOW}
_RULE_SEVERITY = {"ERROR": Severity.HIGH, "WARNING": Severity.MEDIUM, "INFO": Severity.LOW}

# errors[] kinds that are rule/configuration problems. A SemgrepError is one
# only by its code: 4 invalid rule, 5 invalid YAML, 7 invalid config file,
# 8 unknown language (observed codes, §5.5 V14).
_CONFIG_ERROR_TYPES = frozenset({
    "Rule parse error", "InvalidRuleSchemaError", "UnknownLanguageError", "InvalidYaml", "Invalid YAML",
})
_UNKNOWN_LANGUAGE_TYPE = "UnknownLanguageError"
_CONFIG_SEMGREP_ERROR_CODES = frozenset({4, 5, 7, 8})
_RULE_FILE_SUFFIXES = (".yml", ".yaml")
_RULE_TEST_SUFFIXES = (".test.yml", ".test.yaml")


# --- Settings ---------------------------------------------------------------

def _check_local_pack_ref(value: str) -> str:
    if not value:
        raise ValueError("a rule pack path must not be empty")
    if value == "auto" or value.startswith(_REGISTRY_PREFIXES) or "://" in value:
        raise ValueError(f"rule pack {value!r} is a registry reference or URL; only local files and directories are allowed")
    if not os.path.exists(value):
        raise ValueError(f"rule pack {value!r} does not exist")
    return value


class RulePackRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    # Required for a pack inside the workspace (checked at probe).
    sha256: Optional[str] = None

    @field_validator("path")
    @classmethod
    def _local_path(cls, value: str) -> str:
        return _check_local_pack_ref(value)

    @field_validator("sha256")
    @classmethod
    def _hex_digest(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not _SHA256_RE.fullmatch(value):
            raise ValueError("rule pack sha256 must be 64 lowercase hex characters")
        return value


class SemgrepSettings(BaseModel):
    """``static_analysis.providers.semgrep``."""

    model_config = ConfigDict(extra="forbid")

    version: str
    executable: str = "semgrep"
    image: Optional[str] = None
    rule_packs: List[Union[str, RulePackRef]] = Field(min_length=1)
    min_language_maturity: Literal["ga", "beta", "experimental"] = "ga"
    per_file_timeout_seconds: int = Field(default=5, gt=0)
    timeout_threshold: int = Field(default=3, ge=0)

    @field_validator("version")
    @classmethod
    def _exact_version(cls, value: str) -> str:
        if not _VERSION_RE.fullmatch(value):
            raise ValueError(f"semgrep version must be exact x.y.z, got {value!r} (no 'latest', no ranges)")
        return value

    @field_validator("image")
    @classmethod
    def _pinned_image(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not _PINNED_IMAGE_RE.fullmatch(value):
            raise ValueError(f"semgrep image must be pinned by digest (<repository>@sha256:<64 hex>), got {value!r}")
        return value

    @field_validator("rule_packs")
    @classmethod
    def _local_packs(cls, value: List[Union[str, RulePackRef]]) -> List[Union[str, RulePackRef]]:
        for pack in value:
            if isinstance(pack, str):
                _check_local_pack_ref(pack)
        return value


# --- Rule packs -------------------------------------------------------------

class RulePackError(ValueError):
    """A rule pack Kriya cannot bind an identity to (a probe reason code)."""

    def __init__(self, reason_code: str, detail: str) -> None:
        super().__init__(detail)
        self.reason_code = reason_code
        self.detail = detail


@dataclass(frozen=True)
class _PackSpec:
    ref: str            # as configured
    path: str           # realpath
    sha256: Optional[str]


@dataclass(frozen=True)
class _LoadedPacks:
    identities: Tuple[RulePackIdentity, ...]
    # Every rule id across all packs (unique by construction).
    rule_ids: FrozenSet[str]
    # Kriya language id -> number of rules targeting it.
    rules_per_language: Mapping[str, int]


def _pack_specs(settings: SemgrepSettings) -> Tuple[_PackSpec, ...]:
    specs = []
    for pack in settings.rule_packs:
        ref, pin = (pack, None) if isinstance(pack, str) else (pack.path, pack.sha256)
        specs.append(_PackSpec(ref=ref, path=os.path.realpath(ref), sha256=pin))
    return tuple(specs)


def _is_rule_file(relpath: str) -> bool:
    # What Semgrep 1.178.0 loads from a directory config (observed): every
    # .yml/.yaml file at any depth, hidden directories and dotfiles
    # included, except *.test.yml/*.test.yaml (rule test fixtures).
    name = relpath.lower()
    return name.endswith(_RULE_FILE_SUFFIXES) and not name.endswith(_RULE_TEST_SUFFIXES)


def _read_pack(spec: _PackSpec) -> List[Tuple[str, bytes]]:
    """Every regular file of a pack, sorted by pack-relative posix path.

    The digest covers every file (a superset of what Semgrep loads, so no
    byte Semgrep reads escapes the identity). Raises OSError when the pack
    is unreadable and RulePackError when it has no rule file or contains a
    directory symlink (whose contents Semgrep might load unseen)."""
    if os.path.isfile(spec.path):
        with open(spec.path, "rb") as handle:
            return [(os.path.basename(spec.path), handle.read())]
    if not os.path.isdir(spec.path):
        raise FileNotFoundError(f"rule pack {spec.ref!r} does not exist")
    files: List[Tuple[str, bytes]] = []
    for dirpath, dirnames, filenames in os.walk(spec.path):
        for name in dirnames:
            if os.path.islink(os.path.join(dirpath, name)):
                raise RulePackError(RULE_PACK_INVALID, f"rule pack {spec.ref!r} contains a directory symlink: {name}")
        for name in filenames:
            full = os.path.join(dirpath, name)
            if not os.path.isfile(full):
                continue
            with open(full, "rb") as handle:
                files.append((os.path.relpath(full, spec.path).replace(os.sep, "/"), handle.read()))
    files.sort(key=lambda item: item[0])
    if not any(_is_rule_file(rel) for rel, _ in files):
        raise RulePackError(RULE_PACK_INVALID, f"rule pack {spec.ref!r} contains no .yml/.yaml rule file")
    return files


def _pack_digest(files: Sequence[Tuple[str, bytes]]) -> str:
    return canonical_digest([[rel, bytes_digest(data)] for rel, data in files])


def _parse_rules(spec: _PackSpec, files: Sequence[Tuple[str, bytes]]) -> List[Tuple[str, Tuple[str, ...]]]:
    """(rule id, Kriya languages) for every rule Semgrep loads from the pack."""
    single_file = os.path.isfile(spec.path)
    rules: List[Tuple[str, Tuple[str, ...]]] = []
    for rel, data in files:
        if not single_file and not _is_rule_file(rel):
            continue
        try:
            document = yaml.safe_load(data.decode("utf-8"))
        except (yaml.YAMLError, UnicodeDecodeError) as exc:
            raise RulePackError(RULE_PACK_INVALID, f"rule pack {spec.ref!r}: {rel} is not valid YAML: {exc}") from None
        entries = document.get("rules") if isinstance(document, dict) else None
        if not isinstance(entries, list):
            raise RulePackError(RULE_PACK_INVALID, f"rule pack {spec.ref!r}: {rel} has no 'rules:' list")
        for index, rule in enumerate(entries):
            rule_id = rule.get("id") if isinstance(rule, dict) else None
            if not isinstance(rule_id, str) or not rule_id:
                raise RulePackError(RULE_PACK_INVALID, f"rule pack {spec.ref!r}: {rel} rule #{index} has no id")
            declared = rule.get("languages")
            names = declared if isinstance(declared, list) else []
            languages = sorted({_LANGUAGE_ALIASES[n.lower()] for n in names
                                if isinstance(n, str) and n.lower() in _LANGUAGE_ALIASES})
            rules.append((rule_id, tuple(languages)))
    return rules


def _inside(path: str, root: str) -> bool:
    return path_relation(root, path) is PathRelation.WITHIN


def _load_packs(specs: Sequence[_PackSpec], workspace_root: str) -> _LoadedPacks:
    identities: List[RulePackIdentity] = []
    seen: Dict[str, str] = {}
    per_language: Dict[str, int] = {}
    for spec in specs:
        try:
            files = _read_pack(spec)
        except OSError as exc:
            raise RulePackError(RULE_PACK_INVALID, f"rule pack {spec.ref!r} is unreadable: {exc}") from None
        digest = _pack_digest(files)
        if spec.sha256 is not None and spec.sha256 != digest:
            raise RulePackError(
                RULE_PACK_DIGEST_MISMATCH,
                f"rule pack {spec.ref!r} digest {digest} does not match the configured pin {spec.sha256}",
            )
        if spec.sha256 is None and _inside(spec.path, workspace_root):
            raise RulePackError(
                RULE_PACK_UNPINNED,
                f"rule pack {spec.ref!r} is inside the workspace and has no sha256 pin (current digest {digest})",
            )
        rules = _parse_rules(spec, files)
        pack_languages: Set[str] = set()
        for rule_id, languages in rules:
            if rule_id in seen:
                raise RulePackError(
                    RULE_PACK_INVALID,
                    f"rule id {rule_id!r} is defined in both {seen[rule_id]!r} and {spec.ref!r}; "
                    "normalized rule ids must be unique",
                )
            seen[rule_id] = spec.ref
            pack_languages.update(languages)
            for language in languages:
                per_language[language] = per_language.get(language, 0) + 1
        identities.append(RulePackIdentity(
            ref=spec.ref, digest=digest, rule_count=len(rules), languages=tuple(sorted(pack_languages)),
        ))
    return _LoadedPacks(identities=tuple(identities), rule_ids=frozenset(seen), rules_per_language=per_language)


# --- Output normalization ---------------------------------------------------

def _bounded_text(value: Any) -> str:
    text = value if isinstance(value, str) else ""
    text = _CONTROL_CHARS.sub(" ", text).strip()
    return text[:_MESSAGE_LIMIT]


def _norm_path(path: str) -> str:
    return posixpath.normpath(path.replace(os.sep, "/"))


def _string_tuple(value: Any) -> Tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list):
        return tuple(item for item in value if isinstance(item, str))
    return ()


def map_severity(rule_severity: Any, metadata: Mapping[str, Any]) -> Severity:
    """The versioned (SEVERITY_MAP_VERSION) Semgrep -> Kriya severity map."""
    security = metadata.get("security-severity")
    if isinstance(security, str) and security.strip().lower() in _SECURITY_SEVERITY:
        return _SECURITY_SEVERITY[security.strip().lower()]
    impact = metadata.get("impact")
    if isinstance(impact, str) and impact.strip().upper() in _IMPACT:
        return _IMPACT[impact.strip().upper()]
    if isinstance(rule_severity, str) and rule_severity in _RULE_SEVERITY:
        return _RULE_SEVERITY[rule_severity]
    return Severity.UNKNOWN


def normalize_rule_id(check_id: str, rule_ids: FrozenSet[str]) -> str:
    """Semgrep prefixes a rule id with the dotted config path (observed:
    ``<dotted host path>.<id>`` on the host, ``kriya.rules.0.<id>`` in the
    container). The longest known id the check id ends with wins, since
    ids may contain dots; an unmatched check id is kept whole."""
    # Candidate suffixes after each dot, longest first: the first known one wins.
    candidates = [check_id] + [check_id[i + 1:] for i, char in enumerate(check_id) if char == "."]
    best = next((candidate for candidate in candidates if candidate in rule_ids), check_id)
    return f"{PROVIDER}:{best}"


class _MalformedOutput(ValueError):
    pass


def _parse_finding(index: int, raw: Any, rule_ids: FrozenSet[str], root: str) -> Finding:
    if not isinstance(raw, dict):
        raise _MalformedOutput(f"results[{index}] is not an object")
    check_id, path, start, end = raw.get("check_id"), raw.get("path"), raw.get("start"), raw.get("end")
    extra = raw.get("extra")
    if not (isinstance(check_id, str) and isinstance(path, str) and isinstance(start, dict)
            and isinstance(end, dict) and isinstance(extra, dict)):
        raise _MalformedOutput(f"results[{index}] lacks check_id/path/start/end/extra")
    try:
        start_line, end_line = int(start["line"]), int(end["line"])
        start_col, end_col = int(start.get("col", 0)), int(end.get("col", 0))
    except (KeyError, TypeError, ValueError):
        raise _MalformedOutput(f"results[{index}] has an invalid range") from None
    metadata = extra.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    if os.path.isabs(path):
        path = os.path.relpath(path, root)
    category = metadata.get("category")
    return Finding(
        provider=PROVIDER,
        rule_id=normalize_rule_id(check_id, rule_ids),
        severity=map_severity(extra.get("severity"), metadata),
        path=_norm_path(path),
        start_line=start_line, end_line=end_line, start_col=start_col, end_col=end_col,
        category=category if isinstance(category, str) else None,
        cwe=_string_tuple(metadata.get("cwe")),
        owasp=_string_tuple(metadata.get("owasp")),
        message=_bounded_text(extra.get("message")),
        raw_index=index,
    )


def _error_kind(raw: Mapping[str, Any]) -> Tuple[Optional[str], Any]:
    """``type`` is a string, or ``[kind, spans]`` (1.178.0 PartialParsing, V10)."""
    value = raw.get("type")
    if isinstance(value, str):
        return value, None
    if isinstance(value, list) and value and isinstance(value[0], str):
        return value[0], value[1] if len(value) > 1 else None
    return None, None


def _error_paths(raw: Mapping[str, Any], type_spans: Any) -> Set[str]:
    paths: Set[str] = set()
    if isinstance(raw.get("path"), str):
        paths.add(_norm_path(raw["path"]))
    for spans in (raw.get("spans"), type_spans):
        for span in spans if isinstance(spans, list) else ():
            if isinstance(span, dict):
                for key in ("path", "file"):
                    if isinstance(span.get(key), str):
                        paths.add(_norm_path(span[key]))
    return paths


def _is_config_error(kind: Optional[str], raw: Mapping[str, Any]) -> bool:
    if kind in _CONFIG_ERROR_TYPES:
        return True
    code = raw.get("code")
    return kind == "SemgrepError" and isinstance(code, int) and code in _CONFIG_SEMGREP_ERROR_CODES


def classify_errors(raw_errors: Sequence[Any], targets: FrozenSet[str]) -> Tuple[List[ScanError], bool]:
    """Each ``errors[]`` entry by its kind, never by the exit code (§5.3).

    Returns the normalized errors and whether every config error is an
    unknown language. A target-level error is one that names an intended
    target; an error naming only non-targets (a rule file, Semgrep's own
    temp file) and of no config kind is "other"."""
    errors: List[ScanError] = []
    unknown_language_only = True
    for index, raw in enumerate(raw_errors):
        if not isinstance(raw, dict):
            raise _MalformedOutput(f"errors[{index}] is not an object")
        kind, type_spans = _error_kind(raw)
        level = raw.get("level") if isinstance(raw.get("level"), str) else "error"
        message = _bounded_text(raw.get("message") or raw.get("long_msg") or raw.get("short_msg") or kind or "")
        if _is_config_error(kind, raw):
            unknown_language_only = unknown_language_only and kind == _UNKNOWN_LANGUAGE_TYPE
            errors.append(ScanError(kind="config", message=message, level=level))
            continue
        hit = sorted(_error_paths(raw, type_spans) & targets)
        if hit:
            errors.extend(ScanError(kind="target", message=message, path=path, level=level) for path in hit)
        else:
            errors.append(ScanError(kind="other", message=message, level=level))
    return errors, unknown_language_only


def _skipped(paths: Mapping[str, Any]) -> Dict[str, str]:
    raw = paths.get("skipped")
    if not isinstance(raw, list):
        raise _MalformedOutput("paths.skipped is missing (Semgrep omits it without --verbose)")
    skipped: Dict[str, str] = {}
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise _MalformedOutput(f"paths.skipped[{index}] has no path")
        reason = entry.get("reason")
        skipped[_norm_path(entry["path"])] = reason if isinstance(reason, str) else "skipped"
    return skipped


def interpret_output(
    stdout: str, returncode: int, targets: Sequence[str], rule_ids: FrozenSet[str], root: str,
) -> ScanResult:
    """Map one completed Semgrep run to a ScanResult (§5.3 table)."""
    raw_sha256 = bytes_digest(stdout.encode("utf-8"))
    base = {"raw_output": stdout, "raw_sha256": raw_sha256}
    try:
        data = json.loads(stdout)
        if not isinstance(data, dict) or not all(key in data for key in ("results", "paths", "errors")):
            raise _MalformedOutput("output lacks one of results/paths/errors")
        results, paths, raw_errors = data["results"], data["paths"], data["errors"]
        if not isinstance(results, list) or not isinstance(paths, dict) or not isinstance(raw_errors, list):
            raise _MalformedOutput("results/paths/errors have the wrong type")
        scanned = paths.get("scanned")
        if not isinstance(scanned, list) or not all(isinstance(p, str) for p in scanned):
            raise _MalformedOutput("paths.scanned is missing or invalid")
        # Findings are evidence whatever the exit code: exit 0 is not clean.
        findings = tuple(_parse_finding(i, raw, rule_ids, root) for i, raw in enumerate(results))
        skipped = _skipped(paths)
        intended = frozenset(_norm_path(t) for t in targets)
        errors, unknown_language_only = classify_errors(raw_errors, intended)
    except (json.JSONDecodeError, _MalformedOutput) as exc:
        return ScanResult(status=ScanStatus.MALFORMED_OUTPUT, reason_code=SCAN_OUTPUT_MALFORMED,
                          detail=f"Semgrep output is malformed: {exc}", **base)
    version = data.get("version")
    errored = {e.path for e in errors if e.kind == "target"}
    # Confirmed = scanned - skipped - errored (§6; a partially parsed file is
    # in all three in 1.178.0, V10).
    analyzed = frozenset(_norm_path(p) for p in scanned) - set(skipped) - errored
    common = dict(findings=findings, analyzed=analyzed, skipped=skipped, errors=tuple(errors),
                  reported_version=version if isinstance(version, str) else None, **base)
    config = [e for e in errors if e.kind == "config"]
    if config:
        reason = CAPABILITY_UNKNOWN if unknown_language_only else RULE_PACK_INVALID
        return ScanResult(status=ScanStatus.CONFIG_ERROR, reason_code=reason,
                          detail=f"Semgrep reported {len(config)} rule/config error(s) (exit {returncode}): "
                                 f"{config[0].message}", **common)
    other = [e for e in errors if e.kind == "other"]
    if other or returncode != 0:
        detail = other[0].message if other else "no classified error"
        return ScanResult(status=ScanStatus.FAILED, reason_code=SCAN_FAILED,
                          detail=f"Semgrep run failed (exit {returncode}): {detail}", **common)
    missing = sorted(intended - analyzed)
    if missing:
        return ScanResult(status=ScanStatus.INCOMPLETE, reason_code=SCAN_INCOMPLETE,
                          detail=f"{len(missing)} target(s) not confirmed analyzed: {', '.join(missing[:20])}",
                          **common)
    return ScanResult(status=ScanStatus.COMPLETE, **common)


# --- Adapter ----------------------------------------------------------------

def _file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class EngineNotLocatedError(ValueError):
    """The semgrep-core engine of a host installation cannot be located
    deterministically; its identity is never reduced to the entry point."""


def locate_engine(entry_point: str) -> str:
    """The semgrep-core binary the resolved entry point loads.

    The entry point must be a script whose shebang names an absolute
    interpreter; the engine is the single packaged binary under that
    interpreter's prefix. Anything else (``/usr/bin/env`` shebang, a native
    binary, no or several candidates) raises EngineNotLocatedError."""
    with open(entry_point, "rb") as handle:
        first = handle.readline(4096)
    words = first[2:].decode("utf-8", "replace").split() if first.startswith(b"#!") else []
    if not words or not os.path.isabs(words[0]) or os.path.basename(words[0]) == "env":
        raise EngineNotLocatedError(
            f"cannot locate semgrep-core for {entry_point!r}: no absolute interpreter in its shebang",
        )
    prefix = glob.escape(os.path.dirname(os.path.dirname(words[0])))
    matches = sorted({os.path.realpath(path) for pattern in _ENGINE_GLOBS
                      for path in glob.glob(os.path.join(prefix, pattern))})
    if len(matches) != 1 or not os.path.isfile(matches[0]):
        raise EngineNotLocatedError(
            f"cannot locate semgrep-core for {entry_point!r}: {len(matches)} packaged engine(s) under "
            f"{os.path.dirname(os.path.dirname(words[0]))!r}",
        )
    return matches[0]


def host_executable_digest(entry_point: str) -> str:
    """Identity of a host installation: the entry point AND its engine.

    Raises OSError or EngineNotLocatedError (a ValueError)."""
    return canonical_digest({
        "entry_point": _file_sha256(entry_point), "semgrep_core": _file_sha256(locate_engine(entry_point)),
    })


def _tail(text: str, limit: int = 500) -> str:
    return _bounded_text(text[-limit:])


class SemgrepAdapter:
    """StaticAnalysisPort for Semgrep CE (host process or pinned OCI image)."""

    name = PROVIDER

    def __init__(
        self, settings: SemgrepSettings, context: ExecutionContext,
        process_controller: Optional[ProcessController] = None,
    ) -> None:
        self._settings = settings
        self._context = context
        self._process = process_controller or ProcessController(max_output_chars=_MAX_OUTPUT_CHARS)
        self._specs = _pack_specs(settings)
        self._probe: Optional[ProviderProbe] = None
        self._packs: Optional[_LoadedPacks] = None
        self._executable: Optional[str] = None

    # -- fixed invocation ---------------------------------------------------

    @property
    def _contained(self) -> bool:
        return bool(self._context.contained)

    def _flags(self, max_target_bytes: Union[int, str]) -> List[str]:
        return [
            "scan", "--metrics=off", "--disable-version-check", "--disable-nosem", "--no-git-ignore",
            "--oss-only", "--json", "--verbose",
            "--timeout", str(self._settings.per_file_timeout_seconds),
            "--timeout-threshold", str(self._settings.timeout_threshold),
            "--max-target-bytes", str(max_target_bytes),
        ]

    @staticmethod
    def _host_env(home: str, tmpdir: str) -> Dict[str, str]:
        env = build_restricted_env([])
        env.update({"HOME": home, "TMPDIR": tmpdir, **_FIXED_ENV})
        return env

    @staticmethod
    def _host_scratch(base: str) -> Tuple[str, str]:
        """Create the per-invocation HOME and TMPDIR under a Kriya-owned directory."""
        home, tmpdir = os.path.join(base, "home"), os.path.join(base, "tmp")
        os.makedirs(home, exist_ok=True)
        os.makedirs(tmpdir, exist_ok=True)
        return home, tmpdir

    @staticmethod
    def _container_env() -> Dict[str, str]:
        return {"HOME": _CONTAINER_HOME, "TMPDIR": _CONTAINER_TMPDIR, **_FIXED_ENV, "PATH": _CONTAINER_PATH}

    def _profile(self, workspace: str, mounts: Tuple[MountSpec, ...]) -> ContainmentProfile:
        return ContainmentProfile(
            trust_class=TrustClass.UNTRUSTED_EXECUTION,
            workspace_path=workspace,
            mount_workspace=True,
            workspace_write=False,
            additional_mounts=mounts,
            network=NetworkAuthority.DENIED,
            resolved_env=self._container_env(),
            run_as_uid=_CONTAINER_UID,
            run_as_gid=_CONTAINER_GID,
            image_reference=self._settings.image,
        )

    def _rule_mounts(self) -> Tuple[Tuple[MountSpec, ...], List[str]]:
        mounts, configs = [], []
        for index, spec in enumerate(self._specs):
            if os.path.isfile(spec.path):
                # A file keeps its name (and so its .yml suffix) in the container.
                container = f"{_CONTAINER_RULES}/{index}/{os.path.basename(spec.path)}"
            else:
                container = f"{_CONTAINER_RULES}/{index}"
            mounts.append(MountSpec(host_path=spec.path, container_path=container, writable=False))
            configs.append(container)
        return tuple(mounts), configs

    def _effective_options_digest(self) -> str:
        """The adapter-owned invocation minus targets and rule-pack paths."""
        options: Dict[str, Any] = {
            "argv": self._flags("<max_target_bytes>") + ["--config", "<rule pack>", "--", "<targets>"],
            "env_keys": sorted(self._container_env() if self._contained else self._host_env("<home>", "<tmp>")),
            "per_file_timeout_seconds": self._settings.per_file_timeout_seconds,
            "timeout_threshold": self._settings.timeout_threshold,
        }
        if self._contained:
            options["container"] = {
                "network": NetworkAuthority.DENIED.value, "workspace": "ro", "rules": "ro",
                "user": f"{_CONTAINER_UID}:{_CONTAINER_GID}",
            }
        return canonical_digest(options)

    def _containment_ready(self) -> Optional[str]:
        """None when contained execution can be attempted, else why not."""
        backend = self._context.containment_backend
        if self._settings.image is None:
            return "containment is required but providers.semgrep.image is not set (no host fallback)"
        if backend is None or getattr(backend, "name", None) == "none":
            return "containment is required but no containment backend is configured (no host fallback)"
        return None

    def _resolve_executable(self) -> Optional[str]:
        found = shutil.which(self._settings.executable, path=build_restricted_env([])["PATH"])
        return os.path.realpath(found) if found else None

    # -- port ---------------------------------------------------------------

    def runtime_fingerprint(self) -> str:
        """What would run, recomputed from disk without running Semgrep.

        Raises OSError/ValueError on an unreadable executable or rule pack:
        the commit guard treats a raise as stale evidence."""
        if self._contained:
            if self._settings.image is None:
                raise ValueError("contained execution without a pinned image")
            location, executable = "container", self._settings.image
        else:
            resolved = self._resolve_executable()
            if resolved is None:
                raise FileNotFoundError(f"executable {self._settings.executable!r} not found on PATH")
            location, executable = "local_process", host_executable_digest(resolved)
        return canonical_digest({
            "execution_location": location,
            "executable": executable,
            "rule_packs": [[spec.ref, _pack_digest(_read_pack(spec))] for spec in self._specs],
            "effective_options_digest": self._effective_options_digest(),
        })

    def probe(self) -> ProviderProbe:
        if self._probe is None:
            self._probe = self._run_probe()
        return self._probe

    def _unavailable(self, reason_code: str, detail: str) -> ProviderProbe:
        return ProviderProbe(identity=None, capability=None, reason_code=reason_code, detail=detail)

    def _run_probe(self) -> ProviderProbe:
        with tempfile.TemporaryDirectory(prefix="kriya-semgrep-probe-") as scratch:
            if self._contained:
                refusal = self._containment_ready()
                if refusal is not None:
                    return self._unavailable(CONTAINMENT_UNAVAILABLE, refusal)
                digest_match = _PINNED_IMAGE_RE.fullmatch(self._settings.image or "")
                executable_digest = digest_match.group(1) if digest_match else ""
                # The empty scratch directory is mounted read-only as the
                # workspace: without a workspace mount the OCI backend makes
                # the /kriya/tmp tmpfs the working directory, which leaves it
                # root-owned 0755 and HOME unwritable for uid 65534 (observed).
                try:
                    result = self._process.run(
                        [_CONTAINER_EXECUTABLE, "--version"], cwd=scratch, timeout=_PROBE_TIMEOUT_SECONDS,
                        containment_profile=self._profile(scratch, ()),
                        containment_backend=self._context.containment_backend,
                    )
                except ContainmentSetupError as exc:
                    return self._unavailable(CONTAINMENT_UNAVAILABLE, f"containment could not be prepared: {exc}")
                location, network_enforced = "container", True
            else:
                executable = self._resolve_executable()
                if executable is None:
                    return self._unavailable(
                        PROVIDER_PROBE_FAILED, f"executable {self._settings.executable!r} not found on PATH",
                    )
                try:
                    executable_digest = host_executable_digest(executable)
                except (OSError, EngineNotLocatedError) as exc:
                    return self._unavailable(PROVIDER_PROBE_FAILED, f"executable identity unavailable: {exc}")
                try:
                    result = self._process.run(
                        [executable, "--version"], cwd=scratch, timeout=_PROBE_TIMEOUT_SECONDS,
                        env=self._host_env(*self._host_scratch(scratch)),
                    )
                except OSError as exc:
                    return self._unavailable(PROVIDER_PROBE_FAILED, f"executable {executable!r} failed to run: {exc}")
                self._executable = executable
                location, network_enforced = "local_process", False
        return self._probe_from_version(result, executable_digest, location, network_enforced)

    def _probe_from_version(
        self, result: ProcessResult, executable_digest: str, location: str, network_enforced: bool,
    ) -> ProviderProbe:
        if result.timeout or result.returncode != 0:
            return self._unavailable(
                PROVIDER_PROBE_FAILED,
                f"'--version' failed (exit {result.returncode}, timeout={result.timeout}): {_tail(result.stderr)}",
            )
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        version = lines[-1] if lines else ""
        if version != self._settings.version:
            return self._unavailable(
                PROVIDER_VERSION_MISMATCH,
                f"configured version {self._settings.version} but the executable reports {version or '<nothing>'}",
            )
        table = _LANGUAGE_TABLE.get(version)
        if table is None:
            return self._unavailable(CAPABILITY_UNKNOWN, f"no language table for version {version}")
        try:
            packs = self._loaded_packs()
        except RulePackError as exc:
            return self._unavailable(exc.reason_code, exc.detail)
        limitations = [LIMITATION_SILENT_SYNTAX_RECOVERY, LIMITATION_FILE_LOCAL]
        if not self._contained:
            limitations.append(LIMITATION_HOST_NETWORK)
        capability = ProviderCapability(
            languages={lang: LanguageSupport(maturity=maturity, rules_available=packs.rules_per_language.get(lang, 0))
                       for lang, maturity in table.items()},
            analysis_scope="file_local",
            supported_scopes=frozenset({ScanScope.CHANGED_FILES, ScanScope.MODULE, ScanScope.REPOSITORY}),
            minimum_scope=ScanScope.CHANGED_FILES,
            network_requirement="none",
            source_upload=False,
            prerequisites={},
            control_files=CONTROL_FILES,
            limitations=tuple(limitations),
        )
        identity = ProviderIdentity(
            provider=PROVIDER, version=version, edition=EDITION, executable_digest=executable_digest,
            execution_location=location, network_enforced=network_enforced, rule_packs=packs.identities,
            effective_options_digest=self._effective_options_digest(), severity_map_version=SEVERITY_MAP_VERSION,
        )
        return ProviderProbe(identity=identity, capability=capability)

    def _loaded_packs(self) -> _LoadedPacks:
        if self._packs is None:
            self._packs = _load_packs(self._specs, self._context.workspace_root)
        return self._packs

    def check_prerequisites(
        self, capability: ProviderCapability, languages: Sequence[str], workspace: str,
    ) -> Sequence[PrerequisiteResult]:
        # Semgrep CE needs no build: no language declares a prerequisite.
        return ()

    def build_graph_roots(self, workspace: str, changed_paths: Sequence[str]) -> Sequence[str]:
        # BUILD_GRAPH is not a supported scope; the service never asks.
        return ()

    def scan(self, request: ScanRequest) -> ScanResult:
        started = time.monotonic()

        def elapsed() -> int:
            return int((time.monotonic() - started) * 1000)

        for target in request.targets:
            norm = _norm_path(target)
            if os.path.isabs(target) or norm == ".." or norm.startswith("../") \
                    or not os.path.isfile(os.path.join(request.root, target)):
                # One missing target aborts the whole Semgrep run (V15).
                return ScanResult(status=ScanStatus.FAILED, reason_code=SCAN_FAILED, duration_ms=elapsed(),
                                  detail=f"target {target!r} is not a file under the snapshot root; not scanned")
        refusal = self._containment_ready() if self._contained else None
        if refusal is not None:
            return ScanResult(status=ScanStatus.FAILED, reason_code=CONTAINMENT_UNAVAILABLE, detail=refusal,
                              duration_ms=elapsed())
        try:
            rule_ids = self._loaded_packs().rule_ids
        except RulePackError as exc:
            return ScanResult(status=ScanStatus.CONFIG_ERROR, reason_code=exc.reason_code, detail=exc.detail,
                              duration_ms=elapsed())
        try:
            if self._contained:
                mounts, configs = self._rule_mounts()
                command = [_CONTAINER_EXECUTABLE, *self._flags(request.max_target_bytes)]
                for config in configs:
                    command += ["--config", config]
                command += ["--", *request.targets]
                result = self._process.run(
                    command, cwd=request.root, timeout=request.timeout_seconds,
                    containment_profile=self._profile(request.root, mounts),
                    containment_backend=self._context.containment_backend,
                )
            else:
                executable = self._executable or self._resolve_executable()
                if executable is None:
                    return ScanResult(status=ScanStatus.FAILED, reason_code=SCAN_FAILED, duration_ms=elapsed(),
                                      detail=f"executable {self._settings.executable!r} not found on PATH")
                home, tmpdir = self._host_scratch(request.scratch_dir)
                command = [executable, *self._flags(request.max_target_bytes)]
                for spec in self._specs:
                    command += ["--config", spec.path]
                command += ["--", *request.targets]
                result = self._process.run(command, cwd=request.root, timeout=request.timeout_seconds,
                                           env=self._host_env(home, tmpdir))
        except ContainmentSetupError as exc:
            return ScanResult(status=ScanStatus.FAILED, reason_code=CONTAINMENT_UNAVAILABLE, duration_ms=elapsed(),
                              detail=f"containment could not be prepared (no host fallback): {exc}")
        except OSError as exc:
            return ScanResult(status=ScanStatus.FAILED, reason_code=SCAN_FAILED, duration_ms=elapsed(),
                              detail=f"Semgrep could not be started: {exc}")
        raw = {"raw_output": result.stdout, "raw_sha256": bytes_digest(result.stdout.encode("utf-8"))}
        if result.timeout:
            return ScanResult(status=ScanStatus.TIMEOUT, reason_code=SCAN_TIMEOUT, duration_ms=elapsed(),
                              detail=f"Semgrep exceeded the {request.timeout_seconds}s scan timeout", **raw)
        if result.stdout_truncated:
            return ScanResult(status=ScanStatus.MALFORMED_OUTPUT, reason_code=SCAN_OUTPUT_MALFORMED,
                              duration_ms=elapsed(), detail="Semgrep output exceeded the capture limit", **raw)
        interpreted = interpret_output(result.stdout, result.returncode, request.targets, rule_ids, request.root)
        return replace(interpreted, duration_ms=elapsed())


def factory(settings: dict, context: ExecutionContext) -> SemgrepAdapter:
    return SemgrepAdapter(SemgrepSettings.model_validate(settings), context)


register_provider(PROVIDER, factory, SemgrepSettings)
