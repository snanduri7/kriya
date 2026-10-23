"""D-001: the workspace AUTHORITY root (``kriya.context.workspace_identity.authority_root``) is consulted only by the modules that OWN a workspace-scoped
authority. A module outside this exact set that wants a state path must go through one of them; deriving it from the invoked directory (or calling the
resolver itself) would recreate the parallel authority D-001 removed. A new owner is a deliberate edit to this set."""
import ast
import pathlib

KRIYA = pathlib.Path(__file__).resolve().parents[2] / "kriya"

OWNERS = {
    "kriya/application/project.py": 1,        # kriya_directory_present
    "kriya/config/resolution.py": 1,          # kriya.yaml discovery + SEC-009's workspace
    "kriya/context/workspace_identity.py": 1,  # the identity hash (the legacy id searches from the SPELLING it was given, by find_repository_root)
    "kriya/control/checkpoint.py": 1,
    "kriya/control/control_store.py": 1,
    "kriya/control/human_decision_store.py": 1,  # D2 M1a: refuses an authority home that resolves inside the repository
    "kriya/control/ownership_audit.py": 1,    # D2 M4: the authority's checkpoint and milestone-sidecar directories audited for ownership
    "kriya/control/persistence.py": 2,        # the control dir + the legacy nested scan
    "kriya/control/retention.py": 1,
    "kriya/control/run_coordinator.py": 1,    # D2 M3: the repository whose tracked state the run-entry refusal judges
    "kriya/control/run_ownership.py": 1,
    "kriya/production_doctor.py": 1,
    "kriya/repository/commit.py": 1,
    "kriya/workflow/milestones.py": 1,
}


def _calls(root=KRIYA):
    found = {}
    for path in sorted(root.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", None)) == "authority_root":
                relative = path.relative_to(root.parent).as_posix()
                found[relative] = found.get(relative, 0) + 1
    return found


def test_only_the_workspace_authority_owners_call_the_authority_root_resolver():
    assert _calls() == OWNERS


def test_a_module_outside_the_owners_that_calls_the_resolver_is_detected(tmp_path):
    planted = tmp_path / "kriya" / "workflow"
    planted.mkdir(parents=True)
    (planted / "planted.py").write_text("from kriya.context.workspace_identity import authority_root\n\n\ndef state(path):\n    return authority_root(path) + '/.kriya'\n")
    (planted / "attribute_form.py").write_text("import kriya.context.workspace_identity as wi\n\n\ndef state(path):\n    return wi.authority_root(path)\n")
    found = _calls(tmp_path / "kriya")
    assert found == {"kriya/workflow/planted.py": 1, "kriya/workflow/attribute_form.py": 1}
    assert set(found) - set(OWNERS) == set(found)
