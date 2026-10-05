"""LR-R1-M1 invariant I-1 (design §6.7; test T15): the store's layout is
internal. Outside ``kriya/core/attempt_evidence/``, no code in ``kriya/``,
``scripts/`` or ``benchmarks/`` names the store's internal files together
with the store, or its blob paths - consumers use the reader API (or
``kriya evidence ... --json``)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_INTERNAL_FILES = ("records.jsonl", "seal.json", "manifest.json")
_STORE_NAMES = ("attempt-evidence", "attempt_evidence")


def layout_violations(text: str):
    found = []
    if "blobs/" in text:
        found.append("names blobs/")
    if any(store in text for store in _STORE_NAMES):
        found.extend(f"names {name} with the store" for name in _INTERNAL_FILES if name in text)
    return found


def _scanned_files():
    for base in ("kriya", "scripts", "benchmarks"):
        root = ROOT / base
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            rel = path.relative_to(ROOT).as_posix()
            if path.suffix not in (".py", ".sh") or rel.startswith("kriya/core/attempt_evidence/"):
                continue
            yield rel, path.read_text(encoding="utf-8", errors="replace")


def test_no_consumer_names_the_store_layout():
    violations = {rel: found for rel, text in _scanned_files() if (found := layout_violations(text))}
    assert violations == {}


def test_the_tripwire_catches_planted_layout_reads():
    """Negative control: each forbidden shape is caught; a mention of the
    store alone (a docstring, the reader API) is not."""
    assert layout_violations('open(os.path.join(state, "attempt-evidence", run, "records.jsonl"))')
    assert layout_violations("from kriya.core.attempt_evidence import scope\npath = 'seal.json'")
    assert layout_violations('gzip.open(f"{store}/blobs/{ref[:2]}/{ref}.gz")')
    assert not layout_violations("from kriya.core.attempt_evidence import reader\nreader.list_runs(state)")
    assert not layout_violations('"""one attempt-evidence call scope"""')


def test_the_scan_covers_the_consumer_trees():
    scanned = {rel.split("/", 1)[0] for rel, _text in _scanned_files()}
    assert {"kriya", "scripts", "benchmarks"} <= scanned
