"""REG-R1 Part 1: deterministic reproduction of the live full-regression gate (no model calls).

Runs Kriya a049c02's own PolymorphicValidator.run_tests() (the exact call the live baseline capture and the live
POST gate made: workflow.py capture_brownfield_baselines lambda / run_generation_workflow terminal regression) on a
layout mirroring run 20261006T231821-fb31caad:
  BASE = <root>/.kriya/worktree                         (live: the enforce plan worktree, baseline workspace_path)
  CAND = <root>/.kriya/worktrees/candidate-reg-r1         (live: candidate-kriya-worktree-c2a3db38, sibling)
Both are git worktrees of a clone of the frozen workspace at 67f99bd; CAND carries the staged candidate engine.py.

Recording-only wrappers (arguments and results passed through unchanged):
  PolymorphicValidator._run_cmd_with_timeout       -> argv, cwd, timeout, network, full ProcessResult dict
  PolymorphicValidator.build_containment_profile_and_backend -> profile fields (image, mounts, limits)
  test_execution.collect                           -> byte copy of the per-invocation JUnit XML before Kriya discards it
Nothing is normalized before it is written.

usage (cwd = a SEC-009-approved workspace for the config, env sourced):
  python reg_r1_repro.py <config.yaml> <root> <out_dir> <label> <BASE|CAND>
"""
import dataclasses
import hashlib
import json
import os
import shutil
import sys
import time

from kriya.build_info import version_report
from kriya.config.config import load_config
from kriya.tools import test_execution
from kriya.tools.validate import PolymorphicValidator

RECORD = {"commands": [], "profiles": []}


def _plain(value):
    if dataclasses.is_dataclass(value):
        return {f.name: _plain(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_plain(v) for v in value]
    if hasattr(value, "value") and not isinstance(value, (str, int, float, bool)):
        return value.value
    return value if isinstance(value, (str, int, float, bool, type(None))) else repr(value)


def install_recorders(out_dir, label):
    original_run = PolymorphicValidator._run_cmd_with_timeout
    original_profile = PolymorphicValidator.build_containment_profile_and_backend
    original_collect = test_execution.collect

    def run(self, cmd, cwd, *args, **kwargs):
        started = time.time()
        result = original_run(self, cmd, cwd, *args, **kwargs)
        index = len(RECORD["commands"])
        for stream in ("stdout", "stderr"):
            data = result.get(stream)
            if isinstance(data, str):
                with open(os.path.join(out_dir, f"{label}.cmd{index}.{stream}"), "w", encoding="utf-8",
                          newline="") as handle:
                    handle.write(data)
        RECORD["commands"].append({"index": index, "argv": list(cmd), "cwd": cwd, "args": _plain(args),
                                   "kwargs": _plain(kwargs), "started_epoch": started,
                                   "wall_seconds": round(time.time() - started, 3),
                                   "result": {k: _plain(v) for k, v in result.items() if k not in ("stdout", "stderr")},
                                   "stdout_sha256": hashlib.sha256((result.get("stdout") or "").encode()).hexdigest(),
                                   "stderr_sha256": hashlib.sha256((result.get("stderr") or "").encode()).hexdigest()})
        return result

    def profile(self, *args, **kwargs):
        built, backend = original_profile(self, *args, **kwargs)
        RECORD["profiles"].append({"kwargs": _plain(kwargs), "profile": _plain(built),
                                   "backend": type(backend).__name__ if backend is not None else None})
        return built, backend

    def collect(binding):
        if binding.pytest_report:
            source = os.path.join(binding.workspace, binding.pytest_report)
            if os.path.isfile(source):
                shutil.copyfile(source, os.path.join(out_dir, f"{label}.junit.xml"))
        return original_collect(binding)

    PolymorphicValidator._run_cmd_with_timeout = run
    PolymorphicValidator.build_containment_profile_and_backend = profile
    test_execution.collect = collect


def main(config_path, root, out_dir, label, kind):
    cfg = load_config(config_path)
    base_path = os.path.join(root, ".kriya", "worktree")
    path = base_path if kind == "BASE" else os.path.join(root, ".kriya", "worktrees", "candidate-reg-r1")
    install_recorders(out_dir, label)
    validator = PolymorphicValidator(path, original_workspace_path=base_path, autonomy_cfg=cfg.autonomy)
    started = time.time()
    result = validator.run_tests()
    ended = time.time()
    output = result.get("output") or ""
    with open(os.path.join(out_dir, f"{label}.gate_output.txt"), "w", encoding="utf-8", newline="") as handle:
        handle.write(output)
    meta = {"label": label, "kind": kind, "workspace": path, "original_workspace": base_path,
            "kriya_build": version_report(), "stack": validator.stack, "started_epoch": started,
            "wall_seconds": round(ended - started, 3), "success": result.get("success"),
            "gate_output_sha256": hashlib.sha256(output.encode()).hexdigest(), "gate_output_bytes": len(output.encode()),
            "result": {k: _plain(v) for k, v in result.items() if k != "output"}, **RECORD}
    with open(os.path.join(out_dir, f"{label}.meta.json"), "w", encoding="utf-8") as handle:
        json.dump(meta, handle, indent=1, default=str)
    print(json.dumps({k: meta[k] for k in ("label", "success", "wall_seconds", "gate_output_sha256", "gate_output_bytes")}))


if __name__ == "__main__":
    main(*sys.argv[1:6])
