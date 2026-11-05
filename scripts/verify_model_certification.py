#!/usr/bin/env python3
"""PRD-035 revision binding: verify that a live-model certification is bound to the exact Kriya executable and configuration it ran on.

The authoritative evidence is the content-digested matrix report (``model-certification.json``: ``{content, content_digest}``) and the sealed
record ``scripts/certify_model.sh`` stores from it (``~/.kriya/certifications/<key digest>.json``, whose ``report_digest`` is the report's
``content_digest``). The report's ``content.kriya_identity`` carries the Git commit, tree, ``kriya/`` and ``plugins/`` subtrees, the
tracked-worktree-clean state (when the matrix began and when it ended) and the sha256 of the production configuration file, so the record is
bound to them transitively. This verifier is fail-closed: every check below must hold, otherwise the verdict is REFUSED (exit 1).

  python scripts/verify_model_certification.py --report OUT/model-certification.json --repo . --config $KRIYA_RELEASE_CONFIG [--out FILE]
  ... --historical    check the evidence against the Git objects only (no requirement that the checkout still is that commit)

No product code is involved: this reads the report, the record and Git, and imports only the record format from ``kriya.core.model_certification``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from typing import Any, Dict, List, Mapping, Optional

BOUND, REFUSED = "BOUND_CERTIFIED", "REFUSED"
CASE_IDS = [f"C{number}" for number in range(1, 12)]  # the signed PRD-035 matrix: C1-C11


def _git(repo: str, *args: str) -> str:
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, check=False).stdout.strip()


def _sha256_of(path: str) -> Optional[str]:
    try:
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except OSError:
        return None


def verify(report_path: str, repo: str, config_path: Optional[str], *, historical: bool = False,
           expect_commit: Optional[str] = None) -> Dict[str, Any]:
    from kriya.core import model_certification as mc

    problems: List[str] = []
    try:
        with open(report_path, "r", encoding="utf-8") as handle:
            report = json.load(handle)
        content = report["content"]
    except (OSError, ValueError, KeyError, TypeError):
        return {"verdict": REFUSED, "problems": ["report_unreadable"]}

    if report.get("content_digest") != mc._content_digest(content):  # noqa: SLF001 - the record format's own digest
        problems.append("report_digest_mismatch")
    if content.get("status") != mc.CERTIFIED or content.get("tier") != mc.TARGET_TIER:
        problems.append("report_not_certified_on_the_target_tier")
    cases = content.get("cases") or []
    if [c.get("case_id") for c in cases] != CASE_IDS or any(c.get("verdict") != "PASSED" for c in cases):
        problems.append("not_all_eleven_cases_passed")
    if content.get("case_set_version") != mc.CASE_SET_VERSION:
        problems.append("case_set_version_mismatch")

    bound = content.get("kriya_identity")
    if not isinstance(bound, Mapping) or not isinstance(bound.get("start"), Mapping) or not isinstance(bound.get("end"), Mapping):
        problems.append("kriya_identity_not_recorded")
        return {"verdict": REFUSED, "problems": problems}
    start, end = bound["start"], bound["end"]
    if start != end:
        problems.append("identity_changed_during_the_matrix")
    for label, identity in (("start", start), ("end", end)):
        if identity.get("tracked_worktree_clean") is not True:
            problems.append(f"worktree_not_clean_at_{label}")

    # The recorded tree fields must be what Git says about the recorded commit (an edited or copied field fails here).
    commit = str(start.get("commit") or "")
    if not commit or _git(repo, "cat-file", "-t", commit) != "commit":
        problems.append("recorded_commit_unknown_to_git")
    else:
        for field, rev in (("tree", f"{commit}^{{tree}}"), ("kriya_tree", f"{commit}:kriya"), ("plugins_tree", f"{commit}:plugins")):
            if not start.get(field) or start.get(field) != _git(repo, "rev-parse", rev):
                problems.append(f"{field}_mismatch")

    if not historical:
        head = _git(repo, "rev-parse", "HEAD")
        if head != commit or (expect_commit is not None and expect_commit != commit):
            problems.append("checkout_is_not_the_recorded_commit")
        if _git(repo, "status", "--porcelain", "--untracked-files=no"):
            problems.append("worktree_not_clean_now")

    recorded_config = start.get("config")
    if not isinstance(recorded_config, Mapping) or not recorded_config.get("sha256"):
        problems.append("production_config_digest_not_recorded")
    elif config_path is None:
        problems.append("production_config_not_supplied")
    elif _sha256_of(config_path) != recorded_config.get("sha256"):
        problems.append("production_config_digest_mismatch")

    identity = content.get("identity") or {}
    key = mc.CertificationKey(model=str(identity.get("model")), runtime_digest=str(identity.get("runtime_fingerprint")),
                              inference_settings_digest=str(identity.get("inference_settings_digest")),
                              environment_digest=str((content.get("execution_environment") or {}).get("digest")),
                              case_set_version=int(content.get("case_set_version") or 0))
    record = mc._load(mc.record_path(key))  # noqa: SLF001 - sealed-record reader of the record format
    if record is None:
        problems.append("record_missing_or_invalid")
    else:
        if record.get("status") != mc.CERTIFIED:
            problems.append("record_not_certified")
        if record.get("report_digest") != report.get("content_digest"):
            problems.append("record_does_not_seal_this_report")
    return {"verdict": BOUND if not problems else REFUSED, "problems": problems,
            "bound_to": {"commit": start.get("commit"), "tree": start.get("tree"), "kriya_tree": start.get("kriya_tree"),
                         "plugins_tree": start.get("plugins_tree"), "config_sha256": (recorded_config or {}).get("sha256"),
                         "case_set_version": content.get("case_set_version"), "report_digest": report.get("content_digest"),
                         "runtime_fingerprint": identity.get("runtime_fingerprint"),
                         "inference_settings_digest": identity.get("inference_settings_digest"),
                         "environment_digest": (content.get("execution_environment") or {}).get("digest")}}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="PRD-035: verify a live-model certification's Kriya revision/configuration binding (fail closed)")
    parser.add_argument("--report", required=True)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--config", default=None, help="the production configuration file the matrix ran with")
    parser.add_argument("--historical", action="store_true")
    parser.add_argument("--expect-commit", default=None)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)
    verdict = verify(args.report, args.repo, args.config, historical=args.historical, expect_commit=args.expect_commit)
    text = json.dumps(verdict, indent=2, sort_keys=True)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
    print(text)
    print(f"[verify-model-certification] {verdict['verdict']}", file=sys.stderr)
    return 0 if verdict["verdict"] == BOUND else 1


if __name__ == "__main__":
    sys.exit(main())
