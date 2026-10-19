"""The ONE redaction owner (S2, OI-98, SEC-CLI-URL-CREDENTIAL-REDACTION-001; completed by S2b, OI-98/OI-131).

Owner rule (2026-10-19): URL userinfo and every secret-bearing value are never displayed or logged; one owner, no
per-field special cases. Every display, log, record and error-message channel that can carry a configuration value or
recorded run evidence asks this module; nothing else in Kriya decides what is secret. It REDACTS A COPY for display:
stored authoritative evidence keeps its raw bytes (digests bind exact bytes; S2b, OI-131).

Why FOUNDATION: the callers sit in every layer - CONFIG (logging_setup, authority_approval), AUTHORITY (policy
telemetry/egress, launch approval), PROVIDER (core.llm, model_runtime, embedding), APPLICATION, ADAPTER-KUP and
ADAPTER-CLI. FOUNDATION (L0) is the one layer all of them may import without a new allowlist row or a widened
footnote (DEPENDENCY-RULES §1). The module is pure: standard library only, no I/O, no state.

Two entry points, one rule set:

* ``redact_value(value)`` - a structure (a configuration dump, a pending authority field, report evidence, a run's
  recorded gate outcomes). A secret-bearing KEY hides its whole value; a credential CONTAINER key (``env``) hides every
  value inside it with the names kept; in a list, the element after a secret-bearing FLAG (``["--token", "x"]``) is
  hidden, and after ``-u``/``--user``/``--proxy-user`` the part after the first colon of ``user:password``; every
  string leaf, under any key, goes through ``redact_text``. A falsy value (None, "", 0, False, {}) carries no secret
  and is shown as-is, so an unset credential still reads as unset.
* ``redact_text(text)`` - free text (a log line, an error message, a URL, an argv element or a joined command line).
  A text that is EXACTLY a JSON document as ``json.dumps`` writes it (Kriya's stored evidence columns) is parsed,
  redacted as a structure and written back the same way, so the text blob and its parsed form always agree and the
  result stays valid JSON. Otherwise value SHAPES, matched wherever they occur:
    - a private-key PEM block (``-----BEGIN ... PRIVATE KEY-----``; the body, to the END line or to the end of a cut text);
    - URL userinfo (``scheme://user[:password]@`` -> ``scheme://***REDACTED***@``, up to the LAST ``@`` of the
      authority, so an unencoded ``@`` or a ``%40`` inside the password is covered and a token used as the username
      is covered too) and scheme-relative ``//user:password@``;
    - ``Bearer``/``Basic`` credentials; an ``Authorization``/``Proxy-Authorization`` header value of any scheme
      (``Token``, ``Digest`` with its parameter list); a ``Cookie``/``Set-Cookie`` header value;
    - a secret-bearing argv flag and its separate value (``--password x``; ``-u user:password`` keeps the user);
    - ``name=value`` / ``name: value`` pairs (spaces around the separator, a ``%3D``/``%3A`` separator, a quoted
      name as in JSON or a Python repr, a JVM ``-Dname=``) whose NAME is secret-bearing by the same word rule as
      ``is_secret_key``; a quoted value is hidden whole (spaces included), a bare value up to whitespace, a quote,
      a backslash or one of ``& ; ,`` (so a query string's other parameters and the enclosing structure survive);
    - well-known token shapes (AWS access key ids, ``sk-`` keys, GitHub classic and fine-grained tokens; S2c: OpenAI
      project/service/admin and Anthropic ``sk-...-`` keys, Slack ``xox?-``, GitLab ``glpat-``, npm ``npm_``, Stripe
      ``sk_/rk_live|test_``, Google ``AIza``, Hugging Face ``hf_``) - a closed table;
    - S2c (OI-144): a compound ALL-CAPS name without separators ending in a long secret word (``PGPASSWORD=``,
      ``GITHUBTOKEN:``); a percent-encoded name (``%70assword=``, ``api%5Fkey=``) and the userinfo of a percent-encoded
      URL (``next=https%3A%2F%2Fu%3Apw%40h``), decoded for DETECTION only - the output never holds a decoded byte.

What stays readable (deliberate, pinned): SHA-256 digests and commit SHAs (no credential-shaped name or prefix), a URL
without userinfo (an ``@`` in its path or query is not userinfo), an e-mail address and an scp-style ``git@host:path``
remote (no ``scheme://``), a scheme-relative ``//user@host`` without a password, a certificate PEM block, and names that
merely contain a secret word (``max_tokens``, ``token_budget``, ``--token-file``, ``secretary``): names are matched by
WORD (snake/kebab/camel segments), never by substring. Known limits (documented, not a leak of a recognised shape): an
UNQUOTED secret value that itself contains whitespace, a quote, a backslash or ``& ; ,`` is hidden only up to that
character; a header kept as a two-element list (``["Authorization", "Token x"]``) carries no flag or key to recognise; a
POSITIONAL secret with no name or flag grammar of its own (``mysql -p<pw>``, ``sshpass -p <pw>``, ``docker login -p <pw>``,
``npm config set //registry/:_authToken <pw>``) is shown - whether to add command-grammar-aware argv redaction is the open
owner question OI-144 (4). Recorded over-redaction (fail-closed): ``pwd=`` / ``PWD=`` (``pwd`` is a secret last word) and
a compound ALL-CAPS name that merely ends in a long secret word (``NOTOKEN=``, ``TOPSECRET=``).

Not a redaction (kept elsewhere on purpose): the SEC-009 approval digest input
(``authority_approval._redact_env_values``, salted digests so a rotated secret still invalidates an approval) and the
identity canonicalizer ``core.model_runtime.endpoint_identity`` (identity-bearing digest input); both already drop
or digest secrets and must keep their bytes.
"""
from __future__ import annotations

import json
import re
from typing import Any, List, Match, Optional
from urllib.parse import unquote, urlsplit, urlunsplit

REDACTED = "***REDACTED***"

# Last word of a key (snake/kebab/camel split, lower-cased) that makes the key secret-bearing.
_SECRET_LAST_WORDS = frozenset({
    "password", "passwd", "pwd", "passphrase", "secret", "token", "credential", "credentials", "authorization",
    "apikey", "cookie",
})
# Last TWO words joined (``api_key``, ``ApiKey``, ``private-key``, ``ssh_key``) that make the key secret-bearing.
_SECRET_LAST_PAIRS = frozenset({"apikey", "accesskey", "privatekey", "secretkey", "authkey", "sshkey"})
# A key whose value is a credential container: every value inside is hidden, the names are kept.
_CONTAINER_KEYS = frozenset({"env"})
# A whole NAME in free text that is secret-bearing besides the key rule (the S2 text rule's spellings): ``auth`` and the
# plural of a one-word secret name (``tokens: x``). Only for a one-word name, so ``max_tokens=4096`` stays readable.
_TEXT_ONLY_NAMES = frozenset({"auth"})
# A compound ALL-CAPS name without separators (the POSIX environment-variable convention: ``PGPASSWORD``, ``GITHUBTOKEN``,
# ``AWSSECRETACCESSKEY``) is secret-bearing when it ENDS in one of these (S2c, OI-144 (1)). Suffix only - never infix
# (``PASSWORDLESS``, ``TOKENIZER`` stay readable) - and only secret words long enough not to collide with a common
# non-secret word: ``PWD`` / ``PASSWD`` are left out (``OLDPWD`` is a directory, ``NOPASSWD`` a sudoers tag; MEASURED
# controls), ``AUTHORIZATION`` is the header rule's. Closed table, pinned by a tripwire test.
_COMPOUND_SUFFIXES = ("PASSWORD", "PASSPHRASE", "SECRET", "TOKEN", "CREDENTIAL", "CREDENTIALS", "APIKEY", "ACCESSKEY",
                      "PRIVATEKEY", "SECRETKEY")
_COMPOUND_NAME = re.compile(r"[A-Z][A-Z0-9]*")
# argv flags whose value is ``user:password``: only the part after the first colon is hidden.
_USER_FLAGS = frozenset({"u", "user", "proxy-user"})
_AUTHORIZATION_HEADER_NAMES = frozenset({"Authorization", "authorization", "Proxy-Authorization", "proxy-authorization"})

_WORD_SPLIT = re.compile(r"[^A-Za-z0-9]+")
_CAMEL = re.compile(r"([a-z0-9])([A-Z])")

_PRIVATE_KEY_PEM = re.compile(
    r"-----BEGIN (?P<label>[A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?)-----(?:.*?)(?P<end>-----END (?P=label)-----|\Z)", re.DOTALL)
# scheme://<userinfo>@ : the userinfo runs to the LAST '@' before the authority ends ('/', '?', '#', whitespace, a
# quote or a backslash - the end of an enclosing string).
_URL_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://)[^\s/?#'\"\\]+@")
# The same userinfo inside a PERCENT-ENCODED URL (a redirect or callback parameter: ``next=https%3A%2F%2Fu%3Apw%40h``):
# ``scheme%3A%2F%2F<userinfo>(@|%40)``, the authority ending at an encoded or literal '/', '?', '#' or at '&'. Matched on
# the bytes as written; the output keeps them as written around the marker (S2c, OI-144 (3)).
_ENCODED_URL_USERINFO = re.compile(
    r"(?i)\b(?P<scheme>[a-z][a-z0-9+.\-]*(?:%2B[a-z0-9+.\-]*)*%3A%2F%2F)(?:(?!%2F|%3F|%23)[^\s/?#&'\"\\])+(?P<at>@|%40)")
# //user:password@ without a scheme (not after ':' - that is the scheme form above); a colon before the first '@' is
# required, so ``//git@host`` (no password) stays readable.
_SCHEME_RELATIVE_USERINFO = re.compile(r"(?<![A-Za-z0-9+.\-:/])//[^\s/?#'\"\\@:]*:[^\s/?#'\"\\]*@")
_BEARER = re.compile(r"(?i)\bBearer\s+[^\s'\"\\]+")
# Basic: case-sensitive and base64-shaped with at least one digit/upper/+/=, so prose ("Basic setup") stays readable.
_BASIC = re.compile(r"\bBasic\s+(?=[A-Za-z0-9+/]*[0-9A-Z+/=])[A-Za-z0-9+/]{8,}={0,2}")
_AUTH_PARAM = r"[\w-]+=(?:\\?\"[^\"\\]*\\?\"|[^\s,'\"\\]*)"
# Authorization: [<scheme> ]<token68 | auth-param list>, the header name spelled as a header is (an all-capitals
# ``AUTHORIZATION:`` is prose or a reason code, see _is_secret_name). A scheme word is hidden too ("Authorization: ***REDACTED***
# ***REDACTED***"), so the shown text never says which credential kind was carried.
_AUTHORIZATION_HEADER = re.compile(
    r"(?<![A-Za-z0-9_\-])(?P<head>(?:[Pp]roxy-)?[Aa]uthorization(?:\\?[\"'])?[ \t]*[:=][ \t]*(?:\\?[\"'])?)"
    r"(?:(?P<scheme>[^\s'\"\\,;=]+)[ \t]+)?"
    rf"(?P<credential>{_AUTH_PARAM}(?:[ \t]*,[ \t]*{_AUTH_PARAM})*|[^\s'\"\\,;]+)")
# Cookie / Set-Cookie: the whole header value (to the end of the line or of the enclosing string).
_COOKIE_HEADER = re.compile(r"(?i)(?<![A-Za-z0-9_\-])((?:set-)?cookie(?:\\?[\"'])?[ \t]*:[ \t]*)[^\r\n'\"\\]*[^\s'\"\\]")
# An argv flag followed by its value as the next word: ``--password x``, ``-u user:password``.
_FLAG_VALUE = re.compile(r"(?<!\S)(?P<flag>--?(?P<name>[A-Za-z][A-Za-z0-9_\-]*))(?P<gap>[ \t]+)"
                         r"(?P<value>'[^'\n]*'|\"[^\"\n]*\"|(?!-)[^\s'\"\\]+)")
# A name followed by a separator. Only the name and the separator are consumed, so a candidate whose name is not secret
# never hides a later pair (``foo=password=x``). ``-D`` is a JVM system property prefix, not part of the name. S2c (OI-144
# (3)): a percent-escape of a NAME character (letter, digit, ``_ - .``) is part of the name (``%70assword``, ``api%5Fkey``)
# and the name is decoded for the word test only; an escape of a delimiter (``%3D``, ``%26``, ``%3F``, ...) never joins a
# name, and a name may start right after one (a percent-encoded query inside a parameter).
_FIRST_ESCAPE = r"%(?:[46][1-9A-Fa-f]|[57][0-9Aa]|5[Ff])"
_NAME_ESCAPE = r"%(?:[46][1-9A-Fa-f]|[57][0-9Aa]|5[Ff]|2[DdEe]|3[0-9])"
_NAME_SEPARATOR = re.compile(
    r"(?:(?<![A-Za-z0-9_.])|(?<=%2[0-9A-Fa-f])|(?<=%3[A-Fa-f]))(?:(?<=-)D)?"
    rf"(?P<name>(?:[A-Za-z_]|{_FIRST_ESCAPE})(?:[A-Za-z0-9_.\-]|{_NAME_ESCAPE})*)"
    r"(?:\\?[\"'])?[ \t]*(?:[:=]|%3[DdAa])[ \t]*")
_VALUE = re.compile(
    r"(?P<open>\\\")(?:(?!\\\").)+(?P<close>\\\")"          # an escaped double-quoted value (JSON inside a JSON string)
    r"|(?P<open2>\")(?:[^\"\\\n]|\\.)+(?P<close2>\")"       # a double-quoted value
    r"|(?P<open3>')[^'\n]+(?P<close3>')"                    # a single-quoted value
    r"|[^\s'\"\\&;,]+")                                     # a bare value
# Fixed-prefix credential formats their issuers document (closed table, tripwire-tested; S2c added the second group,
# OI-144 (2)). Each prefix is vendor-specific and each body has the issued length/alphabet, so a kebab or snake
# identifier that merely starts alike (``sk-learn-...``, ``npm_config_registry``, ``hf_hub_download``) stays readable.
_SHAPED_SECRETS = (
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                    # AWS access key id
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),                 # OpenAI-style API key
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),          # GitHub classic token prefixes (ghp_/gho_/ghu_/ghs_/ghr_)
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),        # GitHub fine-grained token
    re.compile(r"\bsk-(?:proj|svcacct|admin|ant)-[A-Za-z0-9_\-]{20,}"),  # OpenAI project/service/admin, Anthropic keys
    re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"),          # Slack bot/user/app/refresh/session tokens
    re.compile(r"\bglpat-[A-Za-z0-9_\-]{20,}"),              # GitLab personal access token
    re.compile(r"\bnpm_[A-Za-z0-9]{36}\b"),                 # npm access token
    re.compile(r"\b[rs]k_(?:live|test)_[A-Za-z0-9]{20,}"),   # Stripe secret / restricted key
    re.compile(r"\bAIza[0-9A-Za-z_\-]{35}(?![0-9A-Za-z_\-])"),  # Google API key
    re.compile(r"\bhf_[A-Za-z0-9]{34}\b"),                  # Hugging Face access token
)
_JSON_SEPARATORS = (None, (",", ":"))                         # json.dumps default, and the compact form


def _words(key: str) -> List[str]:
    return [w for w in _WORD_SPLIT.split(_CAMEL.sub(r"\1 \2", key).lower()) if w]


def _is_compound_secret(name: str) -> bool:
    """A compound ALL-CAPS name without separators ending in a ``_COMPOUND_SUFFIXES`` word (``PGPASSWORD``)."""
    return bool(_COMPOUND_NAME.fullmatch(name)) and any(name.endswith(suffix) for suffix in _COMPOUND_SUFFIXES)


def is_secret_key(key: Any) -> bool:
    """True when ``key`` names a secret-bearing value, by WORD: the last word is a credential word or the last two
    words form one (``api_key``, ``Auth-Token``, ``dbPassword``, ``client_secret``, ``ssh_key``); ``max_tokens`` is not.
    A compound ALL-CAPS name ending in a long secret word (``PGPASSWORD``, ``GITHUBTOKEN``) is one too."""
    if not isinstance(key, str):
        return False
    # Both splits: camel segments (``dbPassword``) and the plain one (``PassWord`` is one word, not "pass word").
    for words in (_words(key), [w for w in _WORD_SPLIT.split(key.lower()) if w]):
        if words and (words[-1] in _SECRET_LAST_WORDS or "".join(words[-2:]) in _SECRET_LAST_PAIRS):
            return True
    return _is_compound_secret(key)


def _is_secret_name(name: str) -> bool:
    """A NAME in free text: the key rule, plus a one-word ``auth`` or the plural of a one-word secret name; but
    ``authorization`` only as the HTTP header name, spelled as a header is (``Authorization``, ``authorization``,
    ``Proxy-Authorization``): ``SOURCE_CHANGED_SINCE_AUTHORIZATION: '<path>'`` is a reason code and ``NEEDS YOUR
    AUTHORIZATION: the change`` is prose."""
    words = _words(name)
    if not words:
        return False
    if len(words) == 1:
        word = words[0]
        if word in _TEXT_ONLY_NAMES:
            return True
        forms = {word, word[:-1]} if word.endswith("s") else {word}
    else:
        forms = {name}
    if "authorization" in forms or words[-1] == "authorization":
        return name in _AUTHORIZATION_HEADER_NAMES
    # The forms are lower-cased; the compound rule reads the name as written (``PGPASSWORD``, never ``MAXTOKENS`` minus
    # its plural ``s``).
    return any(is_secret_key(form) for form in forms) or _is_compound_secret(name)


def _hidden_value(value: str) -> str:
    """``value`` (one matched value token) hidden, its quotes kept."""
    for opening in ("\\\"", "\"", "'"):
        if len(value) >= 2 * len(opening) and value.startswith(opening) and value.endswith(opening):
            return f"{opening}{REDACTED}{opening}"
    return REDACTED


def _user_value(value: str) -> str:
    """``user:password`` -> ``user:***REDACTED***`` (a value without a colon carries no password and is kept)."""
    user, colon, password = value.partition(":")
    return f"{user}:{REDACTED}" if colon and password else value


def _flag_value(match: Match) -> str:
    name, value = match.group("name"), match.group("value")
    if name.lower() in _USER_FLAGS:
        hidden = _user_value(value)
    elif _is_secret_name(name):
        hidden = _hidden_value(value)
    else:
        return match.group(0)
    return f"{match.group('flag')}{match.group('gap')}{hidden}"


def _redact_pairs(text: str) -> str:
    """Every ``name<sep>value`` whose name is secret-bearing, value hidden (quotes kept)."""
    out: List[str] = []
    position = 0
    for match in _NAME_SEPARATOR.finditer(text):
        name = match.group("name")
        if match.start() < position or not _is_secret_name(unquote(name) if "%" in name else name):
            continue
        value = _VALUE.match(text, match.end())
        if value is None:
            continue
        out.append(text[position:value.start()])
        out.append(_hidden_value(value.group(0)))
        position = value.end()
    out.append(text[position:])
    return "".join(out)


def _authorization(match: Match) -> str:
    hidden = f"{REDACTED} {REDACTED}" if match.group("scheme") else REDACTED
    return f"{match.group('head')}{hidden}"


def _redact_json_document(text: str) -> Optional[str]:
    """The redacted text of a JSON document written exactly as ``json.dumps`` writes it (default or compact
    separators), so its non-secret bytes are unchanged; None when ``text`` is not such a document."""
    if text[0] not in "{[\"":
        return None
    try:
        parsed = json.loads(text)
    except ValueError:
        return None
    for separators in _JSON_SEPARATORS:
        if json.dumps(parsed, separators=separators) == text:
            return json.dumps(redact_value(parsed), separators=separators)
    return None


def redact_text(text: str) -> str:
    """``text`` with every secret-shaped value replaced by ``REDACTED`` (see the module docstring). A new string;
    a non-string is returned unchanged."""
    if not isinstance(text, str) or not text:
        return text
    document = _redact_json_document(text)
    if document is not None:
        return document
    redacted = _PRIVATE_KEY_PEM.sub(lambda m: f"-----BEGIN {m.group('label')}-----{REDACTED}{m.group('end')}", text)
    redacted = _URL_USERINFO.sub(lambda m: f"{m.group(1)}{REDACTED}@", redacted)
    if "%" in redacted:  # the encoded form needs '%3A%2F%2F' (OI-60: a substring test instead of a regex scan of every text)
        redacted = _ENCODED_URL_USERINFO.sub(lambda m: f"{m.group('scheme')}{REDACTED}{m.group('at')}", redacted)
    redacted = _SCHEME_RELATIVE_USERINFO.sub(f"//{REDACTED}@", redacted)
    # Scheme credentials before the header and the pairs: "Authorization: Bearer x" must hide x, not only the word Bearer.
    redacted = _BEARER.sub(f"Bearer {REDACTED}", redacted)
    redacted = _BASIC.sub(f"Basic {REDACTED}", redacted)
    redacted = _AUTHORIZATION_HEADER.sub(_authorization, redacted)
    redacted = _COOKIE_HEADER.sub(lambda m: f"{m.group(1)}{REDACTED}", redacted)
    redacted = _FLAG_VALUE.sub(_flag_value, redacted)
    redacted = _redact_pairs(redacted)
    for pattern in _SHAPED_SECRETS:
        redacted = pattern.sub(REDACTED, redacted)
    return redacted


def safe_endpoint(url: str, unparseable: str = "(unparseable endpoint)") -> str:
    """An endpoint (or any ``scheme://`` URL) as it may be SHOWN: scheme, host, port and path, with any ``user:password@``,
    query and fragment dropped; ``unparseable`` when the URL cannot be split (an out-of-range or non-numeric port).
    The ONE display rule (S2c, OI-145: it had a second copy in the skill-source display). Not the identity canonicalizer
    ``core.model_runtime.endpoint_identity`` (digest input: it also lower-cases and drops a trailing '/'; kept apart)."""
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        netloc = f"{host}:{parts.port}" if parts.port else host
        return urlunsplit((parts.scheme, netloc, parts.path, "", ""))
    except ValueError:
        return unparseable


def _hide(value: Any) -> Any:
    """A secret-bearing value: every non-empty leaf hidden, the structure (dict keys, list length) kept."""
    if isinstance(value, dict):
        return {k: _hide(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_hide(v) for v in value]
    return REDACTED if value else value


def _flag_name(item: Any) -> Optional[str]:
    """The name of a flag element (``--token`` -> ``token``), None for anything else (a ``--name=value`` element is
    free text)."""
    if isinstance(item, str) and item.startswith("-") and "=" not in item:
        name = item.lstrip("-")
        if name and len(item) - len(name) <= 2 and name[0].isalpha():
            return name
    return None


def _redact_list(items: Any) -> List[Any]:
    out: List[Any] = []
    flag: Optional[str] = None
    for item in items:
        if flag is not None and isinstance(item, str) and item and not item.startswith("-"):
            if flag.lower() in _USER_FLAGS:
                out.append(redact_text(_user_value(item)))
            elif _is_secret_name(flag):
                out.append(REDACTED)
            else:
                out.append(redact_value(item))
        else:
            out.append(redact_value(item))
        flag = _flag_name(item)
    return out


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
        return _redact_list(value)
    if isinstance(value, str):
        return redact_text(value)
    return value


__all__ = ("REDACTED", "is_secret_key", "redact_text", "redact_value", "safe_endpoint")
