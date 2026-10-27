"""S8 (done): the guards of the S8 record (tests/containment_s8_list.py, tests/containment_census.py, tests/containment_h_ledger.py).

S8 deleted ONLY unreachable project-controlled host-execution code and tests. After it:
- the ONLY production readers of the decision's ``contained`` flag are the retained record / identity / report readers, so a new host branch cannot appear unnoticed;
- every recorded S8 site is either DELETED (it no longer reads the flag) or HELD with a written reason; trusted host infrastructure never appears on the record;
- the test half is gone: no test is classified H, no test carries ``legacy_host_mcp``, and none of the recorded ids is collected any more;
- the whole record is digest-pinned, so changing it is a reviewed change."""
import ast
import hashlib
from pathlib import Path

import containment_census as cc
import containment_h_ledger as ledger
import containment_s8_list as s8

ROOT = Path(__file__).resolve().parent.parent

# Update ONLY with an owner-reviewed change to the S8 record (the digest of the sorted code sites with their outcomes + the recorded deleted test ids)
S8_LIST_DIGEST = "bc134e70041ae58adef321a1f55cd0515abc43f49ea1279274933b650df3a9ab"


def _qualnames(tree):
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}

    def qualname(node):
        names, current = [], node
        while current in parents:
            current = parents[current]
            if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.append(current.name)
        return ".".join(reversed(names)) or "<module>"

    return qualname


def _scan() -> dict:
    """{ "file::qualified function": [lines] } for every read of ``.contained`` / ``._contained`` in kriya/ and plugins/ (the owner's own module excluded)."""
    found = {}
    for root in ("kriya", "plugins"):
        for path in sorted((ROOT / root).rglob("*.py")):
            relpath = path.relative_to(ROOT).as_posix()
            if relpath.startswith("kriya/tools/containment"):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            qualname = _qualnames(tree)
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr in ("contained", "_contained") and isinstance(node.ctx, ast.Load):
                    found.setdefault(f"{relpath}::{qualname(node)}", []).append(node.lineno)
    return found


def _defines(site: str) -> bool:
    relpath, _, qualified = site.partition("::")
    path = ROOT / relpath
    if not path.is_file():
        return False
    tree = ast.parse(path.read_text(encoding="utf-8"))
    qualname = _qualnames(tree)
    return any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (f"{qualname(node)}.{node.name}" if qualname(node) != "<module>" else node.name) == qualified
               for node in ast.walk(tree))


def test_only_the_retained_record_readers_read_the_contained_flag():
    scan = set(_scan())
    assert scan == set(s8.RETAINED), (f"a production function reads the contained flag again (a host branch is back, or a new record reader must be classified RETAINED): "
                                      f"{sorted(scan - set(s8.RETAINED))}; retained but no longer reading it: {sorted(set(s8.RETAINED) - scan)}")


def test_every_recorded_site_is_deleted_or_held_with_a_reason_and_the_retained_set_is_disjoint():
    assert not set(s8.S8_CODE) & set(s8.RETAINED)
    scan = set(_scan())
    for site, entry in s8.S8_CODE.items():
        assert entry.outcome in ("DELETED", "HELD"), site
        assert site not in scan, f"{site}: a DELETED/HELD S8 site reads the contained flag again"
        if entry.outcome == "HELD":
            assert len(s8.HELD_NOTES.get(site, "")) >= 80 and _defines(site), f"{site}: a held site needs a written reason and must still exist"
    assert set(s8.HELD_NOTES) == {site for site, entry in s8.S8_CODE.items() if entry.outcome == "HELD"}
    for site in s8.RETAINED:
        assert _defines(site), f"{site}: no such function"
    for orphan in s8.ORPHANS:
        assert _defines(orphan) or "::" in orphan and (ROOT / orphan.partition("::")[0]).is_file(), f"{orphan}: orphan record is stale"


def test_trusted_and_hardened_host_infrastructure_is_never_on_the_s8_record():
    for site in (*s8.S8_CODE, *s8.RETAINED):
        function = site.partition("::")[2]
        assert not any(name in function.split(".") for name in s8.NEVER_S8), f"{site} is trusted host infrastructure, not S8 scope"


def test_the_test_half_is_gone_no_test_is_legacy_host_and_the_recorded_plain_ids_are_not_collected_any_more():
    assert cc.legacy_host_ids() == ()
    assert not any(cc.classify(test_id) == ("H", "H") for test_id in cc.EXCEPTIONS)
    assert len(ledger.LEDGER) == 54 and len(ledger.MARKED_LEDGER) == 3        # the record of what S8 deleted
    for test_id in (*ledger.LEDGER, *ledger.MARKED_LEDGER):
        path, _, name = test_id.partition("::")
        if path in ledger.FILES_DELETED_WITH_DEAD_CODE:
            assert not (ROOT / path).exists(), f"{path} is back"
            continue
        if "[" in name:
            continue                  # a parameter set: the function may legitimately remain with its other parameters
        tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
        assert name not in {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}, f"{test_id}: a deleted legacy-host test is back"


def test_no_test_carries_the_retired_legacy_host_mcp_marker():
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert not any("legacy_host_mcp" in ast.dump(decorator) for decorator in node.decorator_list), f"{path.name}::{node.name}"


def test_the_whole_s8_record_is_digest_pinned():
    material = "\n".join([*(f"code {site} {entry.outcome}" for site, entry in sorted(s8.S8_CODE.items())), *(f"test {test_id}" for test_id in sorted(ledger.LEDGER)),
                          *(f"marked {test_id}" for test_id in sorted(ledger.MARKED_LEDGER))])
    assert hashlib.sha256(material.encode("utf-8")).hexdigest() == S8_LIST_DIGEST


def test_every_deleted_orphan_has_a_negative_proof_that_exists():
    """S8-ORPHAN-HOST-CODE-001: the orphans are recorded as deleted, each with the test that pins its absence; the list of live orphans is empty."""
    assert s8.ORPHANS == {}
    assert len(s8.ORPHANS_DELETED) == 5
    for site, (why, proof) in s8.ORPHANS_DELETED.items():
        assert len(why) >= 40, site
        path, _, name = proof.partition("::")
        tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
        assert name in {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}, f"{site}: its proof {proof} does not exist"
