"""S7: the NARROW process seam for validator-mechanics tests (replaces the old global ``patch("subprocess.Popen")``).

Under mandatory containment the validator runs Kriya-internal probes (``git rev-parse`` for the gate view, venv/toolchain setup) and starts every workload through
``ProcessController`` with a containment prefix, so a global ``subprocess.Popen`` mock answers calls the test never meant to control. The seam a command-construction test
actually intends to control is the validator's ONE process choke point, ``PolymorphicValidator._run_cmd_with_timeout``: it receives the command the validator DECIDED to run
(the production contained command construction stays authoritative - nothing here builds or rewrites a command) and the cwd it runs in, and returns a result dict.

``ScriptedRunner`` records every call and answers from a script: a result dict, or a callable ``(cmd, cwd) -> dict`` (which may create files in ``cwd`` - the gate view -
to model what a real tool would have written). Unscripted calls get ``default`` (a successful empty result) unless ``strict`` is set, which fails the test on a surprise.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Union

from kriya.tools.validate import PolymorphicValidator

Outcome = Union[Dict[str, Any], Callable[[List[str], str], Dict[str, Any]]]


def ok(stdout: str = "", stderr: str = "") -> Dict[str, Any]:
    return {"returncode": 0, "stdout": stdout, "stderr": stderr, "timeout": False}


def fail(stderr: str = "", stdout: str = "", returncode: int = 1) -> Dict[str, Any]:
    return {"returncode": returncode, "stdout": stdout, "stderr": stderr, "timeout": False}


@dataclass
class Call:
    cmd: List[str]
    cwd: str
    kwargs: Dict[str, Any] = field(default_factory=dict)


class ScriptedRunner:
    def __init__(self, monkeypatch, *script: Outcome, default: Optional[Dict[str, Any]] = None, strict: bool = False) -> None:
        self.calls: List[Call] = []
        self._script = list(script)
        self._default = default if default is not None else ok()
        self._strict = strict
        # a plain function on the class, so the validator binds as ``self`` (a bound method of this runner would not be rebound and would receive the validator's arguments shifted)
        monkeypatch.setattr(PolymorphicValidator, "_run_cmd_with_timeout", lambda validator, cmd, cwd, *args, **kwargs: self._run(validator, cmd, cwd, *args, **kwargs))

    def _run(self, _validator, cmd, cwd, timeout=300, stdin_payload=None, **kwargs):      # pylint: disable=unused-argument
        self.calls.append(Call(list(cmd), cwd, dict(kwargs)))
        if self._script:
            outcome = self._script.pop(0)
        elif self._strict:
            raise AssertionError(f"unscripted process call: {cmd}")
        else:
            outcome = self._default
        return dict(outcome(list(cmd), cwd) if callable(outcome) else outcome)

    @property
    def commands(self) -> List[List[str]]:
        return [call.cmd for call in self.calls]

    def roles(self) -> List[str]:
        """A coarse, host-independent label per call (what the command IS, not how it is spelled): venv / pip / pytest / maven / javac / bundle / rspec / other."""
        labels = []
        for cmd in self.commands:
            text = " ".join(cmd)
            labels.append("venv" if " -m venv" in text else "pip" if " -m pip " in text else "pytest" if " -m pytest" in text or text.endswith("pytest") or "pytest.main(" in text
                          else "maven" if cmd and cmd[0].endswith(("mvn", "mvnw")) else "javac" if cmd and cmd[0].endswith("javac") else "bundle" if "bundle" in cmd[:2]
                          else "rspec" if "rspec" in text else "other")
        return labels
