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
from typing import Any, Dict, Iterator, List, Optional

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
    """A record or manifest of a schema this reader does not support."""


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
        if data.get("schema") not in model.SUPPORTED_SCHEMAS:
            raise UnsupportedEvidenceSchema(f"manifest schema {data.get('schema')!r}")
        return data

    def seal(self) -> Optional[Dict[str, Any]]:
        path = os.path.join(self.directory, _SEAL)
        if not os.path.isfile(path):
            return None
        with open(path, "rb") as handle:
            return json.loads(handle.read())

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
            if record.get("schema") not in model.SUPPORTED_SCHEMAS:
                raise UnsupportedEvidenceSchema(f"record schema {record.get('schema')!r}")
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

    def verify(self) -> Verification:
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
            if record.get("schema") not in model.SUPPORTED_SCHEMAS:
                raise UnsupportedEvidenceSchema(f"record schema {record.get('schema')!r}")
            if record.get("seq") != count + 1 or record.get("prev") != prev:
                return Verification(self.run_id, CHAIN_BROKEN, count, False, broken_seq=count + 1,
                                    detail=f"seq/prev does not follow record {count}")
            if model.canonical_bytes(record) != raw:
                return Verification(self.run_id, CHAIN_BROKEN, count, False, broken_seq=count + 1,
                                    detail="record bytes are not canonical")
            prev, count = model.digest(raw), count + 1
            blob_refs.extend(record.get("blobs", {}).values())
        seal = self.seal()
        if tail:
            return Verification(self.run_id, TRUNCATED_TAIL, count, seal is not None, seal=seal,
                                detail=f"{len(tail)} bytes of a partial record after seq {count}")
        problems = []
        for ref in dict.fromkeys(blob_refs):
            try:
                self.blob(ref)
            except BlobCorrupt as error:
                problems.append(str(error))
        if problems:
            return Verification(self.run_id, BLOB_CORRUPT, count, seal is not None, seal=seal,
                                blob_problems=problems)
        if seal is None:
            return Verification(self.run_id, UNSEALED, count, False)
        if seal.get("final_seq") != count or seal.get("head_digest") != prev:
            return Verification(self.run_id, SEAL_MISMATCH, count, True, seal=seal,
                                detail=f"seal names seq {seal.get('final_seq')}, chain head is seq {count}")
        return Verification(self.run_id, VERIFIED, count, True, seal=seal)


def list_runs(state_dir: str) -> List[str]:
    root = store_root(state_dir)
    if not os.path.isdir(root):
        return []
    return sorted(name for name in os.listdir(root)
                  if os.path.isfile(os.path.join(root, name, _MANIFEST)))


def open_run(state_dir: str, run_id: str) -> EvidenceRun:
    return EvidenceRun(state_dir, run_id)
