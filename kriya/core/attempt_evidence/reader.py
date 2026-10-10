"""The stable read API of the attempt evidence store (invariant I-1).

Consumers - the CLI, analyzers, benchmark scripts, a GUI - use this module.
They never parse the store's files: the layout may change, and this API plus
the record schema are the versioned contract.

Production code never imports this module (design §9.1; structural test):
the recorder is write-only from Kriya's decisions.

Verification (design §6.3) is read-only: it never repairs, truncates or
rewrites anything. Outcomes:

- ``VERIFIED``: chain intact, every referenced blob intact, seal matches;
- ``UNSEALED``: chain intact but no seal (crashed or still running) - the
  reader never guesses which;
- ``TRUNCATED_TAIL``: the last line is partial (reported, never repaired);
- ``CHAIN_BROKEN``: the first record (``broken_seq``) whose ``prev``/``seq``
  does not follow, or that is not a valid record;
- ``BLOB_CORRUPT``: a referenced blob is missing or re-hashes differently;
- ``SEAL_MISMATCH``: a seal exists but does not name the chain's head.
"""
from __future__ import annotations

import gzip
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional, Tuple

from kriya.core.attempt_evidence import model
from kriya.core.attempt_evidence.writer import _BLOBS, _MANIFEST, _RECORDS, _SEAL, run_directory, store_root

VERIFIED = "VERIFIED"
UNSEALED = "UNSEALED"
TRUNCATED_TAIL = "TRUNCATED_TAIL"
CHAIN_BROKEN = "CHAIN_BROKEN"
BLOB_CORRUPT = "BLOB_CORRUPT"
SEAL_MISMATCH = "SEAL_MISMATCH"
NOT_FOUND = "NOT_FOUND"


class UnsupportedEvidenceSchema(ValueError):
    """A record or manifest of a schema this reader does not support. ``schema_seen`` is the value found, verbatim."""

    def __init__(self, message: str, schema_seen: Any = None) -> None:
        super().__init__(message)
        self.schema_seen = schema_seen


class BlobCorrupt(ValueError):
    """A blob is missing or its bytes do not match the digest that names it."""


@dataclass(frozen=True)
class Verification:
    run_id: str
    status: str
    record_count: int
    sealed: bool
    broken_seq: Optional[int] = None
    detail: str = ""
    seal: Optional[Dict[str, Any]] = None
    blob_problems: List[str] = field(default_factory=list)
    # Digest of the last complete record verified in this pass (``prev`` of the next record); None when the chain
    # did not verify up to a head (NOT_FOUND, CHAIN_BROKEN).
    head_digest: Optional[str] = None


class EvidenceRun:
    """Read access to one run's store."""

    def __init__(self, state_dir: str, run_id: str) -> None:
        self.run_id = run_id
        self.directory = run_directory(state_dir, run_id)

    @property
    def exists(self) -> bool:
        return os.path.isfile(os.path.join(self.directory, _MANIFEST))

    def manifest(self) -> Dict[str, Any]:
        with open(os.path.join(self.directory, _MANIFEST), "rb") as handle:
            data = json.loads(handle.read())
        if not isinstance(data, dict):
            raise ValueError("manifest is not a JSON object")
        if data.get("schema") not in model.SUPPORTED_SCHEMAS:
            raise UnsupportedEvidenceSchema(f"manifest schema {data.get('schema')!r}", data.get("schema"))
        return data

    def seal(self) -> Optional[Dict[str, Any]]:
        path = os.path.join(self.directory, _SEAL)
        if not os.path.isfile(path):
            return None
        with open(path, "rb") as handle:
            seal = json.loads(handle.read())
        if not isinstance(seal, dict):
            raise ValueError("seal is not a JSON object")
        return seal

    def measure(self) -> Tuple[int, int, int]:
        """(complete record lines, bytes of every file under the run directory, blob files): a cheap count that verifies
        nothing. ``lstat`` only - symlinks are never followed - and nothing is created or touched."""
        lines = 0
        with open(os.path.join(self.directory, _RECORDS), "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                lines += chunk.count(b"\n")
        total = blobs = 0
        blob_root = os.path.join(self.directory, _BLOBS)
        for root, _dirs, files in os.walk(self.directory):
            for name in files:
                total += os.lstat(os.path.join(root, name)).st_size
                if root.startswith(blob_root) and name.endswith(".gz"):
                    blobs += 1
        return lines, total, blobs

    def _lines(self):
        path = os.path.join(self.directory, _RECORDS)
        with open(path, "rb") as handle:
            data = handle.read()
        lines = data.split(b"\n")
        complete, tail = lines[:-1], lines[-1]
        return complete, tail

    def records(self) -> Iterator[Dict[str, Any]]:
        """Every complete record, in seq order, schema-checked. A partial tail
        is not yielded (``verify`` reports it)."""
        complete, _tail = self._lines()
        for raw in complete:
            record = json.loads(raw)
            if not isinstance(record, dict):
                raise ValueError("record is not a JSON object")
            if record.get("schema") not in model.SUPPORTED_SCHEMAS:
                raise UnsupportedEvidenceSchema(f"record schema {record.get('schema')!r}", record.get("schema"))
            yield record

    def blob(self, ref: str) -> bytes:
        """The exact bytes a ``blobs`` reference names, re-hashed on read."""
        if not isinstance(ref, str) or not ref.startswith("sha256:"):
            raise BlobCorrupt(f"not a blob reference: {ref!r}")
        hexdigest = ref.split(":", 1)[1]
        path = os.path.join(self.directory, _BLOBS, hexdigest[:2], hexdigest + ".gz")
        try:
            with open(path, "rb") as handle:
                data = gzip.decompress(handle.read())
        except (OSError, EOFError) as error:
            raise BlobCorrupt(f"{ref}: {type(error).__name__}: {error}") from error
        if model.digest(data) != ref:
            raise BlobCorrupt(f"{ref}: content re-hashes to {model.digest(data)}")
        return data

    def verify(self, records_out: Optional[List[Dict[str, Any]]] = None) -> Verification:
        """Recompute the chain, blobs and seal. When ``records_out`` is given, every complete record that passed the chain checks is
        appended to it in seq order (so a consumer that needs both verifies and reads in one pass over the file)."""
        if not self.exists:
            return Verification(self.run_id, NOT_FOUND, 0, False, detail="no store for this run")
        self.manifest()
        complete, tail = self._lines()
        prev, count = model.GENESIS_PREV, 0
        blob_refs: List[str] = []
        for raw in complete:
            try:
                record = json.loads(raw)
            except ValueError:
                return Verification(self.run_id, CHAIN_BROKEN, count, False, broken_seq=count + 1,
                                    detail="record is not valid JSON")
            if not isinstance(record, dict):
                return Verification(self.run_id, CHAIN_BROKEN, count, False, broken_seq=count + 1,
                                    detail="record is not a JSON object")
            if record.get("schema") not in model.SUPPORTED_SCHEMAS:
                raise UnsupportedEvidenceSchema(f"record schema {record.get('schema')!r}", record.get("schema"))
            if record.get("seq") != count + 1 or record.get("prev") != prev:
                return Verification(self.run_id, CHAIN_BROKEN, count, False, broken_seq=count + 1,
                                    detail=f"seq/prev does not follow record {count}")
            if model.canonical_bytes(record) != raw:
                return Verification(self.run_id, CHAIN_BROKEN, count, False, broken_seq=count + 1,
                                    detail="record bytes are not canonical")
            prev, count = model.digest(raw), count + 1
            blob_refs.extend(record.get("blobs", {}).values())
            if records_out is not None:
                records_out.append(record)
        seal = self.seal()
        if tail:
            return Verification(self.run_id, TRUNCATED_TAIL, count, seal is not None, seal=seal,
                                detail=f"{len(tail)} bytes of a partial record after seq {count}", head_digest=prev)
        problems = []
        for ref in dict.fromkeys(blob_refs):
            try:
                self.blob(ref)
            except BlobCorrupt as error:
                problems.append(str(error))
        if problems:
            return Verification(self.run_id, BLOB_CORRUPT, count, seal is not None, seal=seal,
                                blob_problems=problems, head_digest=prev)
        if seal is None:
            return Verification(self.run_id, UNSEALED, count, False, head_digest=prev)
        if seal.get("final_seq") != count or seal.get("head_digest") != prev:
            return Verification(self.run_id, SEAL_MISMATCH, count, True, seal=seal,
                                detail=f"seal names seq {seal.get('final_seq')}, chain head is seq {count}",
                                head_digest=prev)
        return Verification(self.run_id, VERIFIED, count, True, seal=seal, head_digest=prev)


# The role -> phase map the recorder's call scope applies (scope._ROLE_PHASES), copied here because consumers never import the
# writer side; tests/test_kup_evidence_records.py fails if the two ever differ.
ROLE_PHASES = {
    "planner": "planning", "planner_shadow": "planning", "architect": "architect", "reviewer": "review",
    "spec_compliance": "requirement_verification", "run_verifier": "requirement_verification",
    "localization": "localization", "milestone_planner": "milestone_planning",
}
PHASE_EXPLICIT = "explicit"
PHASE_FROM_ATTEMPT = "derived_from_attempt_scope"
PHASE_FROM_ROLE = "derived_from_role"
PHASE_FROM_ROLE_NAME = "role_name_fallback"


def explicit_phase_ranges(records: List[Dict[str, Any]]) -> List[Tuple[int, float]]:
    """Inclusive ``(opened_seq, closed_seq)`` ranges of explicit ``phase.opened`` .. ``phase.closed`` pairs of one chain (an unclosed
    phase runs to the end). No producer writes these today, so this is normally empty."""
    ranges: List[Tuple[int, float]] = []
    opened: Optional[int] = None
    for record in records:
        kind = record.get("kind")
        if kind == "phase.opened" and opened is None:
            opened = record["seq"]
        elif kind == "phase.closed" and opened is not None:
            ranges.append((opened, record["seq"]))
            opened = None
    if opened is not None:
        ranges.append((opened, float("inf")))
    return ranges


def phase_provenance(record: Dict[str, Any], ranges: List[Tuple[int, float]]) -> Optional[str]:
    """How a record's phase is known (computed here, never stored): ``explicit`` inside a recorded phase range, else
    ``derived_from_attempt_scope`` when it carries an attempt number, ``derived_from_role`` when its role is in the recorder's role
    map, ``role_name_fallback`` when the phase is an unmapped raw role name, and None when no phase was recorded at all."""
    seq = record.get("seq")
    if any(start <= seq <= end for start, end in ranges):
        return PHASE_EXPLICIT
    if record.get("attempt_number") is not None:
        return PHASE_FROM_ATTEMPT
    role = record.get("role")
    if role in ROLE_PHASES:
        return PHASE_FROM_ROLE
    if record.get("phase") is not None and role is not None:
        return PHASE_FROM_ROLE_NAME
    return None


def store_directory(state_dir: str) -> str:
    """The directory that holds every run's store under ``state_dir`` (reported, never opened, by consumers)."""
    return store_root(state_dir)


def list_runs(state_dir: str) -> List[str]:
    root = store_root(state_dir)
    if not os.path.isdir(root):
        return []
    return sorted(name for name in os.listdir(root)
                  if not name.startswith(".") and os.path.isfile(os.path.join(root, name, _MANIFEST)))


def newest_run(state_dir: str) -> Optional[str]:
    """The most recently created run store (by its manifest, written once at
    creation), or None."""
    root = store_root(state_dir)
    created = []
    for run_id in list_runs(state_dir):
        try:
            created.append((os.path.getmtime(os.path.join(root, run_id, _MANIFEST)), run_id))
        except OSError:
            continue
    return max(created)[1] if created else None


def open_run(state_dir: str, run_id: str) -> EvidenceRun:
    return EvidenceRun(state_dir, run_id)
