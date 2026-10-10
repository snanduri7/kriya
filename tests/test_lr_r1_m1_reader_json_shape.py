"""Reader hardening: a store file that is valid JSON but not an object (a record line ``[1]``, a manifest or seal ``[]``) is
reported by the reader's own vocabulary (CHAIN_BROKEN / ValueError), never an AttributeError that escapes ``verify`` or ``records``.
Found while building the KUP evidence read operations (Run Cockpit v1.1)."""
from __future__ import annotations

import os

import pytest
from _kup_evidence_fixtures import rewrite_record_line, state_dir, write_store

from kriya.core.attempt_evidence import reader


def test_a_non_object_record_line_is_chain_broken_at_its_seq():
    rewrite_record_line(write_store("h-rec"), 1, lambda _record: b"[1, 2]")
    result = reader.open_run(state_dir(), "h-rec").verify()
    assert result.status == reader.CHAIN_BROKEN and result.broken_seq == 2 and result.detail == "record is not a JSON object"


def test_records_refuses_a_non_object_line_with_a_value_error():
    rewrite_record_line(write_store("h-iter"), 0, lambda _record: b"7")
    with pytest.raises(ValueError, match="not a JSON object"):
        list(reader.open_run(state_dir(), "h-iter").records())


@pytest.mark.parametrize("name,call", [("manifest.json", lambda run: run.manifest()), ("seal.json", lambda run: run.seal())])
def test_a_non_object_manifest_or_seal_is_a_value_error(name, call):
    directory = write_store("h-" + name[:3])
    with open(os.path.join(directory, name), "wb") as handle:
        handle.write(b"[]")
    with pytest.raises(ValueError, match="not a JSON object"):
        call(reader.open_run(state_dir(), "h-" + name[:3]))
