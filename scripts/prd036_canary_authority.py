#!/usr/bin/env python3
"""PRD-036 canary authority: the operator's requirement/acceptance authority for the canary goal, bound to the release candidate.

A mandatory behaviour requirement of the canary goal has no deterministic closer of its own, so the canary supplies operator authority
through Kriya's documented seams (``kriya generate --requirements/--acceptance/--acceptance-approval/--verification-authority``). These artifacts
are release-gate inputs, so they are sealed into ONE canonical identity (``canary_authority``) that the release candidate digest includes and the
canary and the final gate both re-verify. A verdict that merely logged them would not bind them.

The authority directory (private, outside the repository: the goal's baseline is a private third-party fixture) holds:

  requirements.json            kriya.requirements/1  the closed requirement set of the canary run
  mapping.json                 kriya.canary_authority_mapping/1  original text -> normalized requirement -> authority kind -> closure
  KriyaAcceptanceTest.java     the JUnit 5 acceptance suite (cases tagged ``// kriya_requirement: REQ-n``)
  acceptance-approval.json     kriya.acceptance_approval/2  (owner's human authority) - required iff the mapping has acceptance_approval entries
  verification-authority.json  kriya.verification_authority/1 + its assets - required iff the mapping has verification_authority entries

Authority kinds in mapping.json: ``acceptance_approval`` (cases + the owner's explicit approval for a GENERAL rule), ``verification_authority``
(a sealed external oracle run under containment) and ``deterministic`` (a closer Kriya owns: mutation scope, API preservation, ...).

  identity  --dir D --goal G --workspace W --expect PATH [--out F]    print / write the canonical identity
  check     --dir D --goal G --workspace W --expect PATH --candidate C [--out F]   recompute; exit 0 only if equal to C["canary_authority"]
  flags     --dir D --mapping-only                                    the ``kriya generate`` arguments the mapping requires
  draft     --dir D --goal G --workspace W                            write the PROPOSED approval and the manifest from the artifacts

Every artifact goes through Kriya's own loaders (the same binders ``kriya generate`` uses), against a throw-away state directory, so a refusal here is
a refusal Kriya would give before any model call.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from typing import Any, Dict, List, Mapping, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

REQUIREMENTS, MAPPING, ACCEPTANCE = "requirements.json", "mapping.json", "KriyaAcceptanceTest.java"
APPROVAL, AUTHORITY = "acceptance-approval.json", "verification-authority.json"
MAPPING_FORMAT = "kriya.canary_authority_mapping/1"
KINDS = ("acceptance_approval", "verification_authority", "deterministic")
IDENTITY_VERSION = 1


class CanaryAuthorityError(Exception):
    """Refused: ``problems`` names every reason (a missing artifact, a mapping mismatch, a Kriya loader refusal, ...)."""

    def __init__(self, problems: List[str]) -> None:
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


def _sha256_file(path: str) -> str:
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _json(path: str, what: str, problems: List[str]) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError) as error:
        problems.append(f"{what}: unreadable ({type(error).__name__}: {error})")
        return None


def _text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def needed_artifacts(directory: str) -> Dict[str, bool]:
    """Which optional artifacts the mapping requires (a missing REQUIRED artifact is refused by ``identity``)."""
    problems: List[str] = []
    mapping = _json(os.path.join(directory, MAPPING), MAPPING, problems) or {}
    kinds = {entry.get("authority") for entry in mapping.get("entries") or [] if isinstance(entry, dict)}
    return {"approval": "acceptance_approval" in kinds, "authority": "verification_authority" in kinds}


def identity(directory: str, goal_path: str, workspace: str, expect: str) -> Dict[str, Any]:
    """The canonical canary-authority identity. Raises CanaryAuthorityError (every reason) on any refusal."""
    from kriya.workflow.acceptance_approval import load_approval
    from kriya.workflow.acceptance_oracle import load_acceptance
    from kriya.workflow.authority_bundle import load_authority_bundle
    from kriya.workflow.requirement_contract import load_requirement_contract
    from kriya.workflow.requirements import goal_identity
    from kriya.workflow.workflow import _project_language, admission_tracked_workspace_paths
    from kriya.workflow.worktree import git_read_lines

    problems: List[str] = []
    paths = {name: os.path.join(directory, name) for name in (REQUIREMENTS, MAPPING, ACCEPTANCE, APPROVAL, AUTHORITY)}
    for name in (REQUIREMENTS, MAPPING, ACCEPTANCE):
        if not os.path.isfile(paths[name]):
            problems.append(f"required artifact missing: {name}")
    try:
        with open(goal_path, "r", encoding="utf-8") as handle:
            goal = handle.read()
    except OSError as error:
        raise CanaryAuthorityError([f"goal unreadable: {error}"]) from error
    mapping = _json(paths[MAPPING], MAPPING, problems) if os.path.isfile(paths[MAPPING]) else None
    entries = [e for e in (mapping or {}).get("entries") or [] if isinstance(e, dict)]
    if mapping is not None and (mapping.get("format") != MAPPING_FORMAT or not entries):
        problems.append(f"{MAPPING}: wrong format or no entries")
    kinds = {e.get("authority") for e in entries}
    if mapping is not None and not kinds <= set(KINDS):
        problems.append(f"{MAPPING}: unknown authority kind(s) {sorted(kinds - set(KINDS))}")
    needs_approval, needs_authority = "acceptance_approval" in kinds, "verification_authority" in kinds
    if needs_approval and not os.path.isfile(paths[APPROVAL]):
        problems.append(f"required artifact missing: {APPROVAL} (the mapping has acceptance_approval entries)")
    if needs_authority and not os.path.isfile(paths[AUTHORITY]):
        problems.append(f"required artifact missing: {AUTHORITY} (the mapping has verification_authority entries)")
    if problems:
        raise CanaryAuthorityError(problems)

    with tempfile.TemporaryDirectory(prefix="kriya-canary-authority-") as state:
        try:
            head = git_read_lines(workspace, "rev-parse", "HEAD")[0]
            language = _project_language(admission_tracked_workspace_paths(workspace))
        except Exception as error:  # noqa: BLE001 - reported as a refusal with its reason
            raise CanaryAuthorityError([f"workspace {workspace!r} is not a readable git repository: {error}"]) from error
        try:
            contract = load_requirement_contract(paths[REQUIREMENTS], goal, state_root=state, workspace=workspace)
        except Exception as error:  # noqa: BLE001
            raise CanaryAuthorityError([f"requirements: {error}"]) from error
        requirement_set = contract.requirement_set
        by_id = {requirement.id: requirement for requirement in requirement_set.requirements}

        # REQ mapping: every requirement mapped once, text-identical to the contract, original text verbatim from the goal.
        mapped = [e.get("requirement_id") for e in entries]
        if sorted(mapped) != sorted(by_id) or len(set(mapped)) != len(mapped):
            problems.append(f"{MAPPING}: requirement ids {sorted(map(str, mapped))} != contract ids {sorted(by_id)}")
        for entry in entries:
            requirement = by_id.get(entry.get("requirement_id"))
            if requirement is None:
                continue
            if _text_sha256(str(entry.get("normalized_text"))) != _text_sha256(requirement.text):
                problems.append(f"{MAPPING}: {entry['requirement_id']} normalized_text differs from the contract's requirement text")
            if str(entry.get("original_text") or "") not in goal:
                problems.append(f"{MAPPING}: {entry['requirement_id']} original_text is not a verbatim statement of the goal")
            if not str(entry.get("closure") or "").strip():
                problems.append(f"{MAPPING}: {entry['requirement_id']} states no closure mechanism")
        wanted = {kind: sorted(e["requirement_id"] for e in entries if e.get("authority") == kind and e.get("requirement_id") in by_id)
                  for kind in KINDS}

        try:
            acceptance = load_acceptance(paths[ACCEPTANCE], requirement_set, state)
        except Exception as error:  # noqa: BLE001
            raise CanaryAuthorityError(problems + [f"acceptance: {error}"]) from error
        cases = {rid: sorted(acceptance.identities_for(rid)) for rid in by_id}
        covered = sorted(rid for rid, names in cases.items() if names)
        if covered != wanted["acceptance_approval"]:
            problems.append(f"acceptance cases cover {covered}, the mapping assigns acceptance_approval to {wanted['acceptance_approval']}")

        approval_doc: Optional[Dict[str, Any]] = None
        if needs_approval:
            try:
                approval = load_approval(paths[APPROVAL], requirement_set, acceptance, state_root=state, workspace=workspace, base_revision=head)
            except Exception as error:  # noqa: BLE001 - a wrong/stale approval is refused by Kriya's own binder
                raise CanaryAuthorityError(problems + [f"approval: {error}"]) from error
            approved = sorted(approval.entries)
            if approved != wanted["acceptance_approval"]:
                problems.append(f"approval covers {approved}, the mapping assigns acceptance_approval to {wanted['acceptance_approval']}")
            approval_doc = {"sha256": _sha256_file(paths[APPROVAL]), "digest": approval.digest, "requirement_ids": approved}

        authority_doc: Optional[Dict[str, Any]] = None
        if needs_authority:
            try:
                bundle = load_authority_bundle(paths[AUTHORITY], requirement_set, goal, state_root=state, workspace=workspace,
                                               base_revision=head, project_language=language)
            except Exception as error:  # noqa: BLE001
                raise CanaryAuthorityError(problems + [f"verification authority: {error}"]) from error
            covers = sorted(bundle.covers)
            if covers != wanted["verification_authority"]:
                problems.append(f"verification authority covers {covers}, the mapping assigns verification_authority to {wanted['verification_authority']}")
            authority_doc = {"manifest_sha256": _sha256_file(paths[AUTHORITY]), "digest": bundle.digest, "authority_id": bundle.authority_id,
                             "assets": dict(sorted(bundle.assets.items())), "requirement_ids": covers}
        if problems:
            raise CanaryAuthorityError(problems)

        document: Dict[str, Any] = {
            "version": IDENTITY_VERSION,
            "goal": {"sha256": _sha256_file(goal_path), "identity": goal_identity(goal)},
            "baseline_revision": head, "expect": expect,
            "requirements": {"sha256": _sha256_file(paths[REQUIREMENTS]), "contract_digest": contract.digest,
                             "set_digest": requirement_set.digest,
                             "entries": [{"id": r.id, "kind": r.kind, "text_sha256": _text_sha256(r.text)} for r in requirement_set.requirements]},
            "mapping": {"sha256": _sha256_file(paths[MAPPING]),
                        "entries": [{"requirement_id": e["requirement_id"], "authority": e["authority"],
                                     "deterministic_closers": sorted(e.get("deterministic_closers") or []),
                                     "closure_sha256": _text_sha256(e["closure"])} for e in sorted(entries, key=lambda e: e["requirement_id"])]},
            "acceptance": {"sha256": _sha256_file(paths[ACCEPTANCE]), "digest": acceptance.digest, "cases": cases},
            "approval": approval_doc, "verification_authority": authority_doc,
        }
    return {**document, "digest": _digest(document)}


def compare(recorded: Mapping[str, Any], current: Mapping[str, Any]) -> List[str]:
    """Why ``current`` is not the identity the candidate recorded (empty = identical)."""
    changes: List[str] = []
    if not recorded:
        return ["the candidate records no canary_authority"]
    for key in sorted(set(recorded) | set(current)):
        if key != "digest" and recorded.get(key) != current.get(key):
            changes.append(key)
    if recorded.get("digest") != current.get("digest") and not changes:
        changes.append("digest")
    if recorded.get("digest") != _digest({k: v for k, v in recorded.items() if k != "digest"}):
        changes.append("recorded.digest_invalid")
    return changes


def generate_flags(directory: str) -> List[str]:
    """The ``kriya generate`` arguments the mapping requires: --requirements and --acceptance always, the others only where the mapping needs them."""
    needs = needed_artifacts(directory)
    flags = ["--requirements", os.path.join(directory, REQUIREMENTS), "--acceptance", os.path.join(directory, ACCEPTANCE)]
    if needs["approval"]:
        flags += ["--acceptance-approval", os.path.join(directory, APPROVAL)]
    if needs["authority"]:
        flags += ["--verification-authority", os.path.join(directory, AUTHORITY)]
    return flags


def draft(directory: str, goal_path: str, workspace: str) -> Dict[str, str]:
    """Write the PROPOSED approval (accept_suite_as_sufficient true for the mapping's acceptance_approval entries - the owner reviews and seals it by
    renaming it to acceptance-approval.json) and the manifest of the verification authority, from the artifacts, through Kriya's own seams."""
    from kriya.workflow.acceptance_approval import approval_template
    from kriya.workflow.acceptance_oracle import load_acceptance
    from kriya.workflow.requirement_contract import load_requirement_contract
    from kriya.workflow.worktree import git_read_lines

    with open(goal_path, "r", encoding="utf-8") as handle:
        goal = handle.read()
    mapping = _json(os.path.join(directory, MAPPING), MAPPING, [])
    entries = {e["requirement_id"]: e for e in mapping["entries"]}
    written: Dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="kriya-canary-authority-") as state:
        head = git_read_lines(workspace, "rev-parse", "HEAD")[0]
        contract = load_requirement_contract(os.path.join(directory, REQUIREMENTS), goal, state_root=state, workspace=workspace)
        rset = contract.requirement_set
        acceptance = load_acceptance(os.path.join(directory, ACCEPTANCE), rset, state)
        ids = sorted(rid for rid, e in entries.items() if e["authority"] == "acceptance_approval")
        if ids:
            template = approval_template(rset, acceptance, ids, head, workspace)
            for entry in template["approvals"]:
                entry["accept_suite_as_sufficient"] = True
            path = os.path.join(directory, "acceptance-approval.PROPOSED.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(template, handle, indent=2, sort_keys=True)
                handle.write("\n")
            written["approval"] = path
        vids = sorted(rid for rid, e in entries.items() if e["authority"] == "verification_authority")
        if vids:
            assets = {}
            for rel in ("oracle/verify.sh", "oracle/base/DefaultDriverService.java"):
                assets[rel] = _sha256_file(os.path.join(directory, rel))
            manifest = {"format": "kriya.verification_authority/1", "authority_id": "prd036-canary-req3-oracle", "type": "external_acceptance_command",
                        "goal_sha256": rset.goal_digest, "requirement_set_sha256": rset.digest, "base_revision": head, "assets": assets,
                        "prepare": None, "verify": ["sh", "oracle/verify.sh"], "timeout_seconds": 300,
                        "toolchain": {"language": "java", "build_tool": "maven"}, "verdict_protocol": "exit_code", "visibility": "hidden",
                        "covers": [{"requirement_id": rid, "requirement_text_sha256": _text_sha256(rset.get(rid).text), "claim": "BEHAVIOR",
                                    "accepted_strength": "GENERAL", "accept_as_sufficient": True,
                                    "why": "the sealed oracle proves, deterministically, that the candidate equals the base outside the body of "
                                           "delete(Long driverId) (imports excepted) and that delete's annotations and signature are unchanged"}
                                   for rid in vids]}
            path = os.path.join(directory, AUTHORITY)
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(manifest, handle, indent=2, sort_keys=True)
                handle.write("\n")
            written["authority"] = path
    return written


def _common(parser: argparse.ArgumentParser, *, workspace: bool = True) -> None:
    parser.add_argument("--dir", required=True)
    if workspace:
        parser.add_argument("--goal", required=True)
        parser.add_argument("--workspace", required=True)
        parser.add_argument("--expect", default="")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    ident = sub.add_parser("identity")
    _common(ident)
    ident.add_argument("--out")
    check = sub.add_parser("check")
    _common(check)
    check.add_argument("--candidate", required=True)
    check.add_argument("--out")
    _common(sub.add_parser("flags"), workspace=False)
    _common(sub.add_parser("draft"))
    args = parser.parse_args(argv)
    try:
        if args.command == "flags":
            print("\n".join(generate_flags(args.dir)))
            return 0
        if args.command == "draft":
            print(json.dumps(draft(args.dir, args.goal, args.workspace), indent=2))
            return 0
        current = identity(args.dir, args.goal, args.workspace, args.expect)
    except CanaryAuthorityError as error:
        print(json.dumps({"status": "REFUSED", "problems": error.problems}, indent=2), file=sys.stderr)
        return 1
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(current, handle, indent=2, sort_keys=True)
            handle.write("\n")
    if args.command == "identity":
        print(current["digest"])
        return 0
    with open(args.candidate, "r", encoding="utf-8") as handle:
        recorded = json.load(handle).get("canary_authority") or {}
    changes = compare(recorded, current)
    print(json.dumps({"status": "CURRENT" if not changes else "STALE", "changes": changes, "digest": current["digest"]}, indent=2))
    return 0 if not changes else 1


if __name__ == "__main__":
    sys.exit(main())
