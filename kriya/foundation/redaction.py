"""The ONE redaction owner (S2, OI-98, SEC-CLI-URL-CREDENTIAL-REDACTION-001).

Owner rule (2026-10-19): URL userinfo and every secret-bearing value are never displayed or logged; one owner, no
per-field special cases. Every display, log, record and error-message channel that can carry a configuration value
asks this module; nothing else in Kriya decides what is secret.

Why FOUNDATION: the callers sit in every layer - CONFIG (logging_setup, authority_approval), AUTHORITY (policy
telemetry/egress, launch approval), PROVIDER (core.llm, model_runtime, embedding), APPLICATION, ADAPTER-KUP and
ADAPTER-CLI. FOUNDATION (L0) is the one layer all of them may import without a new allowlist row or a widened
footnote (DEPENDENCY-RULES §1). The module is pure: standard library only, no I/O, no state.

Two entry points, one rule set:

* ``redact_value(value)`` - a structure (a configuration dump, a pending authority field, report evidence).
  A secret-bearing KEY hides its whole value; a credential CONTAINER key (``env``) hides every value inside it with
  the names kept; every string leaf, under any key, goes through ``redact_text``. A falsy value (None, "", 0, False,
  {}) carries no secret and is shown as-is, so an unset credential still reads as unset.
* ``redact_text(text)`` - free text (a log line, an error message, a URL, an argv element). Value SHAPES, matched
  wherever they occur: URL userinfo (``scheme://user[:password]@`` -> ``scheme://***REDACTED***@``, up to the LAST
  ``@`` of the authority, so an unencoded ``@`` or a ``%40`` inside the password is covered and a token used as the
  username is covered too); ``Bearer``/``Basic`` credentials; ``name=value`` / ``name: value`` pairs whose name is
  credential-shaped; well-known token shapes (AWS access key ids, ``sk-`` keys, GitHub tokens).

What stays readable (deliberate, pinned): SHA-256 digests and commit SHAs (no credential-shaped name or prefix), a URL
without userinfo (an ``@`` in its path or query is not userinfo), an e-mail address and an scp-style ``git@host:path``
remote (no ``scheme://``), and key names that merely contain a secret word as a substring (``max_tokens``,
``token_budget``, ``secretary``): key names are matched by WORD (snake/kebab/camel segments), never by substring.

Not a redaction (kept elsewhere on purpose): the SEC-009 approval digest input
(``authority_approval._redact_env_values``, salted digests so a rotated secret still invalidates an approval) and the
identity canonicalizer ``core.model_runtime.endpoint_identity`` (identity-bearing digest input); both already drop
or digest secrets and must keep their bytes.
"""
from __future__ import annotations

import re
from typing import Any, List

REDACTED = "***REDACTED***"

# Last word of a key (snake/kebab/camel split, lower-cased) that makes the key secret-bearing.
_SECRET_LAST_WORDS = frozenset({
    "password", "passwd", "pwd", "passphrase", "secret", "token", "credential", "credentials", "authorization",
    "apikey", "cookie",
})
# Last TWO words joined (``api_key``, ``ApiKey``, ``private-key``) that make the key secret-bearing.
_SECRET_LAST_PAIRS = frozenset({"apikey", "accesskey", "privatekey", "secretkey", "authkey"})
# A key whose value is a credential container: every value inside is hidden, the names are kept.
_CONTAINER_KEYS = frozenset({"env"})

_WORD_SPLIT = re.compile(r"[^A-Za-z0-9]+")
_CAMEL = re.compile(r"([a-z0-9])([A-Z])")

# scheme://<userinfo>@ : the userinfo runs to the LAST '@' before the authority ends ('/', '?', '#', whitespace).
_URL_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://)[^\s/?#]+@")
_BEARER = re.compile(r"(?i)\bBearer\s+[^\s'\"]+")
# Basic: case-sensitive and base64-shaped with at least one digit/upper/+/=, so prose ("Basic setup") stays readable.
_BASIC = re.compile(r"\bBasic\s+(?=[A-Za-z0-9+/]*[0-9A-Z+/=])[A-Za-z0-9+/]{8,}={0,2}")
_KEY_VALUE_SECRET = re.compile(
    r"(?i)\b((?:password|secret|token|api[_-]?key|access[_-]?key|auth(?:orization)?|credential)s?)([:=]\s*)(\S+)"
)
_SHAPED_SECRETS = (
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),             # AWS access key id
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),          # OpenAI-style API key
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),   # GitHub token prefixes
)


def _words(key: str) -> List[str]:
    return [w for w in _WORD_SPLIT.split(_CAMEL.sub(r"\1 \2", key).lower()) if w]


def is_secret_key(key: Any) -> bool:
    """True when ``key`` names a secret-bearing value, by WORD: the last word is a credential word or the last two
    words form one (``api_key``, ``Auth-Token``, ``dbPassword``, ``client_secret``); ``max_tokens`` is not."""
    if not isinstance(key, str):
        return False
    words = _words(key)
    if not words:
        return False
    return words[-1] in _SECRET_LAST_WORDS or "".join(words[-2:]) in _SECRET_LAST_PAIRS


def redact_text(text: str) -> str:
    """``text`` with every secret-shaped value replaced by ``REDACTED`` (see the module docstring). A new string;
    a non-string is returned unchanged."""
    if not isinstance(text, str) or not text:
        return text
    redacted = _URL_USERINFO.sub(lambda m: f"{m.group(1)}{REDACTED}@", text)
    # Scheme credentials before name=value: "Authorization: Bearer x" must hide x, not the word Bearer.
    redacted = _BEARER.sub(f"Bearer {REDACTED}", redacted)
    redacted = _BASIC.sub(f"Basic {REDACTED}", redacted)
    redacted = _KEY_VALUE_SECRET.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", redacted)
    for pattern in _SHAPED_SECRETS:
        redacted = pattern.sub(REDACTED, redacted)
    return redacted


def _hide(value: Any) -> Any:
    """A secret-bearing value: every non-empty leaf hidden, the structure (dict keys, list length) kept."""
    if isinstance(value, dict):
        return {k: _hide(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_hide(v) for v in value]
    return REDACTED if value else value


def redact_value(value: Any) -> Any:
    """A display-safe copy of ``value`` (dicts, lists, tuples and scalars; tuples become lists). Never mutates."""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if is_secret_key(key) or (key in _CONTAINER_KEYS and isinstance(item, (dict, list, tuple))):
                out[key] = _hide(item)
            else:
                out[key] = redact_value(item)
        return out
    if isinstance(value, (list, tuple)):
        return [redact_value(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


__all__ = ("REDACTED", "is_secret_key", "redact_text", "redact_value")
