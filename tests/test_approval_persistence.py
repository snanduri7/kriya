"""Approval subjects, digests and records: persisted and serialized forms are byte-identical across the C3 move (full modularization,
slice C3; ``kriya/workflow/runtime_command_approval.py`` and the review helpers of ``kriya/control/launch_approval.py`` ->
``kriya/policy/approval.py``; ``kriya.core.llm.is_local_url`` -> ``kriya/policy/egress.py``).

How these are persisted (TRACED at C2 HEAD 05cfa59, before the move): the launch record's ``pending_approval`` dict
(``build_pending`` / ``build_pending_runtime``: plain JSON values; the review id, the candidate digest and the command fingerprint are
sha256 strings over JSON/NUL-joined text, never a module path), the decision file written by ``decide`` and the fingerprint kept on
``GenerationState``. No production code pickles a subject or a decision or records ``__module__``/``__qualname__``; the ``repr`` of the
two frozen dataclasses names no module. ``ApprovalRefused`` is serialized only as its ``code`` and ``message``.

``tests/golden/policy_approval/serialized_forms.json`` was WRITTEN AT C2 HEAD (before any C3 edit) by ``serialized_forms()`` below,
through the OLD import paths; this test recomputes it. ``OLD_PICKLES`` (captured at C2 HEAD, module path
``kriya.workflow.runtime_command_approval``) must still load: the facade keeps the old path importable. Regenerate the golden only for
a deliberate format change: ``.venv/bin/python -m tests.test_approval_persistence --write``.
"""
from __future__ import annotations

import asyncio
import base64
import json
import pickle
import sys
from pathlib import Path
from typing import Any, Dict

import kriya.control.launch_approval as launch_approval
import kriya.workflow.runtime_command_approval as rca
from kriya.core.llm import is_local_url

GOLDEN = Path(__file__).parent / "golden" / "policy_approval" / "serialized_forms.json"

# pickle.dumps(obj, protocol=4), base64, captured at C2 HEAD (module path kriya.workflow.runtime_command_approval)
OLD_PICKLES: Dict[str, str] = {
    "subject": (
        "gASVfQEAAAAAAACMJ2tyaXlhLndvcmtmbG93LnJ1bnRpbWVfY29tbWFuZF9hcHByb3ZhbJSMFVJ1bnRpbWVDb21tYW5kU3ViamVjdJSTlCmBlH2UKIwHdW5pdF9pZJSMA3UtMZSMCWNyaXRlcmlvbpSMDHByaW50cyBoZWxsb5SMEXdvcmtpbmdfZGlyZWN0b3J5lIwDL3d0lIwIY29tbWFuZHOUjANtdm6UjAItcZSMCWV4ZWM6amF2YZSHlIwEamF2YZSMBC1qYXKUjAVhLmphcpSHlIaUjA1pbnB1dF9jaGFubmVslIwFc3RkaW6UjAxzdGRpbl9zaGEyNTaUjEdzaGEyNTY6NTg5MWI1YjUyMmQ1ZGYwODZkMGZmMGIxMTBmYmQ5ZDIxYmI0ZmM3MTYzYWYzNGQwODI4NmEyZTg0NmY2YmUwM5SMBnJlYXNvbpSMBnJ1biBpdJSMEWVudmlyb25tZW50X25hbWVzlCmMEWluY29tcGxldGVfcmVhc29ulE51Yi4="),
    "decision": (
        "gASVbwAAAAAAAACMJ2tyaXlhLndvcmtmbG93LnJ1bnRpbWVfY29tbWFuZF9hcHByb3ZhbJSMFlJ1bnRpbWVDb21tYW5kRGVjaXNpb26Uk5QpgZR9lIwUYXBwcm92ZWRfZmluZ2VycHJpbnSUjAhzaGEyNTY6ZpRzYi4="),
    "declined": (
        "gASVZQAAAAAAAACMJ2tyaXlhLndvcmtmbG93LnJ1bnRpbWVfY29tbWFuZF9hcHByb3ZhbJSMFlJ1bnRpbWVDb21tYW5kRGVjaXNpb26Uk5QpgZR9lIwUYXBwcm92ZWRfZmluZ2VycHJpbnSUTnNiLg=="),
}

FILES = [{"filepath": "src/Main.java", "content": "--- a\n+++ b\n+class Main {}\n-old\n"}, {"filepath": "pom.xml", "content": "+<project/>\n"}]


def _subject(**overrides: Any) -> Any:
    fields = {"unit_id": "u-1", "criterion": "prints hello", "working_directory": "/wt", "commands": [["mvn", "-q", "exec:java"], ["java", "-jar", "a.jar"]],
              "input_channel": "stdin", "stdin_payload": "hello\n", "reason": "run it"}
    fields.update(overrides)
    return rca.build_subject(**fields)


def _normalized(record: Any) -> Any:
    if not isinstance(record, dict):
        return record
    out = dict(record)
    for key in ("requested_at", "expires_at"):
        out[key] = "<stamp>" if key in out else None
    return out


def serialized_forms() -> Dict[str, str]:
    subject, no_stdin = _subject(), _subject(stdin_payload=None, input_channel="")
    forms: Dict[str, str] = {
        "fingerprint_of": rca.fingerprint_of("/wt", [("a", "b"), ("c",)], "none", None),
        "fingerprint_of.stdin": rca.fingerprint_of("/wt", [["a"]], "stdin", rca.stdin_digest("x")),
        "stdin_digest": json.dumps([rca.stdin_digest("hello\n"), rca.stdin_digest(None), rca.stdin_digest("")]),
        "subject.fingerprint": subject.fingerprint, "subject.repr": repr(subject), "subject.no_stdin.repr": repr(no_stdin),
        "decision.repr": json.dumps([repr(rca.RuntimeCommandDecision("sha256:f")), repr(rca.RuntimeCommandDecision(None))]),
        "constants": json.dumps([rca.REVIEW_KIND, rca.MAX_REVIEW_ROUNDS, rca.MAX_COMMANDS, rca.MAX_ARGV_CHARS, launch_approval.KIND_CHANGE,
                                 launch_approval.KIND_RUNTIME, list(launch_approval.KINDS), launch_approval.WORKING_DIRECTORY_KIND,
                                 launch_approval.REVIEW_SECTION_HEADER, list(launch_approval.REFUSAL_CODES)]),
        "reviewable": json.dumps([rca.reviewable(subject), rca.reviewable(_subject(commands=[])), rca.reviewable(_subject(commands=[["x" * 4001]])),
                                  rca.reviewable(_subject(working_directory="")), rca.reviewable(_subject(commands=[["a"]] * 21))]),
        "approval_matches": json.dumps([rca.approval_matches(rca.RuntimeCommandDecision(subject.fingerprint), subject),
                                        rca.approval_matches(rca.RuntimeCommandDecision(no_stdin.fingerprint), subject),
                                        rca.approval_matches(rca.RuntimeCommandDecision(None), subject)]),
        "authored": json.dumps([rca.commands_authored_in_goal([["mvn", "-q", "test"]], "please run mvn -q test now"),
                                rca.commands_authored_in_goal([["mvn", "-q", "test"]], "never run mvn -q test"),
                                rca.commands_authored_in_goal([["make"]], "run `make`"), rca.commands_authored_in_goal([["make"]], "run make"),
                                rca.commands_authored_in_goal([["bash", "-c", "ls"]], "bash -c ls"),
                                rca.commands_authored_in_goal([["mvn", "test"]], "mvn test", stdin_sha256="sha256:x")]),
        "candidate_digest": launch_approval.candidate_digest(FILES),
        "review_identity": launch_approval.review_identity("20261016T000000-abcdef12", "wsid", 3, "sha256:" + "a" * 64, "reason"),
        "split_reason": json.dumps([launch_approval.split_reason(f"note\n\n{launch_approval.REVIEW_SECTION_HEADER}\nreview text\n"),
                                    launch_approval.split_reason(" just a note ")]),
        "build_pending": json.dumps(_normalized(launch_approval.build_pending("20261016T000000-abcdef12", "wsid", 1, FILES, "why")), sort_keys=True),
        "build_pending_runtime": json.dumps(_normalized(launch_approval.build_pending_runtime("20261016T000000-abcdef12", "wsid", 2, subject)), sort_keys=True),
        "build_pending_runtime.secret": json.dumps(_normalized(launch_approval.build_pending_runtime(
            "20261016T000000-abcdef12", "wsid", 2, _subject(commands=[["curl", "-H", "Authorization: Bearer sk-abcdefghijklmnopqrstuvwxyz0123456789"]])))),
        "build_pending_runtime.unreviewable": json.dumps(launch_approval.build_pending_runtime("20261016T000000-abcdef12", "wsid", 2, _subject(commands=[]))),
    }
    refused = launch_approval.ApprovalRefused("STALE_REVIEW", "msg")
    forms["approval_refused"] = json.dumps([refused.code, refused.message, str(refused), repr(refused), type(refused).__name__])

    async def legacy_yes(_files: Any, _reason: str) -> bool:
        return True

    typed = rca.TypedApprovalCallback(lambda _f, _r: True, lambda s: rca.RuntimeCommandDecision("sha256:other"))
    forms["ask"] = json.dumps([repr(asyncio.run(rca.ask(legacy_yes, subject))), repr(asyncio.run(rca.ask(lambda _f, _r: False, subject))),
                               repr(asyncio.run(rca.ask(typed, subject))), repr(asyncio.run(rca.ask(typed, _subject(commands=[]))))])
    forms["is_local_url"] = json.dumps([is_local_url(u) for u in (
        "http://localhost:11434/v1", "http://127.0.0.1:8080", "http://[::1]:1/", "http://box.local:1/", "http://10.0.0.2/", "http://169.254.1.1/",
        "http://8.8.8.8:11434/v1", "localhost:11434", "", "http://[bad/")])
    forms["class_names"] = json.dumps([cls.__name__ for cls in (rca.RuntimeCommandSubject, rca.RuntimeCommandDecision, rca.TypedApprovalCallback,
                                                                launch_approval.ApprovalRefused)])
    return forms


def test_persisted_and_serialized_forms_are_byte_identical_to_the_pre_move_golden():
    assert serialized_forms() == json.loads(GOLDEN.read_text(encoding="utf-8"))


def test_a_pickle_written_before_the_move_still_loads_to_an_equal_object():
    expected = {"subject": _subject(), "decision": rca.RuntimeCommandDecision("sha256:f"), "declined": rca.RuntimeCommandDecision(None)}
    assert set(OLD_PICKLES) == set(expected)
    for name, payload in OLD_PICKLES.items():
        loaded = pickle.loads(base64.b64decode(payload))   # noqa: S301 - bytes captured by this test file
        assert loaded == expected[name] and type(loaded) is type(expected[name]), name


if __name__ == "__main__" and sys.argv[1:] == ["--write"]:
    GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN.write_text(json.dumps(serialized_forms(), indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {GOLDEN}")
    for label, obj in {"subject": _subject(), "decision": rca.RuntimeCommandDecision("sha256:f"), "declined": rca.RuntimeCommandDecision(None)}.items():
        print(label, base64.b64encode(pickle.dumps(obj, protocol=4)).decode("ascii"))
