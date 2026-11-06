"""Project health rows (product/demo-72h): the capabilities of a folder that no other KUP answer already states, each with a state, a closed reason code and the
configuration fields or observations that decided it.

``RunService.preflight`` adds these as ``health`` (additive) next to the memory view, so the cockpit's capability strip and Project Health render ONE backend answer
and recompute nothing. Every row is derived from the folder's RESOLVED configuration or from a read-only observation; none is a claim about a run (a run-level
USED / ACTIVE / BLOCKED state needs that run's evidence and is a different projection). Closed state vocabulary:

  AVAILABLE      the configuration or observation says the capability is in force
  DEGRADED       in force, but weaker than the safe default (the reason code says how)
  UNAVAILABLE    cannot be used here (the reason code says why)
  NOT_REQUIRED   nothing in this folder needs it
  UNKNOWN        the configuration did not load, or nothing was observed

Honest by construction: containment is MANDATORY with no operator opt-out (the configuration refuses any other value: kriya/config/authority.py ContainmentOptOutError), so
the security row reports that invariant; ``jdtls`` has no contained mode and is therefore never started (kriya/tools/lsp.py), so the language-server row says exactly that
whatever is installed; and the structural code-intelligence row is the code-understanding capability a run actually has.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from kriya.platform import private_store
from kriya.tools.containment import CONTAINMENT_REQUIRED

AVAILABLE = "AVAILABLE"
DEGRADED = "DEGRADED"
UNAVAILABLE = "UNAVAILABLE"
NOT_REQUIRED = "NOT_REQUIRED"
UNKNOWN = "UNKNOWN"
STATES = (AVAILABLE, DEGRADED, UNAVAILABLE, NOT_REQUIRED, UNKNOWN)
CONFIG_UNAVAILABLE = "CONFIG_UNAVAILABLE"


def _row(row_id: str, label: str, state: str, reason: str, detail: str, evidence: List[str]) -> Dict[str, Any]:
    return {"id": row_id, "label": label, "state": state, "reason_code": reason, "detail": detail, "evidence": evidence}


def capability_rows(cfg: Optional[Any], *, state_directory: Optional[str]) -> List[Dict[str, Any]]:
    """The health rows for a folder whose resolved configuration is ``cfg`` (None when it did not load) and whose Kriya state directory is ``state_directory``."""
    if cfg is None:
        return [_row(i, label, UNKNOWN, CONFIG_UNAVAILABLE, "The folder's configuration did not load, so nothing can be said about this yet.", [])
                for i, label in (("security", "Security"), ("private_state", "Private state"), ("lookup", "Outward lookup"), ("verification", "Runtime verification"),
                                 ("recovery", "Recovery"), ("lsp", "Language server"), ("code_intelligence", "Code intelligence"))]
    auto = cfg.autonomy
    rows: List[Dict[str, Any]] = []

    # Containment is MANDATORY with no operator opt-out: the configuration refuses any other value at load (ContainmentOptOutError), so a loaded configuration IS a contained
    # one. This row states that invariant and reads none of the containment fields (their only reader is the canonical decision, kriya/tools/containment.py).
    rows.append(_row(
        "security", "Security", AVAILABLE, CONTAINMENT_REQUIRED,
        "Verification commands run only in an isolating container with the network denied - there is no setting that turns this off; writes are limited to the authorized scope; "
        "credential-bearing files are protected paths; secrets are redacted from evidence.",
        ["configuration load: a non-mandatory containment value is refused (ContainmentOptOutError)", "kriya/tools/containment.py"]))

    observed = private_store.inspect_private_directory(state_directory) if state_directory else private_store.MISSING
    private_state = {
        private_store.PRIVATE: (AVAILABLE, "PRIVATE_STATE_VERIFIED", "Kriya's run state directory is this user's own and closed to others."),
        private_store.NOT_PRIVATE: (DEGRADED, "STATE_DIRECTORY_NOT_PRIVATE", "The state directory is open to other accounts; the next run tightens it before writing."),
        private_store.MISSING: (UNKNOWN, "STATE_DIRECTORY_NOT_CREATED", "No state directory exists yet; the first run creates it private."),
        private_store.UNSAFE: (UNAVAILABLE, "STATE_DIRECTORY_UNSAFE", "The state location is a link, not a directory, or another account's; a run refuses it."),
    }[observed]
    rows.append(_row("private_state", "Private state", private_state[0], private_state[1], private_state[2], ["observed: the state directory's owner and mode"]))

    egress = auto.egress_policy
    if auto.web_lookup_enabled:
        rows.append(_row("lookup", "Outward lookup", DEGRADED if egress != "local_only" else AVAILABLE, "LOOKUP_ENABLED_" + ("LOCAL_ONLY" if egress == "local_only" else "EGRESS_OPEN"),
                         "Public lookup is enabled. A lookup is requested only when a run finds a public-knowledge gap, the query is sanitized and private repository context is not sent.",
                         ["autonomy.web_lookup_enabled", "autonomy.egress_policy"]))
    else:
        rows.append(_row("lookup", "Outward lookup", NOT_REQUIRED, "LOOKUP_DISABLED", "Public lookup is off: nothing leaves this machine for knowledge. Enable it only through the operator configuration.",
                         ["autonomy.web_lookup_enabled", "autonomy.egress_policy"]))

    rows.append(_row("verification", "Runtime verification", AVAILABLE if auto.run_verification_enabled else UNAVAILABLE,
                     "RUN_VERIFICATION_ENABLED" if auto.run_verification_enabled else "RUN_VERIFICATION_DISABLED",
                     "A goal that asks for observable behaviour is verified by running the generated application in containment." if auto.run_verification_enabled else
                     "Run verification is switched off, so a goal that requires observable behaviour ends unsuccessfully rather than unverified.",
                     ["autonomy.run_verification_enabled"]))

    rows.append(_row("recovery", "Recovery", AVAILABLE, "RETRY_BUDGET_CONFIGURED",
                     f"A failed attempt is diagnosed and retried against the exact failure; a run with no progress stops after {auto.max_consecutive_no_progress_attempts} consecutive attempts.",
                     ["autonomy.max_consecutive_no_progress_attempts"]))

    rows.append(_row("lsp", "Language server", UNAVAILABLE, "JDTLS_NOT_CONTAINABLE",
                     "The Java language server (jdtls) imports the project's build on the host and has no contained mode, so Kriya never starts it; code understanding comes from "
                     "the structural index below.", ["configuration load: containment is mandatory", "kriya/tools/lsp.py"]))

    rows.append(_row("code_intelligence", "Code intelligence", AVAILABLE, "STRUCTURAL_INDEX",
                     "Kriya indexes the repository structurally (symbols, references, implementations) and uses it to localize a change; it does not depend on a language server.",
                     ["kriya/code_intel"]))
    return rows
