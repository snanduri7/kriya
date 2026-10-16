"""The CONFIG resolution contract (full modularization, slice D3; MODULE-CONTRACTS §3): what a configuration resolves to, where every
Kriya home is, and what the KUP configuration operations answer must be byte for byte what they were before CONFIG got its one
environment reader (``kriya/config/environment.py``), its resolution module and ``ConfigService``.

The three goldens in ``tests/golden/config/`` were WRITTEN FROM THE PRE-D3 CODE (aec8e4e), generated three times and compared identical
before any D3 production edit. Each covers a MATRIX, not one happy path:

* ``resolution_forms.json``: ``resolve_config_state`` and ``load_config`` for every layer/env combination that changes their outcome
  (packaged default, discovered, explicit and symlinked ``--config``; ``paths.state`` relative/``~``/inside-but-not-under-``.kriya``/
  outside; ``logging.directory`` relative/``~``; the removed fields; a protected field unapproved, approved, and under a trust file that is
  absolute-outside, inside the workspace and relative; the production preset contradiction; malformed YAML; an unregistered static-analysis
  provider; an escaping MCP capability path) - the violations in clear, the effective dictionaries and the constructed configuration as
  digests of their normalized canonical JSON, every error as its class and message;
* ``environment_homes.json``: every home and environment-derived value (state, logs, authority, MCP approvals, waivers, qualifications,
  certifications, adjudications, the model-runtime fingerprint store, the acceptance/validator state roots, both probe switches, the git
  probe PATH/HOME, the OCI image overrides read at import, path expansion in policy and the kernel) with the override unset, empty,
  ``~``-relative, relative and absolute;
* ``kup_config_forms.json``: the KUP operations that resolve a folder's configuration through the real ``_load_in``/``_resolve_locked`` (since D3: ``ConfigService.resolve``)
  (no monkeypatch): a malformed folder, a refused folder, a trust file in effect, inside the workspace and relative.

Normalization replaces only values that differ between two runs of the SAME code (temporary paths, the workspace identities derived from
them, the install directory, the per-process review fingerprint salt). Regenerate only from a commit whose behaviour is known good, with
``KRIYA_WRITE_CONFIG_GOLDEN=1``.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

import yaml

import kriya
from kriya.config import AppConfig

GOLDEN = Path(__file__).resolve().parent.parent / "golden" / "config"
WRITE = os.environ.get("KRIYA_WRITE_CONFIG_GOLDEN") == "1"
REPO = Path(__file__).resolve().parent.parent.parent
INSTALL = str(Path(kriya.__file__).resolve().parent.parent)

# Every variable a Kriya home or switch is read from (CURRENT-DEPENDENCY-MAP §5): unset for each case, then set by the case.
KRIYA_VARIABLES = ("KRIYA_STATE_DIR", "KRIYA_LOG_DIR", "KRIYA_AUTHORITY_HOME", "KRIYA_TRUST_FILE", "KRIYA_MCP_APPROVAL_HOME",
                   "KRIYA_STATIC_ANALYSIS_HOME", "KRIYA_QUALIFICATION_HOME", "KRIYA_CERTIFICATION_HOME", "KRIYA_ADJUDICATION_HOME",
                   "KRIYA_MODEL_RUNTIME_PROBE", "KRIYA_EXECUTION_ENVIRONMENT_PROBE", "GRADLE_USER_HOME")
_SHA = re.compile(r"sha256:[0-9a-f]{64}")


class World:
    """One case's filesystem: a fake HOME, a workspace (the working directory) and an outside directory, all under ``tmp``."""

    def __init__(self, tmp: Path, monkeypatch) -> None:
        self.tmp = tmp
        self.home = tmp / "home"
        self.ws = tmp / "ws"
        self.outside = tmp / "outside"
        for d in (self.home, self.ws, self.outside):
            d.mkdir(parents=True, exist_ok=True)
        self.monkeypatch = monkeypatch
        monkeypatch.setenv("HOME", str(self.home))
        for name in KRIYA_VARIABLES:
            monkeypatch.delenv(name, raising=False)
        monkeypatch.chdir(self.ws)
        self.roots: List[Tuple[Path, str]] = [(self.ws, "<WS>"), (self.outside, "<OUTSIDE>")]
        self._tokens: List[Tuple[str, str]] = []

    def tokens(self) -> List[Tuple[str, str]]:
        """Computed once per case (canonical spelling lists directories)."""
        if not self._tokens:
            from kriya.context.workspace_identity import workspace_identity

            pairs = []
            for root, token in self.roots:
                for spelled in sorted({str(root), os.path.realpath(str(root))}):
                    pairs.append((workspace_identity(spelled), token + "_ID"))
                    # the validator's dependency-cache key: sha256(realpath(workspace))[:16]
                    pairs.append((hashlib.sha256(spelled.encode("utf-8")).hexdigest()[:16], token + "_KEY"))
            for real, token in ((os.path.realpath(str(self.tmp)), "<TMP>"), (str(self.tmp), "<TMP>"), (INSTALL, "<INSTALL>")):
                pairs.append((real, token))
            self._tokens = pairs
        return self._tokens


VOLATILE_KEYS = frozenset({"observed_at"})  # wall-clock time of the envelope (MEASURED: the only timestamp key)
# A message that is cut at a fixed length (``str(error)[:300]``) can end INSIDE a temporary path, whose length varies between runs:
# from the first remaining temporary-directory prefix to the end of the string is replaced by one token.
_TMP_TAIL = re.compile("(" + re.escape(os.path.realpath(tempfile.gettempdir())) + "|" + re.escape(tempfile.gettempdir()) + ").*$",
                       re.DOTALL)


def normalize(value: Any, world: World) -> Any:
    if isinstance(value, dict):
        return {str(k): ("<volatile>" if k in VOLATILE_KEYS and v is not None else normalize(v, world)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalize(v, world) for v in value]
    if isinstance(value, str):
        for real, token in world.tokens():
            value = value.replace(real, token)
        return _TMP_TAIL.sub("<TRUNCATED_TMP_PATH>", _SHA.sub("sha256:<DIGEST>", value))
    return value


def _digest(value: Any, world: World) -> str:
    text = json.dumps(normalize(value, world), sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _error(error: BaseException, world: World) -> Dict[str, Any]:
    return {"error": type(error).__name__, "message": normalize(str(error), world)}


def _violations(violations) -> List[Dict[str, str]]:
    return [{"field": v.field_path, "classification": v.classification.value, "source": v.source.value, "reason": v.reason}
            for v in sorted(violations, key=lambda x: x.field_path)]


# ------------------------------------------------------------------ resolution forms

def _write(path: Path, data: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else yaml.safe_dump(data), encoding="utf-8")
    return str(path)


PROTECTED = {"llm": {"base_url": "http://operator:pass@127.0.0.1:11434/v1"}}


def _resolution_cases() -> List[Tuple[str, Callable[[World], Dict[str, Any]]]]:
    """(name, setup(world) -> {"config_path"?, "trust_file"?, "approve"?}): each case writes its files and environment."""

    def none(w):
        return {}

    def discovered(w):
        _write(w.ws / "kriya.yaml", {"llm": {"model": "m-discovered"}})
        return {}

    def discovered_yml(w):
        _write(w.ws / "kriya.yml", {"llm": {"model": "m-yml"}})
        return {}

    def explicit_outside(w):
        return {"config_path": _write(w.outside / "conf" / "kriya.yaml", {"llm": {"model": "m-explicit"}, "paths": {"skills": "./skills"}})}

    def explicit_symlink(w):
        target = _write(w.outside / "real" / "kriya.yaml", {"llm": {"model": "m-link"}})
        link = w.ws / "linked.yaml"
        os.symlink(target, link)
        return {"config_path": str(link)}

    def explicit_missing(w):
        return {"config_path": str(w.outside / "absent.yaml")}

    def state_relative(w):
        _write(w.ws / "kriya.yaml", {"paths": {"state": "state"}})
        return {}

    def state_under_kriya(w):
        _write(w.ws / "kriya.yaml", {"paths": {"state": ".kriya/state"}})
        return {}

    def state_is_kriya_dir(w):
        _write(w.ws / "kriya.yaml", {"paths": {"state": ".kriya"}})
        return {}

    def state_tilde(w):
        _write(w.ws / "kriya.yaml", {"paths": {"state": "~/kstate"}})
        return {}

    def state_outside(w):
        _write(w.ws / "kriya.yaml", {"paths": {"state": str(w.outside / "state")}})
        return {}

    def state_null(w):
        _write(w.ws / "kriya.yaml", {"paths": {"state": None}})
        return {}

    def log_relative(w):
        _write(w.ws / "kriya.yaml", {"logging": {"directory": "logs"}})
        return {}

    def log_tilde(w):
        _write(w.ws / "kriya.yaml", {"logging": {"directory": "~/klogs"}})
        return {}

    def log_null(w):
        _write(w.ws / "kriya.yaml", {"logging": {"directory": None}})
        return {}

    def removed_paths_logs(w):
        _write(w.ws / "kriya.yaml", {"paths": {"logs": "./logs"}})
        return {}

    def removed_logging_file(w):
        _write(w.ws / "kriya.yaml", {"logging": {"file": "kriya.log"}})
        return {}

    def protected_unapproved(w):
        _write(w.ws / "kriya.yaml", PROTECTED)
        return {}

    def protected_approved(w):
        w.monkeypatch.setenv("KRIYA_AUTHORITY_HOME", str(w.outside / "authority"))
        _write(w.ws / "kriya.yaml", PROTECTED)
        return {"approve": True}

    def protected_trust_absolute(w):
        _write(w.ws / "kriya.yaml", PROTECTED)
        w.monkeypatch.setenv("KRIYA_TRUST_FILE", str(w.outside / "trust.json"))
        return {}

    def protected_trust_inside(w):
        _write(w.ws / "kriya.yaml", PROTECTED)
        w.monkeypatch.setenv("KRIYA_TRUST_FILE", str(w.ws / "trust.json"))
        return {}

    def protected_trust_relative(w):
        _write(w.ws / "kriya.yaml", PROTECTED)
        w.monkeypatch.setenv("KRIYA_TRUST_FILE", "trust.json")
        return {}

    def protected_trust_empty(w):
        _write(w.ws / "kriya.yaml", PROTECTED)
        w.monkeypatch.setenv("KRIYA_TRUST_FILE", "")
        return {}

    def protected_trust_argument(w):
        _write(w.ws / "kriya.yaml", PROTECTED)
        w.monkeypatch.setenv("KRIYA_TRUST_FILE", str(w.ws / "trust.json"))  # the explicit argument wins over the environment
        return {"trust_file": str(w.outside / "explicit-trust.json")}

    def production_contradiction(w):
        _write(w.ws / "kriya.yaml", {"runtime_profile": "production", "knowledge": {"offline_mode": False}})
        return {}

    def preset_conflict(w):
        _write(w.ws / "kriya.yaml", {"runtime_profile": "validated", "engineering_triage": {"enabled": True}})
        return {}

    def malformed(w):
        _write(w.ws / "kriya.yaml", "llm: [unclosed\n")
        return {}

    def static_unregistered(w):
        _write(w.ws / "kriya.yaml", {"static_analysis": {"enabled": True, "provider": "nope"}})
        w.monkeypatch.setenv("KRIYA_AUTHORITY_HOME", str(w.outside / "authority"))
        return {"approve": True}

    def mcp_escape(w):
        _write(w.ws / "kriya.yaml", {"mcp": {"srv": {"command": "x", "capabilities": {"additional_read_paths": ["../escape"]}}}})
        return {}

    def mcp_paths(w):
        _write(w.ws / "kriya.yaml", {"mcp": {"srv": {"command": "x", "capabilities": {"additional_read_paths": ["inner", "/abs/path"]}}}})
        return {}

    def static_paths(w):
        _write(w.ws / "kriya.yaml", {"static_analysis": {"waivers": {"store": "~/waivers.json"},
                                                         "providers": {"semgrep": {"rule_packs": ["rules", {"path": "packs/p"}]}}}})
        return {}

    def routing_relative(w):
        _write(w.ws / "kriya.yaml", {"model_policy": {"routing": {"table_path": "rel/table.json"}}})
        return {}

    def routing_tilde(w):
        _write(w.ws / "kriya.yaml", {"model_policy": {"routing": {"table_path": "~/table.json"}}})
        return {}

    return [(f.__name__, f) for f in (
        none, discovered, discovered_yml, explicit_outside, explicit_symlink, explicit_missing, state_relative, state_under_kriya,
        state_is_kriya_dir, state_tilde, state_outside, state_null, log_relative, log_tilde, log_null, removed_paths_logs,
        removed_logging_file, protected_unapproved, protected_approved, protected_trust_absolute, protected_trust_inside,
        protected_trust_relative, protected_trust_empty, protected_trust_argument, production_contradiction, preset_conflict, malformed,
        static_unregistered, mcp_escape, mcp_paths, static_paths, routing_relative, routing_tilde)]


def resolution_form(name: str, setup, tmp: Path, monkeypatch) -> Dict[str, Any]:
    from kriya.config.config import load_config, resolve_config_state

    world = World(tmp / name, monkeypatch)
    spec = setup(world)
    form: Dict[str, Any] = {}
    try:
        state = resolve_config_state(spec.get("config_path"))
    except Exception as error:  # noqa: BLE001 - the error IS the recorded outcome
        form["resolve"] = _error(error, world)
    else:
        form["resolve"] = {"config_source": state.config_source, "config_path": normalize(state.config_path, world),
                           "workspace_root": normalize(state.workspace_root, world), "violations": _violations(state.violations),
                           "config_dict": _digest(state.config_dict, world),
                           "user_sections": normalize({k: state.config_dict.get(k) for k in ("paths", "logging", "mcp", "static_analysis")},
                                                      world)}
        if spec.get("approve"):
            from kriya.config.authority_approval import grant_local_approval

            grant_local_approval(state.violations, state.config_dict, state.workspace_root)
    try:
        cfg = load_config(spec.get("config_path"), trust_file=spec.get("trust_file"))
    except Exception as error:  # noqa: BLE001 - the error IS the recorded outcome
        load: Dict[str, Any] = _error(error, world)
        violations = getattr(error, "violations", None)
        if violations is not None:
            load["violations"] = _violations(violations)
        form["load"] = load
    else:
        form["load"] = {"app": _digest(cfg.model_dump(mode="json"), world), "sensitive_paths": list(cfg.autonomy.sensitive_paths),
                        "paths": normalize(cfg.paths.model_dump(mode="json"), world),
                        "logging_directory": normalize(cfg.logging.directory, world)}
    return form


# ------------------------------------------------------------------ environment homes

OVERRIDES = (("unset", None), ("empty", ""), ("tilde", "~/override"), ("relative", "rel/override"), ("absolute", "<OUTSIDE>/override"))


def _value(world: World, raw):
    return None if raw is None else raw.replace("<OUTSIDE>", str(world.outside))


def _call(world: World, fn: Callable[[], Any]) -> Any:
    try:
        return normalize(fn(), world)
    except Exception as error:  # noqa: BLE001 - the error IS the recorded outcome
        return _error(error, world)


def _homes(world: World) -> Dict[str, Any]:
    from kriya.config import authority_approval
    from kriya.core import logging_setup, model_certification, model_qualification, model_runtime, state_paths
    from kriya.mcp import invocation_approval
    from kriya.metrics import adjudication
    from kriya.static_analysis import waivers
    from kriya.workflow import acceptance_jvm

    return {
        "authority_home": _call(world, authority_approval._authority_home_dir),
        "authority_local_path": _call(world, lambda: authority_approval.default_local_approval_path(str(world.ws))),
        "mcp_approval_home": _call(world, invocation_approval._approval_home_dir),
        "waiver_store_default": _call(world, lambda: waivers.waiver_store_path(None, str(world.ws))),
        "waiver_store_configured_tilde": _call(world, lambda: waivers.waiver_store_path("~/w.json", str(world.ws))),
        "qualification_home": _call(world, model_qualification.qualification_home),
        "certification_home": _call(world, model_certification.certification_home),
        "adjudication_store": _call(world, adjudication.store_path),
        "default_state_directory": _call(world, state_paths.default_state_directory),
        "resolve_state_directory": _call(world, lambda: state_paths.resolve_state_directory(AppConfig())),
        "trace_db_path": _call(world, lambda: state_paths.trace_db_path(AppConfig())),
        "default_log_directory": _call(world, logging_setup.default_log_directory),
        "resolve_log_directory": _call(world, lambda: logging_setup.resolve_log_directory(AppConfig())),
        "model_runtime_fingerprint_store": _call(world, lambda: model_runtime.fingerprint_store_dir(None)),
        "acceptance_jvm_state_root": _call(world, acceptance_jvm._state_root),
        "validator_maven_cache": _call(world, lambda: _validator(world)._maven_cache_dir()),
        "validator_dependency_cache": _call(world, lambda: _validator(world)._dependency_cache_dir("gradle")),
    }


def _validator(world: World):
    from kriya.tools.validate import PolymorphicValidator

    validator = object.__new__(PolymorphicValidator)
    validator.original_workspace_path = str(world.ws)
    validator.workspace_path = str(world.ws)
    return validator


HOME_VARIABLES = ("KRIYA_STATE_DIR", "KRIYA_LOG_DIR", "KRIYA_AUTHORITY_HOME", "KRIYA_MCP_APPROVAL_HOME", "KRIYA_STATIC_ANALYSIS_HOME",
                  "KRIYA_QUALIFICATION_HOME", "KRIYA_CERTIFICATION_HOME", "KRIYA_ADJUDICATION_HOME")
SWITCH_VALUES = (("unset", None), ("empty", ""), ("zero", "0"), ("false_padded", " False "), ("off", "off"), ("no", "no"), ("one", "1"),
                 ("yes", "yes"))


def environment_homes(tmp: Path, monkeypatch) -> Dict[str, Any]:
    from kriya.core import execution_environment, model_runtime
    from kriya.policy import execution as policy_execution
    from kriya.policy import filesystem as policy_filesystem
    from kriya.tools import git_metadata
    from kriya.workflow import workflow as workflow_module

    forms: Dict[str, Any] = {}
    for variable in HOME_VARIABLES:
        for label, raw in OVERRIDES:
            world = World(tmp / f"{variable}-{label}", monkeypatch)
            if raw is not None:
                monkeypatch.setenv(variable, _value(world, raw))
            forms[f"{variable}={label}"] = _homes(world)
    for variable, fn in (("KRIYA_MODEL_RUNTIME_PROBE", model_runtime.probing_enabled),
                         ("KRIYA_EXECUTION_ENVIRONMENT_PROBE", execution_environment.probing_enabled)):
        for label, raw in SWITCH_VALUES:
            world = World(tmp / f"{variable}-{label}", monkeypatch)
            if raw is not None:
                monkeypatch.setenv(variable, raw)
            forms[f"{variable}={label}"] = fn()
    for label, raw in (("unset", None), ("empty", ""), ("two", "<OUTSIDE>/bin1" + os.pathsep + "<OUTSIDE>/bin2")):
        world = World(tmp / f"PATH-{label}", monkeypatch)
        (world.outside / "bin2").mkdir()
        git = world.outside / "bin2" / "git"
        git.write_text("#!/bin/sh\n")
        git.chmod(0o755)
        if raw is None:
            monkeypatch.delenv("PATH", raising=False)
        else:
            monkeypatch.setenv("PATH", _value(world, raw))
        monkeypatch.delenv("HOME", raising=False) if label == "unset" else None
        forms[f"PATH={label}"] = {"probe_env": normalize(git_metadata._probe_env(), world),
                                  "git_candidates": normalize(git_metadata._git_candidates(), world)}
    for label, raw in (("unset", None), ("empty", ""), ("absolute", "<OUTSIDE>/gradle-home")):
        world = World(tmp / f"GRADLE_USER_HOME-{label}", monkeypatch)
        if raw is not None:
            monkeypatch.setenv("GRADLE_USER_HOME", _value(world, raw))
        forms[f"GRADLE_USER_HOME={label}"] = _gradle_seed(world)
    world = World(tmp / "expansion", monkeypatch)
    forms["expansion"] = {
        "policy_filesystem_canonical": normalize(policy_filesystem._canonical("~/a/../b"), world),
        "policy_execution_normalize": normalize(policy_execution._normalize_path("~/a/b"), world),
        "kernel_protected_relpath": normalize(workflow_module._resolve_protected_relpath(str(world.ws), "~/x.py"), world),
        "kernel_protected_relpath_inside": normalize(workflow_module._resolve_protected_relpath(str(world.ws), str(world.ws / "a.py")),
                                                     world),
        "kernel_protected_relpath_tilde_workspace": normalize(workflow_module._resolve_protected_relpath("~/../ws", "~/../ws/b.py"), world),
        "leak_check_read_texts": _leak_check_read(world),
    }
    forms["oci_images"] = _oci_images(tmp)
    return forms


def _gradle_seed(world: World) -> Any:
    import inspect

    from kriya.tools.dependency_execution import seed_gradle_distribution_from_host

    params = list(inspect.signature(seed_gradle_distribution_from_host).parameters)
    distribution = _FakeDistribution()
    kwargs = {"host_gradle_home": None} if "host_gradle_home" in params else {}
    try:
        record = seed_gradle_distribution_from_host(distribution, str(world.tmp / "cache"), **kwargs)
    except Exception as error:  # noqa: BLE001 - the error IS the recorded outcome
        return _error(error, world)
    return normalize(record, world)


class _FakeDistribution:
    url = "https://services.gradle.org/distributions/gradle-8.5-bin.zip"
    name = "gradle-8.5-bin"
    url_hash = "abc123"
    sha256 = None
    version = "8.5"


def _leak_check_read(world: World) -> Any:
    from kriya.core.attempt_evidence.leak_check import read_texts

    (world.home / "leak.txt").write_text("hidden text", encoding="utf-8")
    return read_texts(["~/leak.txt"])


def _oci_images(tmp: Path) -> Dict[str, Any]:
    """The four image overrides are read once, at import of ``kriya.tools.containment_oci``: measured in fresh interpreters."""
    code = ("import json, kriya.tools.containment_oci as c; "
            "print(json.dumps([c._DEFAULT_IMAGE, c._MAVEN_IMAGE, c._GRADLE_IMAGE, c._PYTHON_IMAGE]))")
    forms = {}
    names = ("KRIYA_OCI_IMAGE", "KRIYA_OCI_MAVEN_IMAGE", "KRIYA_OCI_GRADLE_IMAGE", "KRIYA_OCI_PYTHON_IMAGE")
    for label, overrides in (("unset", {}), ("empty", {n: "" for n in names}), ("set", {n: f"img/{n.lower()}:1" for n in names})):
        env = {k: v for k, v in os.environ.items() if k not in names}
        env.update(overrides)
        env["PYTHONPATH"] = str(REPO)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        out = subprocess.run([sys.executable, "-c", code], cwd=str(tmp), env=env, capture_output=True, text=True, timeout=120, check=True)
        forms[label] = json.loads(out.stdout)
    return forms


# ------------------------------------------------------------------ KUP configuration forms

def kup_forms(tmp: Path, monkeypatch) -> Dict[str, Any]:
    from kriya.kup.cli_ops import run_kup_operation
    from kriya.kup.inspect import encode_response

    world = World(tmp / "kup", monkeypatch)
    monkeypatch.setattr("kriya.kup.execution.tool_visibility", lambda: [{"tool": "git", "available": True, "path": "/usr/bin/git",
                                                                          "checked": "ran --version"}])
    monkeypatch.setenv("KRIYA_STATE_DIR", str(world.outside / "state"))
    monkeypatch.setenv("KRIYA_AUTHORITY_HOME", str(world.outside / "authority"))
    folders = {"plain": {}, "malformed": "llm: [unclosed\n", "protected": PROTECTED, "refused": {"execution_policy": {"enabled": True}},
               "state_relative": {"paths": {"state": "state"}}}
    for name, content in folders.items():
        _write(world.tmp / name / "kriya.yaml", content)
        world.roots.append((world.tmp / name, f"<{name.upper()}>"))
    requests = [(f"{op}:{name}", {"operation": op, "workspace": str(world.tmp / name)})
                for op in ("configuration.approval_status", "execution.preflight", "execution.context", "skills.list", "tools.list")
                for name in folders]
    forms: Dict[str, Any] = {}
    for label, trust in (("no_trust", None), ("trust_outside", str(world.outside / "trust.json")), ("trust_inside", "<FOLDER>/trust.json"),
                         ("trust_relative", "trust.json")):
        for key, request in requests:
            if trust is None:
                monkeypatch.delenv("KRIYA_TRUST_FILE", raising=False)
            else:
                monkeypatch.setenv("KRIYA_TRUST_FILE", trust.replace("<FOLDER>", request["workspace"]))
            if label != "no_trust" and not key.startswith(("configuration.", "execution.preflight")):
                continue
            answer = json.loads(encode_response(run_kup_operation(AppConfig(), dict(request), request_id="golden")))
            forms[f"{label}/{key}"] = normalize(answer, world)
        if label == "no_trust":
            stale = {"operation": "configuration.approve", "workspace": str(world.tmp / "malformed"), "fingerprint": "sha256:" + "0" * 64}
            forms["no_trust/configuration.approve:malformed"] = normalize(
                json.loads(encode_response(run_kup_operation(AppConfig(), stale, request_id="golden"))), world)
        else:
            approve = {"operation": "configuration.approve", "workspace": str(world.tmp / "protected"), "fingerprint": "sha256:" + "0" * 64}
            forms[f"{label}/configuration.approve:protected"] = normalize(
                json.loads(encode_response(run_kup_operation(AppConfig(), approve, request_id="golden"))), world)
    return forms


# ------------------------------------------------------------------ the comparisons

def _compare(name: str, value: Any) -> None:
    text = json.dumps(value, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    path = GOLDEN / name
    if WRITE:
        GOLDEN.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return
    expected = json.loads(path.read_text(encoding="utf-8"))
    differing = sorted(k for k in set(expected) | set(value) if expected.get(k) != value.get(k))
    assert differing == [], f"{name}: these cases differ from the pre-D3 golden: {differing}"
    assert path.read_text(encoding="utf-8") == text, f"{name}: not byte-identical to the pre-D3 golden"


def test_resolution_forms_are_byte_identical_to_the_pre_d3_golden(tmp_path, monkeypatch):
    forms = {}
    for name, setup in _resolution_cases():
        with monkeypatch.context() as case:
            forms[name] = resolution_form(name, setup, tmp_path, case)
    _compare("resolution_forms.json", forms)
    assert not WRITE, "goldens written; rerun without KRIYA_WRITE_CONFIG_GOLDEN"


def test_environment_homes_are_byte_identical_to_the_pre_d3_golden(tmp_path, monkeypatch):
    with monkeypatch.context() as case:
        forms = environment_homes(tmp_path, case)
    _compare("environment_homes.json", forms)
    assert not WRITE, "goldens written; rerun without KRIYA_WRITE_CONFIG_GOLDEN"


def test_kup_configuration_forms_are_byte_identical_to_the_pre_d3_golden(monkeypatch):
    # A unique temporary directory of a FIXED length (not tmp_path, whose length differs under xdist): two messages are cut at 300
    # characters (``str(error)[:300]``), so the cut position must not depend on how deep the test's directory is.
    with tempfile.TemporaryDirectory() as root, monkeypatch.context() as case:
        forms = kup_forms(Path(root), case)
    _compare("kup_config_forms.json", forms)
    assert not WRITE, "goldens written; rerun without KRIYA_WRITE_CONFIG_GOLDEN"


def test_the_goldens_cannot_pass_vacuously():
    """Every matrix row exists, outcomes differ where the inputs differ, and each family has a success and an error."""
    resolution = json.loads((GOLDEN / "resolution_forms.json").read_text(encoding="utf-8"))
    assert sorted(resolution) == sorted(name for name, _ in _resolution_cases())
    assert "error" in resolution["state_relative"]["resolve"] and resolution["state_relative"]["resolve"]["error"] == "StateDirectoryError"
    assert resolution["log_relative"]["resolve"]["error"] == "LogDirectoryError"
    assert resolution["protected_unapproved"]["load"]["error"] == "ConfigAuthorityError"
    assert "app" in resolution["protected_approved"]["load"], "an approved protected field loads"
    assert resolution["protected_trust_inside"]["load"]["error"] == "TrustPathInsideWorkspaceError"
    assert resolution["none"]["load"]["app"] != resolution["discovered"]["load"]["app"]
    homes = json.loads((GOLDEN / "environment_homes.json").read_text(encoding="utf-8"))
    assert homes["KRIYA_AUTHORITY_HOME=unset"]["authority_home"] != homes["KRIYA_AUTHORITY_HOME=absolute"]["authority_home"]
    assert homes["KRIYA_STATE_DIR=relative"]["resolve_state_directory"]["error"] == "StateDirectoryError"
    assert homes["KRIYA_MODEL_RUNTIME_PROBE=zero"] is False and homes["KRIYA_MODEL_RUNTIME_PROBE=unset"] is True
    assert homes["oci_images"]["set"] != homes["oci_images"]["unset"]
    kup = json.loads((GOLDEN / "kup_config_forms.json").read_text(encoding="utf-8"))
    assert kup["no_trust/configuration.approval_status:malformed"]["error"]["code"] == "CONFIG_LOAD_FAILED"
    assert kup["no_trust/execution.preflight:refused"]["data"]["ready"] is False
    assert kup["trust_outside/configuration.approval_status:protected"]["data"]["status"] == "unavailable"
