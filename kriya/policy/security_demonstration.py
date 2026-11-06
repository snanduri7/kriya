"""SECURITY DEMONSTRATION (product/demo-72h): Kriya's file protections, shown by RUNNING them.

Each row is a REAL decision of the canonical owner - ``AuthorizedFileWriter`` / ``AuthorizedFileReader`` (kriya/policy/filesystem.py) and the redactor
(kriya/foundation/redaction.py) - asked about a situation built in a DISPOSABLE temporary directory: a decoy file outside the "project", a symlink and a hard link that
lead to it, a credential-named file, a Kriya control path. The decision, its typed reason code and the rule that made it are the owner's own answer; nothing here decides
anything, and nothing outside the temporary directory is read or written. No secret is displayed: the decoy holds the word DECOY, and the secret the redaction row masks is
assembled at run time, passed through the redactor, and only its LENGTH before and after is reported. One ALLOWED row (a write inside the project) is included on purpose: it
shows the protections are scoped decisions, not a blanket refusal.

Deterministic: the same rows, reason codes and authorities on every call (no timestamps, no paths).
"""
from __future__ import annotations

import os
import tempfile
from typing import Any, Dict, List

from kriya.config.environment import KRIYA_DIRECTORY_NAME
from kriya.foundation.redaction import redact_text
from kriya.policy.filesystem import AuthorizedFileReader, AuthorizedFileWriter

BLOCKED = "BLOCKED"
ALLOWED = "ALLOWED"
REDACTED = "REDACTED"
OUTCOMES = (BLOCKED, ALLOWED, REDACTED)


def _row(row_id: str, protection: str, requested: str, outcome: str, reason_code: str, authority: str, explanation: str) -> Dict[str, Any]:
    return {"id": row_id, "protection": protection, "requested": requested, "outcome": outcome, "reason_code": reason_code, "authority": authority, "explanation": explanation}


def _ask(row_id: str, protection: str, requested: str, result: Any, explanation: str) -> Dict[str, Any]:
    outcome = ALLOWED if result.decision.value == "allow" else BLOCKED
    return _row(row_id, protection, requested, outcome, result.reason_code, result.matched_rule, explanation)


def demonstrate() -> List[Dict[str, Any]]:
    """The rows, in presentation order. Raises nothing for a protection that holds: a row whose owner answered differently than its explanation says would be a test failure, not a hidden one."""
    rows: List[Dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="kriya-security-demo-") as sandbox:
        project, outside = os.path.join(sandbox, "project"), os.path.join(sandbox, "outside")
        os.makedirs(os.path.join(project, "src"))
        os.makedirs(os.path.join(project, KRIYA_DIRECTORY_NAME, "control", "runs"))
        os.makedirs(outside)
        decoy = os.path.join(outside, "decoy.txt")
        with open(decoy, "w", encoding="utf-8") as handle:
            handle.write("DECOY")
        os.symlink(outside, os.path.join(project, "shortcut"))
        os.link(decoy, os.path.join(project, "linked.txt"))

        writer, reader = AuthorizedFileWriter(project), AuthorizedFileReader(project)
        rows.append(_ask("write_inside_scope", "Write-scope enforcement", "write src/app.py inside the project",
                         writer.authorize(os.path.join(project, "src", "app.py")), "A write inside the authorized project scope is permitted - the protections decide, they do not refuse everything."))
        rows.append(_ask("write_outside_scope", "Write-scope enforcement", "write a file outside the project",
                         writer.authorize(os.path.join(outside, "new.txt")), "The path resolves outside every authorized writable root, so no write is made."))
        rows.append(_ask("write_through_symlink", "Symlink containment", "write through a symlink that points outside the project",
                         writer.authorize(os.path.join(project, "shortcut", "new.txt")), "The link is resolved before the decision: where it really leads is outside the project."))
        rows.append(_ask("write_control_state", "Private-state protection", "overwrite Kriya's own run records (.kriya/control)",
                         writer.authorize(os.path.join(project, KRIYA_DIRECTORY_NAME, "control", "runs", "run.json")), "Kriya's control state is not a model-writable location; it is written only by Kriya's own control store."))
        rows.append(_ask("write_credential_file", "Sensitive-content protection", "write a credential-named file (.env)",
                         writer.authorize(os.path.join(project, ".env")), "Credential-bearing file names are never a target of a generated write."))
        rows.append(_ask("read_through_hard_link", "Hard-link containment", "read a file whose other name lives outside the project (a hard link)",
                         reader.authorize(os.path.join(project, "linked.txt")), "A file with a name outside the readable roots is the same file: it is refused by identity, not by name."))
        rows.append(_ask("read_outside_scope", "Read-scope enforcement", "read a file outside the project",
                         reader.authorize(decoy), "The path resolves outside every authorized readable root, so nothing is read."))

    secret = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"      # assembled here so no literal credential sits in the source; never displayed
    masked = redact_text(f"deploy token {secret} done")
    held = secret not in masked
    rows.append(_row("redact_secret", "Credential redaction", "record evidence that contains a credential-shaped string", REDACTED if held else ALLOWED,
                     "SECRET_REDACTED" if held else "SECRET_NOT_REDACTED", "kriya.foundation.redaction.redact_text",
                     f"The credential-shaped string ({len(secret)} characters) is removed before anything is stored or shown; the stored text carries a redaction marker instead."))
    return rows
