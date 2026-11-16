"""Skill path authority (C-04, S4): what a skills path IS, decided once, for the Skills owner's one write entry and its trust decision.

Two facts live here and nowhere else:

  * the CLASS of a skills path (`SkillPathClass`): the closed set the write entry (`SkillEngine.write_skill_path`) authorizes against;
  * whether a skill folder in the installation's skills directory is GENUINELY SHIPPED (`is_shipped_skill`): a positive answer from the
    canonical metadata the product already has, never from the folder's location. A wheel installation carries the distribution's
    ``RECORD`` (every shipped file with its SHA-256); a source checkout carries the product repository's ``HEAD`` tree (the release check,
    `kriya.distribution`, already requires a wheel's skills to equal the tracked set). A folder or a file a run left there matches
    neither and is therefore not shipped content: it is loaded untrusted. No canonical metadata (an unpacked source archive that is no
    repository) means nothing is shipped: fail closed;

An EXPLICIT skills root outside the project is the operator's: configuring its path gives a run no write authority there (owner ruling
2026-11-16). A run reads it; only the person's own `kriya skills ...` command writes it.
"""
import base64
import hashlib
import importlib.metadata
import logging
import os
from enum import Enum
from typing import Dict, List, Optional

from kriya.platform.filesystem_semantics import PathRelation, path_relation
from kriya.skills import run_shadow

logger = logging.getLogger(__name__)


class SkillPathClass(str, Enum):
    """The closed set of path classes of a skills path, as one project's run sees it."""

    PROJECT_MUTABLE = "project_mutable"                         # the project's overlay, or a skills root inside the project's repository
    PRODUCT_SHIPPED_READ_ONLY = "product_shipped_read_only"     # the installation's skills directory: never written by a run
    EXPLICIT_SHARED = "explicit_shared"                         # a configured root outside the project: the operator's, never written by a run
    FOREIGN_UNKNOWN = "foreign_unknown"                         # under no skills root of this engine, or a relation that cannot be established


SKILL_WRITE_SHIPPED_READ_ONLY = "SKILL_WRITE_SHIPPED_READ_ONLY"
SKILL_WRITE_EXPLICIT_ROOT_OPERATOR_ONLY = "SKILL_WRITE_EXPLICIT_ROOT_OPERATOR_ONLY"
SKILL_WRITE_PATH_UNCLASSIFIED = "SKILL_WRITE_PATH_UNCLASSIFIED"
SKILL_WRITE_RUN_TRANSACTION_UNAVAILABLE = "SKILL_WRITE_RUN_TRANSACTION_UNAVAILABLE"
SKILL_WRITE_REASON_CODES = frozenset({
    SKILL_WRITE_SHIPPED_READ_ONLY, SKILL_WRITE_EXPLICIT_ROOT_OPERATOR_ONLY, SKILL_WRITE_PATH_UNCLASSIFIED,
    SKILL_WRITE_RUN_TRANSACTION_UNAVAILABLE,
})

# -- genuinely shipped content ------------------------------------------------------------------------------------------------------------

_SHIPPED_IDENTITY: Dict[str, Optional[Dict[str, str]]] = {}
_RECORD, _TREE = "sha256:", "git:"


def reset_shipped_identity_cache() -> None:
    """Forgets every shipped identity read so far (a test that builds an installation, or a process that outlives a product upgrade)."""
    _SHIPPED_IDENTITY.clear()


def shipped_identity(skills_dir: str) -> Optional[Dict[str, str]]:
    """``{posix path relative to skills_dir: canonical content identity}`` of every file the product ships in ``skills_dir``, or None when
    no canonical metadata describes that directory. Read once per process and directory: a later change of the metadata is not seen, so a
    file changed afterwards can only stop matching (fail closed)."""
    key = os.path.realpath(skills_dir)
    if key not in _SHIPPED_IDENTITY:
        _SHIPPED_IDENTITY[key] = _from_distribution_record(key) or _from_source_checkout(key)
    return _SHIPPED_IDENTITY[key]


def _from_distribution_record(skills_dir: str) -> Optional[Dict[str, str]]:
    """The installed distribution's RECORD entries that are files of ``skills_dir`` (a wheel installation)."""
    try:
        distribution = importlib.metadata.distribution("kriya")
        files = distribution.files or []
    except Exception as error:  # noqa: BLE001 - no readable distribution metadata is "no RECORD", never an error of the caller
        logger.debug(f"No distribution RECORD for the shipped skills: {error}")
        return None
    entries: Dict[str, str] = {}
    for item in files:
        if item.hash is None or item.hash.mode != "sha256" or not item.parts or item.parts[0] != "skills":
            continue
        located = os.path.realpath(str(distribution.locate_file(item)))
        if path_relation(skills_dir, located) is PathRelation.WITHIN:
            entries[os.path.relpath(located, skills_dir).replace(os.sep, "/")] = _RECORD + item.hash.value
    return entries or None


def _from_source_checkout(skills_dir: str) -> Optional[Dict[str, str]]:
    """The blobs of ``skills_dir`` in the HEAD tree of the product's own repository (a source checkout): only when the installation
    directory IS that repository's work tree, so a site-packages directory inside some other repository proves nothing."""
    install_dir = os.path.dirname(skills_dir)
    try:
        if run_shadow._toplevel(install_dir) != os.path.realpath(install_dir):
            return None
        listing = run_shadow._git(install_dir, "ls-tree", "-r", "-z", "HEAD", "--", os.path.basename(skills_dir))
    except Exception as error:  # noqa: BLE001 - git that cannot answer is "no canonical metadata"
        logger.debug(f"No source-checkout identity for the shipped skills: {error}")
        return None
    if listing.returncode != 0:
        return None
    entries: Dict[str, str] = {}
    prefix = os.path.basename(skills_dir) + "/"
    for record in listing.stdout.split("\0"):
        meta, _, name = record.partition("\t")
        fields = meta.split()
        if len(fields) != 3 or fields[1] != "blob" or fields[0] == "120000" or not name.startswith(prefix):
            continue
        entries[name[len(prefix):]] = _TREE + fields[2]
    return entries or None


def _content_identity(path: str, expected: str) -> Optional[str]:
    """The identity of the file's bytes in the form ``expected`` is written in; None for anything that is not a plain readable file."""
    if os.path.islink(path) or not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError:
        return None
    if expected.startswith(_RECORD):
        return _RECORD + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")
    if expected.startswith(_TREE):
        algorithm = hashlib.sha256 if len(expected) - len(_TREE) == 64 else hashlib.sha1
        return _TREE + algorithm(b"blob %d\0" % len(data) + data).hexdigest()  # noqa: S324 - git's own object id, an identity and no security digest
    return None


def loaded_skill_files(folder: str) -> List[str]:
    """The files of a skill folder the loader turns into skill content (`SkillEngine.discover_and_load`): the ones whose bytes decide trust."""
    files = [os.path.join(folder, name) for name in ("skill.yaml", "instructions.md", "rules.txt", "rule_provenance.json")
             if os.path.lexists(os.path.join(folder, name))]
    examples = os.path.join(folder, "examples")
    if os.path.isdir(examples):
        files.extend(os.path.join(examples, name) for name in sorted(os.listdir(examples))
                     if not name.startswith(".") and not os.path.isdir(os.path.join(examples, name)))
    return files


def is_shipped_skill(skills_dir: str, folder: str) -> bool:
    """Whether the skill in ``folder`` (inside the installation's ``skills_dir``) is genuinely shipped product content: every file the loader
    reads from it is a shipped file with exactly the shipped bytes. A folder a run created, a shipped skill a run appended to, and an
    installation with no canonical metadata are all False."""
    identity = shipped_identity(skills_dir)
    if not identity or not os.path.isfile(os.path.join(folder, "skill.yaml")):
        return False
    root = os.path.realpath(skills_dir)
    for path in loaded_skill_files(folder):
        expected = identity.get(os.path.relpath(os.path.join(os.path.realpath(os.path.dirname(path)), os.path.basename(path)), root).replace(os.sep, "/"))
        if expected is None or _content_identity(path, expected) != expected:
            return False
    return True

