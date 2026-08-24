"""Prompt-injection detection - MA4.12 of the control-plane implementation
plan (see kriya/policy/__init__.py for MA4's overall principle;
kriya/policy/trust.py for MA4.10's TrustLevel/TrustedContent this module
scans).

Per the trust model: "Repository content and retrieved external content are
untrusted data, even when they contain imperative language... Any
repository text that attempts to [redefine tool permissions, request
secrets, authorize egress, expand filesystem scope, or override the user's
goal] is treated as prompt-injection content." This module is that
detector, scoped to exactly the two trust levels the doc names as untrusted
data - TrustLevel.REPOSITORY and TrustLevel.EXTERNAL. Content at
MILESTONE or above is never scanned: it's either the user's own goal, an
approved policy, or a milestone spec already produced under supervision -
scanning it for "trying to override the user's goal" is a category error,
not an extra safety margin.

High-confidence, deliberately narrow, by design (section 12's own "never
silently default to ALLOW" spirit applies here too, inverted: never flag
ordinary imperative documentation prose just because it contains a verb
like "ignore" or "disregard" in a harmless sentence). Every pattern below
requires the specific object of an override attempt (prior instructions,
the execution policy, a secret store, an egress target, the sandbox
boundary, the user's own goal) adjacent to the verb, not the verb alone.
This is a precision-first v1 - expected to grow new patterns as real
false-negatives are found in practice, not tuned for maximal recall today.

quarantine, not discard: scan_for_injection() never modifies, strips, or
truncates the scanned content - it only ever returns a report ALONGSIDE it.
Deciding what "quarantine" means operationally (stronger fencing, blocking
promotion, routing to human review) is a caller decision, not this
module's. No wiring into a real call site yet (mirrors MA4.10/MA4.11's own
scope) - kriya/workflow/workflow.py's existing "Begin/End Untrusted
Reference Context" fencing around kriya-learn RAG matches (around line 1139)
is the one place EXTERNAL-trust content already flows through today's
pipeline and is the natural first real caller, but wiring that up is later
MA4 work (MA4.13's central-seam integration), not this task's.
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import Tuple

from kriya.policy.trust import TrustedContent, TrustLevel

_SCANNED_TRUST_LEVELS = (TrustLevel.REPOSITORY, TrustLevel.EXTERNAL)

_MATCH_CONTEXT_CHARS = 40
_MATCH_TEXT_CAP = 200


class InjectionCategory(str, Enum):
    """Mirrors the trust model doc's own list of what lower-priority
    content can never do, verbatim."""

    PERMISSION_OVERRIDE = "permission_override"
    SECRET_EXFILTRATION = "secret_exfiltration"
    EGRESS_AUTHORIZATION = "egress_authorization"
    SCOPE_EXPANSION = "scope_expansion"
    GOAL_OVERRIDE = "goal_override"


@dataclass(frozen=True)
class InjectionMatch:
    """One matched pattern. `matched_text` is a short, capped excerpt of
    the SCANNED content around the match (never a secret - it's the
    attacker's own injected wording, not real credential material), kept
    short specifically so a match record stays safe to log per MA4's own
    telemetry rule (kriya/policy/model.py's ActionRequest.metadata
    docstring establishes the same "small facts only" constraint)."""

    category: InjectionCategory
    reason_code: str
    pattern_description: str
    matched_text: str


@dataclass(frozen=True)
class InjectionScanResult:
    flagged: bool
    matches: Tuple[InjectionMatch, ...]


# Each entry: (category, reason_code, human description, compiled pattern).
# Every pattern requires the verb AND its specific override object together
# - never a bare verb - to keep the false-positive rate low against normal
# imperative documentation prose (setup READMEs, troubleshooting guides,
# code comments telling a human reader to do something).
_PATTERNS = (
    (
        InjectionCategory.PERMISSION_OVERRIDE,
        "INJECTION_PERMISSION_OVERRIDE_DETECTED",
        "ignore/disregard prior instructions or rules",
        re.compile(r"\b(ignore|disregard)\s+(all\s+|any\s+)?(previous|prior|above|earlier)\s+(instructions|rules|guidance|policy)", re.IGNORECASE),
    ),
    (
        InjectionCategory.PERMISSION_OVERRIDE,
        "INJECTION_PERMISSION_OVERRIDE_DETECTED",
        "claims the reader is now authorized to act outside policy",
        re.compile(r"\byou'?re?\s+now\s+(allowed|permitted|authorized)\s+to\b", re.IGNORECASE),
    ),
    (
        InjectionCategory.PERMISSION_OVERRIDE,
        "INJECTION_PERMISSION_OVERRIDE_DETECTED",
        "claims to bypass or override the execution policy",
        re.compile(r"\b(bypass|override)\s+(the\s+)?(execution\s+)?polic(y|ies)\b", re.IGNORECASE),
    ),
    (
        InjectionCategory.PERMISSION_OVERRIDE,
        "INJECTION_PERMISSION_OVERRIDE_DETECTED",
        "claims an action is pre-approved",
        re.compile(r"\bthis\s+(command|action|request)\s+(is|has been)\s+pre-?approved\b", re.IGNORECASE),
    ),
    (
        InjectionCategory.SECRET_EXFILTRATION,
        "INJECTION_SECRET_EXFILTRATION_DETECTED",
        "asks to reveal a secret, credential, or API key",
        re.compile(r"\b(print|output|reveal|show|dump|leak)\s+(the\s+)?(contents?\s+of\s+)?(the\s+)?(\.env|api[ _-]?key|secret(s)?|password(s)?|credentials?|access[ _-]?token)\b", re.IGNORECASE),
    ),
    (
        InjectionCategory.SECRET_EXFILTRATION,
        "INJECTION_SECRET_EXFILTRATION_DETECTED",
        "asks to reveal the system prompt or instructions",
        re.compile(r"\breveal\s+your\s+(system\s+)?prompt\b|\bwhat\s+(is|are)\s+your\s+(system\s+)?instructions\b", re.IGNORECASE),
    ),
    (
        InjectionCategory.EGRESS_AUTHORIZATION,
        "INJECTION_EGRESS_AUTHORIZATION_DETECTED",
        "asks to send data to an external URL",
        re.compile(r"\b(send|upload|post|exfiltrate)\s+(this|the|all)\s+(data|files?|repository|repo|contents?)\s+to\s+(https?|ftp)://", re.IGNORECASE),
    ),
    (
        InjectionCategory.EGRESS_AUTHORIZATION,
        "INJECTION_EGRESS_AUTHORIZATION_DETECTED",
        "instructs an outbound network call to an explicit URL",
        re.compile(r"\bmake\s+an?\s+(http|network|outbound)\s+(request|call)\s+to\s+https?://", re.IGNORECASE),
    ),
    (
        InjectionCategory.SCOPE_EXPANSION,
        "INJECTION_SCOPE_EXPANSION_DETECTED",
        "asks to read or access files outside the workspace/sandbox",
        re.compile(r"\b(read|access|list)\s+files?\s+(outside|beyond)\s+the\s+(workspace|sandbox|repo(sitory)?)", re.IGNORECASE),
    ),
    (
        InjectionCategory.SCOPE_EXPANSION,
        "INJECTION_SCOPE_EXPANSION_DETECTED",
        "asks to escape the sandbox/container",
        re.compile(r"\bescape\s+the\s+(sandbox|workspace|container)\b", re.IGNORECASE),
    ),
    (
        InjectionCategory.SCOPE_EXPANSION,
        "INJECTION_SCOPE_EXPANSION_DETECTED",
        "asks to grant elevated/root/admin access",
        re.compile(r"\bgrant\s+(yourself\s+)?(full|root|admin(istrator)?)\s+access\b", re.IGNORECASE),
    ),
    (
        InjectionCategory.GOAL_OVERRIDE,
        "INJECTION_GOAL_OVERRIDE_DETECTED",
        "asks to ignore or disregard the user's actual goal",
        re.compile(r"\b(ignore|disregard)\s+the\s+user'?s?\s+(goal|request|original\s+instructions)\b", re.IGNORECASE),
    ),
    (
        InjectionCategory.GOAL_OVERRIDE,
        "INJECTION_GOAL_OVERRIDE_DETECTED",
        "claims a different, hidden real goal or task",
        re.compile(r"\byour\s+(real|actual|true)\s+(goal|task|objective)\s+is\b", re.IGNORECASE),
    ),
)


def should_scan_for_injection(trust_level: TrustLevel) -> bool:
    """True only for the two trust levels the design doc explicitly names
    as untrusted data. Content at MILESTONE or above is exempt - not
    because it couldn't literally contain these words, but because
    scanning the user's own goal (or an approved policy, or a milestone
    spec produced under supervision) for "trying to override the user's
    goal" is a category error."""

    return trust_level in _SCANNED_TRUST_LEVELS


def _excerpt(text: str, start: int, end: int) -> str:
    lo = max(0, start - _MATCH_CONTEXT_CHARS)
    hi = min(len(text), end + _MATCH_CONTEXT_CHARS)
    excerpt = text[lo:hi].strip()
    return excerpt[:_MATCH_TEXT_CAP]


def scan_for_injection(content: TrustedContent) -> InjectionScanResult:
    """Read-only: never modifies `content`. Returns an unflagged, empty
    result immediately for any trust level should_scan_for_injection()
    excludes - scanning is opt-in by trust level, not universal."""

    if not should_scan_for_injection(content.trust_level):
        return InjectionScanResult(flagged=False, matches=())

    matches = []
    for category, reason_code, description, pattern in _PATTERNS:
        for m in pattern.finditer(content.content):
            matches.append(
                InjectionMatch(
                    category=category,
                    reason_code=reason_code,
                    pattern_description=description,
                    matched_text=_excerpt(content.content, m.start(), m.end()),
                )
            )

    return InjectionScanResult(flagged=bool(matches), matches=tuple(matches))
