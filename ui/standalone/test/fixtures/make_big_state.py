"""Generates a LARGE, deterministic Kriya state directory for the GUI scale measurements (hardening Phase E). TEST INFRASTRUCTURE: it writes only into the directory it is given
(a scratch directory), through the REAL recorder (AttemptEvidenceWriter) and the REAL trace logger, so the real read path serves it.

  python make_big_state.py <state_dir> <workspace_dir> [--history N] [--records N] [--attempts N] [--files N]

Creates: N small runs with a recorded workspace identity (a long history), and ONE large run (many attempts, many changed files with real diff blobs, hostile and Unicode paths,
a very long goal, thousands of extra records), each with its trace row and its evidence store.
"""
import argparse
import os
import sys
import time

os.environ.setdefault("KRIYA_MODEL_RUNTIME_PROBE", "0")

from kriya.control.workspace_identity import canonical_workspace, workspace_identity  # noqa: E402
from kriya.core.attempt_evidence import model  # noqa: E402
from kriya.core.attempt_evidence.writer import AttemptEvidenceWriter  # noqa: E402
from kriya.core.trace import TraceLogger  # noqa: E402

NASTY = ["src/Ünïcode/файл.java", "docs/space in name.md", "src/it's \"quoted\" & $(subshell) `tick`.py", "a/" * 30 + "deep.txt", "src/日本語/テスト.py", "x" * 200 + ".go"]


def store(state, run_id, identity, workspace, records, *, goal_digest="sha256:" + "0" * 64, seal=True, capture=model.CAPTURE_FULL):
    writer = AttemptEvidenceWriter(state, run_id, capture=capture, manifest={"workspace_identity": identity, "workspace_path": workspace, "goal_digest": goal_digest, "base_revision": None})
    records(writer)
    if seal:
        assert writer.seal("run_closed")
    return {"kind": "evidence.attempt_store", "attempt": 0, "source": "workflow.run_generation_workflow", "authority": "auxiliary", "message": "attempt evidence store",
            "failure_type": None, "operation": None, "details": {"run_id": run_id, "status": "CLOSED", "capture": "full"}, "created_at": 1.0}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("state")
    ap.add_argument("workspace")
    ap.add_argument("--history", type=int, default=3000)
    ap.add_argument("--records", type=int, default=20000)
    ap.add_argument("--attempts", type=int, default=30)
    ap.add_argument("--files", type=int, default=8)
    ap.add_argument("--special", action="store_true", help="also create runs in the states a person can meet: unsealed, tampered, digest-only")
    args = ap.parse_args()
    os.makedirs(args.state, exist_ok=True)
    workspace = canonical_workspace(args.workspace)
    identity = workspace_identity(workspace)
    trace = TraceLogger(os.path.join(args.state, "traces.db"))
    started = time.time()
    ident = {"unit_id": "direct", "unit_kind": "direct", "invocation_seq": 1, "phase": "attempt"}

    for i in range(args.history):
        run_id = f"20260101T{(i // 3600) % 24:02d}{(i // 60) % 60:02d}{i % 60:02d}-{i:08x}"

        def small(w, i=i):
            w.append("run.opened", {}, identity=None)
            w.append("attempt.opened", {"attempt": 1}, identity={**ident, "attempt_number": 1})
            w.append("gate.result", {"gate": "test", "success": i % 5 != 0}, identity={**ident, "attempt_number": 1})
            w.append("run.closed", {"outcome": "SUCCESS" if i % 5 else "FAILURE"})
        event = store(args.state, run_id, identity, workspace, small)
        goal = ("Historic goal %d ünï" % i) + (" $(rm -rf)" if i % 7 == 0 else "") + ("g" * 900 if i % 97 == 0 else "")
        trace.log_run(run_id=f"h{i:07x}", goal=goal, duration_sec=float(i % 300), attempts=i % 4, status="success" if i % 5 else "failure", files_modified=[], run_events=[event])

    big_id = "20261011T120000-b16b00b5"

    def big(w):
        w.append("run.opened", {}, identity=None)
        w.append("unit.opened", {"unit_id": "direct"}, identity={"unit_id": "direct", "unit_kind": "direct", "invocation_seq": 1})
        for a in range(1, args.attempts + 1):
            ai = {**ident, "attempt_number": a}
            w.append("attempt.opened", {"mode": "full_set", "operation": "generate"}, identity=ai)
            w.append("model.request", {"dispatched": True}, identity={**ai, "role": "developer", "call_seq": a}, content={"messages": "write the code " * 40})
            w.append("model.result", {"finish_reason": "stop"}, identity={**ai, "role": "developer", "call_seq": a})
            for f in range(args.files):
                path = NASTY[f % len(NASTY)].replace(".", f"{f}.", 1)
                diff = "".join(f"{'+' if n % 3 == 0 else '-' if n % 3 == 1 else ' '}line {n} of {path}\n" for n in range(240))
                w.append("candidate.change", {"decision": "STAGED", "path": path, "created": f % 2 == 0, "deleted": False, "unchanged": False, "lines_added": 80, "lines_removed": 0 if f % 2 == 0 else 80, "text_diff": True},
                         identity=ai, content={"diff": diff})
            for g in range(6):
                w.append("gate.result", {"gate": ["compile", "test", "regression_test", "lint"][g % 4], "success": not (g == 1 and a < args.attempts), "stage": "targeted", "exit_code": 0}, identity=ai)
            w.append("attempt.closed", {"outcome": "SUCCEEDED" if a == args.attempts else "FAILED"}, identity=ai)
        for n in range(args.records):
            w.append("model.request", {"n": n}, identity={**ident, "attempt_number": 1 + n % args.attempts, "role": "reviewer", "call_seq": 1000 + n})
        w.append("unit.closed", {"outcome": "SUCCESS"}, identity={"unit_id": "direct", "unit_kind": "direct", "invocation_seq": 1})
        w.append("run.closed", {"outcome": "SUCCESS", "terminal_status": "SUCCESS"})
    event = store(args.state, big_id, identity, workspace, big)
    trace.log_run(run_id="bigrun01", goal="Implement the whole thing. " + "A very long goal sentence. " * 60, duration_sec=4321.0, attempts=args.attempts, status="success", files_modified=[], run_events=[event])
    if args.special:
        def tiny(w):
            w.append("run.opened", {}, identity=None)
            w.append("attempt.opened", {"attempt": 1}, identity={**ident, "attempt_number": 1})
            w.append("candidate.change", {"decision": "STAGED", "path": "src/a.py", "created": True, "deleted": False, "unchanged": False, "lines_added": 3, "lines_removed": 0, "text_diff": True}, identity={**ident, "attempt_number": 1}, content={"diff": "+a\n+b\n+c\n"})
            w.append("gate.result", {"gate": "test", "success": True}, identity={**ident, "attempt_number": 1})
            w.append("run.closed", {"outcome": "SUCCESS"})
        for tag, kwargs in (("unsealed", {"seal": False}), ("tampered", {}), ("digestonly", {"capture": model.CAPTURE_DIGEST_ONLY})):
            run_id = f"20261012T000000-{tag[:7].encode().hex()[:8]}"
            event = store(args.state, run_id, identity, workspace, tiny, **kwargs)
            trace.log_run(run_id=f"sp-{tag}", goal=f"SPECIAL {tag} run", duration_sec=3.0, attempts=1, status="success", files_modified=[], run_events=[event])
            if tag == "tampered":
                path = os.path.join(args.state, "attempt-evidence", run_id, "records.jsonl")
                data = open(path, "rb").read().split(b"\n")
                data[1] = data[1].replace(b"attempt", b"tampered", 1)
                open(path, "wb").write(b"\n".join(data))
    sys.stdout.write(f"state={args.state} history={args.history} big_run_records~{args.attempts * (args.files + 10) + args.records} elapsed={time.time() - started:.1f}s\n")


if __name__ == "__main__":
    main()
