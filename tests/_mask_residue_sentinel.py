"""CERT-TMP-MASK-RESIDUE-EXCLUSIVE-001: a recording-only sentinel for ``kriya-authority-mask-*`` scratch directories.

``kriya.tools.containment_oci.authority_state_mask_args`` is the only creator of these directories. It returns a cleanup callable that the
caller of ``prepare()`` must invoke; the PRD-034 differential measured two empty directories per exclusive-shard replay whose creating test could
not be attributed (the other four were a product defect, closed). This sentinel makes the NEXT certification run say which test leaves one.

It OBSERVES and never acts: it never removes, creates or modifies a directory, never changes a test's outcome and never fails certification. A
failed write is counted and the run continues; only ``OSError`` is absorbed, so a coding error in this module still fails loudly (the same
contract as ``_certification_progress.ProgressWriter``).

Attribution is exact only where tests run one at a time inside one process with one ``TMPDIR`` - which is how every certification shard runs. The
record names the test during which a directory first appeared and whether it was still there when the session finished (a directory a later test
cleans up is recorded as transient, not as residue).
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Callable, Dict, Set

PREFIX = "kriya-authority-mask-"
RECORD_FILE = "mask-residue.jsonl"


class MaskResidueSentinel:
    def __init__(self, directory: str, root: str, *, clock: Callable[[], float] = time.time) -> None:
        self._path = os.path.join(directory, RECORD_FILE)
        self._root = root
        self._clock = clock
        self._before: Set[str] = set()
        self._seen: Dict[str, Dict[str, Any]] = {}
        self.write_errors = 0

    def _listing(self) -> Set[str]:
        try:
            return {name for name in os.listdir(self._root) if name.startswith(PREFIX)}
        except OSError:
            return set()

    def _describe(self, name: str) -> Dict[str, Any]:
        path = os.path.join(self._root, name)
        try:
            empty = not os.listdir(path)
            mode = oct(os.stat(path).st_mode & 0o777)
        except OSError:
            empty, mode = None, None
        return {"path": path, "empty": empty, "mode": mode}

    def _append(self, record: Dict[str, Any]) -> None:
        try:
            with open(self._path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True) + "\n")
        except OSError:
            self.write_errors += 1

    def start(self, nodeid: str) -> None:  # pylint: disable=unused-argument
        self._before = self._listing()

    def end(self, nodeid: str) -> None:
        """Called after the test's teardown: every matching directory that appeared during this test and is still there is recorded once."""
        appeared = self._listing() - self._before - set(self._seen)
        for name in sorted(appeared):
            record = {"event": "appeared", "node": nodeid, "at": self._clock(), "name": name, **self._describe(name)}
            self._seen[name] = record
            self._append(record)

    def finish(self) -> None:
        """Session end: say, for every directory ever recorded, whether it survived to the end."""
        remaining = self._listing()
        for name, record in sorted(self._seen.items()):
            self._append({"event": "final", "name": name, "node": record["node"], "path": record["path"],
                          "present_at_finish": name in remaining, "at": self._clock()})
        self._append({"event": "summary", "appeared": len(self._seen), "present_at_finish": len(set(self._seen) & remaining),
                      "write_errors": self.write_errors, "root": self._root, "at": self._clock()})
