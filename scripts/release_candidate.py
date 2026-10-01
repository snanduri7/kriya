#!/usr/bin/env python3
"""PRD-036: freeze, check and certify one exact release candidate.

The operator production configuration comes from the environment:
KRIYA_RELEASE_CONFIG (the config file), KRIYA_RELEASE_TRUST_FILE (its SEC-009
approval) and KRIYA_RELEASE_WORKSPACE (the workspace it is approved for).
The config loads exactly as a production run loads it, so an unapproved or
changed config fails here.

  release_candidate.py identity --out FILE      record the candidate identity (the freeze)
  release_candidate.py check --recorded FILE    exit 0 only if FILE is still CURRENT on this host
  release_candidate.py record-trial --candidate FILE --after FILE --report FILE \\
        --started TS --ended TS --evidence DIR  append one live-matrix trial to the streak
  release_candidate.py status --candidate FILE  print the streak (exit 0 only when CERTIFIED)
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from _model_certification import CASES  # noqa: E402 - the live matrix's own case table, after sys.path

from kriya.config.config import load_config  # noqa: E402 - after sys.path
from kriya.core import model_certification as mc  # noqa: E402 - after sys.path
from kriya.core.release_candidate import (  # noqa: E402 - after sys.path
    CURRENT,
    case_set_identity,
    compare_release_candidate,
    release_candidate_identity,
)


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.exit(f"[release-candidate] {name} is not set")
    return os.path.realpath(os.path.expanduser(value))


def current_identity() -> dict:
    config, trust, workspace = (_required("KRIYA_RELEASE_CONFIG"), _required("KRIYA_RELEASE_TRUST_FILE"),
                                _required("KRIYA_RELEASE_WORKSPACE"))
    os.chdir(workspace)
    cfg = load_config(config, trust_file=trust)
    return release_candidate_identity(cfg, config, case_set=case_set_identity(mc.CASE_SET_VERSION, CASES))


def _read(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _write(path: str, value: dict) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("identity").add_argument("--out", required=True)
    sub.add_parser("check").add_argument("--recorded", required=True)
    trial = sub.add_parser("record-trial")
    for name in ("--candidate", "--after", "--report", "--started", "--ended", "--evidence"):
        trial.add_argument(name, required=True)
    sub.add_parser("status").add_argument("--candidate", required=True)
    args = parser.parse_args(argv)
    # current_identity() changes into the release workspace (load_config
    # resolves against the working directory), so every path the caller
    # gave is made absolute against the caller's directory first.
    for name in ("out", "recorded", "candidate", "after", "report", "evidence"):
        if getattr(args, name, None):
            setattr(args, name, os.path.abspath(getattr(args, name)))

    if args.command == "identity":
        identity = current_identity()
        _write(args.out, identity)
        print(f"[release-candidate] identity {identity['digest']} -> {args.out}")
        return 0
    if args.command == "check":
        recorded = _read(args.recorded)
        result = compare_release_candidate(recorded, current_identity())
        print(json.dumps({"candidate": recorded.get("digest"), **result}, indent=2))
        return 0 if result["status"] == CURRENT else 1
    if args.command == "record-trial":
        entry = mc.record_trial(_read(args.candidate), _read(args.report), started=args.started, ended=args.ended,
                                evidence=args.evidence, candidate_after=_read(args.after))
        print(json.dumps(entry, indent=2, sort_keys=True))
        return 0 if entry["outcome"] == mc.CERTIFIED else 1
    status = mc.streak_status(_read(args.candidate)["digest"])
    print(json.dumps({k: v for k, v in status.items() if k != "trials"}
                     | {"trials": [{k: t[k] for k in ("trial", "outcome", "streak_after", "reasons")}
                                   for t in status["trials"]]}, indent=2))
    return 0 if status["status"] == mc.CERTIFIED else 1


if __name__ == "__main__":
    sys.exit(main())
