"""S7 -> S8: the S8 list is FROZEN and exact (tests/containment_s8_list.py, tests/containment_census.py).

S8 deletes ONLY unreachable project-controlled host-execution code and tests. This file makes the list a closed, reviewed set:
- every production function that reads the containment decision's ``contained`` flag is either on the S8 list or explicitly retained as a record / identity / report reader;
- the named extra sites exist; trusted host infrastructure never appears on the list;
- the test half is exactly the census H list, each with its replacement evidence (tests/test_s7_h_replacement_ledger.py);
- the whole list is digest-pinned, so adding or removing an entry is a reviewed change, not a drift."""
import ast
import hashlib
from pathlib import Path

import containment_census as cc
import containment_h_ledger as ledger
import containment_s8_list as s8

ROOT = Path(__file__).resolve().parent.parent

# Update ONLY with an owner-reviewed change to the S8 scope (the digest of the sorted code sites + test ids)
S8_LIST_DIGEST = "d0163381a442a35b8059e0f32582263e4a8a46bdf43d01851ba0490ab2d35d76"


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


def test_every_reader_of_the_contained_flag_is_on_the_s8_list_or_explicitly_retained():
    scan = set(_scan())
    listed = {site for site, entry in s8.S8_CODE.items() if entry.kind == "scan"} | set(s8.RETAINED)
    assert scan == listed, (f"unlisted readers (classify them as S8_CODE or RETAINED): {sorted(scan - listed)}; "
                            f"listed but no longer reading the flag (the branch is gone or moved): {sorted(listed - scan)}")


def test_the_s8_code_and_retained_sets_are_disjoint_and_every_named_site_exists():
    assert not set(s8.S8_CODE) & set(s8.RETAINED)
    for site in (*s8.S8_CODE, *s8.RETAINED):
        assert _defines(site), f"{site}: no such function"


def test_trusted_and_hardened_host_infrastructure_is_never_on_the_s8_list():
    for site in (*s8.S8_CODE, *s8.RETAINED):
        function = site.partition("::")[2]
        assert not any(name in function.split(".") for name in s8.NEVER_S8), f"{site} is trusted host infrastructure, not S8 scope"


def test_the_s8_test_half_is_exactly_the_census_h_list_with_a_ledger_entry_each():
    assert set(ledger.LEDGER) == set(cc.legacy_host_ids())
    assert all(cc.classify(test_id) == ("H", "H") for test_id in cc.legacy_host_ids())


def test_the_still_marked_legacy_host_mcp_tests_are_exactly_the_marked_ledger():
    """The legacy_host_mcp tests that are not census H ids (they never consult the owner) are the second half of the S8 test list."""
    marked = set()
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any("legacy_host_mcp" in ast.dump(decorator) for decorator in node.decorator_list):
                marked.add(f"tests/{path.name}::{node.name}")
    assert marked - set(cc.legacy_host_ids()) == set(ledger.MARKED_LEDGER)
    assert not set(ledger.MARKED_LEDGER) & set(cc.legacy_host_ids())


def test_the_whole_s8_list_is_digest_pinned():
    material = "\n".join([*(f"code {site}" for site in sorted(s8.S8_CODE)), *(f"test {test_id}" for test_id in sorted(cc.legacy_host_ids())),
                          *(f"marked {test_id}" for test_id in sorted(ledger.MARKED_LEDGER))])
    assert hashlib.sha256(material.encode("utf-8")).hexdigest() == S8_LIST_DIGEST
