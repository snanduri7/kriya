"""FILE-INTEGRITY-CONTRACT-001B: follow-up to FILE-INTEGRITY-CONTRACT-001.

P0: Planner prose is never repository mutation authority. A fenced block in
the Planner's plan under a file's heading must never become that file's
bytes; every repository byte comes from a parsed Developer response through
the authorized writer.
"""
import asyncio

from kriya.config import AppConfig
from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.workflow.workflow import WorkflowEngine

DEVELOPER_PROMPTS = ("You are the Kriya Developer Agent", "You are the Kriya File List Planner")

# A plan that "over-delivers": the README's heading followed by an install
# command, and the config's heading followed by a shell line - the shapes the
# independent review reported (README reduced to a code block, config.yaml
# receiving command text).
PLANNER_PLAN = """# Plan

1. Create README.md describing the tool.

### README.md
```
pip install tool && tool --help
```

2. Create config.yaml with the default settings.

### config.yaml
```
export TOOL_HOME=/tmp/tool
```
"""
DESIGN = "Design: write config.yaml with the default settings (name, level)."
DEVELOPER_CONFIG = "name: tool\nlevel: info\n"


def _engine(calls):
    config = AppConfig()
    config.autonomy.mode = "guardrails"
    config.autonomy.run_verification_enabled = False
    llm = LLMClient(config)

    async def complete(system_prompt, user_prompt, *args, **kwargs):
        calls.append(system_prompt[:60])
        if system_prompt.startswith(DEVELOPER_PROMPTS):
            if "File List Planner" in system_prompt:
                return '{"files": ["config.yaml"]}'
            return DEVELOPER_CONFIG
        return {1: PLANNER_PLAN, 2: DESIGN}.get(len(calls), "Review: Approved")

    llm.complete = complete
    return WorkflowEngine(Kernel(config=config), llm)


def test_planner_fenced_blocks_never_become_repository_files(tmp_path):
    # Reproduced at 2e8b09f: the Developer was never called, config.yaml held
    # the Planner's "export TOOL_HOME=/tmp/tool" and the run passed its gates
    # (evidence/file-integrity-contract-001b/planner_bypass_prefix_2e8b09f.txt).
    calls = []
    result = asyncio.run(_engine(calls).run_generation_workflow(
        goal="Create config.yaml for a small command-line tool.", workspace_path=str(tmp_path)))
    assert any(call.startswith(DEVELOPER_PROMPTS) for call in calls), calls  # attempt 1 asks the Developer
    assert (tmp_path / "config.yaml").read_text() == DEVELOPER_CONFIG
    assert not (tmp_path / "README.md").exists()  # a fenced README draft in the plan writes nothing
    for path in tmp_path.rglob("*"):
        if path.is_file() and ".git" not in path.parts and ".kriya" not in path.parts:
            text = path.read_text(errors="replace")
            assert "export TOOL_HOME" not in text and "pip install" not in text, path
    assert result["quality_gates_passed"] is True, result.get("failure_category")


def test_no_planner_text_extraction_path_exists():
    """Structural tripwire: the attempt's files come only from the Developer
    generation call; nothing extracts file content from plan text."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "kriya"
    source = (root / "workflow" / "attempt.py").read_text(encoding="utf-8")
    assert "reused_files" not in source and "planner_blocks" not in source
    assert "files = await _run_developer_generation(" in source
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "extract_planner_code_blocks" not in text, path


# Every source of the attempt's candidate file bytes, with its authority. The
# FILE-INTEGRITY-CONTRACT-001 audit classified write SITES; the Planner bypass
# passed through an approved writer with unauthorized BYTES, so this audits
# where `files` (what the staged writer commits) gets its content.
CANDIDATE_BYTE_SOURCES = {
    "await _run_developer_generation(": "Developer mutation intent (parsed response, authorized writer)",
    "_restore_api_contract_owners_deterministically(": "deterministic recovery: the owner's captured baseline",
    "[{'filepath': fp, 'content': content} for fp, content in (ctx.resume_state.get('final_files')":
        "resume: the checkpoint's verified candidate (STATE-001 binding)",
    "normalized_files": "path normalization of the entries above (no new bytes)",
    "list(deduped.values())": "de-duplication of the entries above (no new bytes)",
    "{'filepath': expected_path, 'content': cumulative_content, '_kriya_carried_forward_content': True}":
        "carried forward: Kriya's own earlier candidate state of a Developer-written file",
}


def test_every_source_of_candidate_bytes_has_a_named_authority():
    import ast
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "kriya/workflow/attempt.py").read_text(encoding="utf-8")
    run_attempt = next(node for node in ast.walk(ast.parse(source))
                       if isinstance(node, ast.AsyncFunctionDef) and node.name == "run_attempt")
    found = []
    for node in ast.walk(run_attempt):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(target, ast.Name) and target.id == "files" for target in targets):
                found.append(ast.unparse(node.value))
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
              and node.func.attr in {"append", "extend", "insert"}
              and isinstance(node.func.value, ast.Name) and node.func.value.id == "files"):
            found.append(ast.unparse(node.args[-1]))
    unclassified = [value[:120] for value in found
                    if not any(value.startswith(prefix) for prefix in CANDIDATE_BYTE_SOURCES)]
    assert unclassified == [], ("a new source of candidate file bytes needs a named mutation authority "
                                f"(FILE-INTEGRITY-CONTRACT-001B): {unclassified}")
    assert len(found) >= 6


# === P1: a verification gate must not create unauthorized repository content ===============================

import os  # noqa: E402 - the second half of this file
import subprocess  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest.mock import patch  # noqa: E402

import pytest  # noqa: E402
from test_file_integrity_contract_001 import _binding, _git, _tree, _verification_gate_named  # noqa: E402


def _engine_repo(tmp_path):
    from _milestone_proof_harness import _config, _engine

    ws = tmp_path / "ws"
    ws.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "t@x"], ["config", "user.name", "t"]):
        _git(ws, *args)
    (ws / ".gitignore").write_text("target/\n")
    (ws / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (ws / "test_calc.py").write_text("from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-qm", "s")
    engine, _ = _engine(_config(), [
        "Step 1: add sub", "Design: Write calc.py",
        '<<<KRIYA:FILE path="calc.py">>>\ndef add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n'
        "<<<KRIYA:END_FILE>>>\n",
        "Review: Approved",
    ])
    return engine, ws


def test_a_gate_creating_an_unignored_file_stops_the_real_run_and_nothing_is_committed(tmp_path):
    """The verified tree must be the committed tree: the compile gate creates
    an untracked, non-ignored config file the tests then run against, and
    the candidate commit would not contain it."""
    import asyncio

    from kriya.tools.validate import PolymorphicValidator

    engine, ws = _engine_repo(tmp_path)

    def generating_gate(self, *args, **kwargs):
        (Path(self.workspace_path) / "extra.properties").write_text("mode=generated\n")
        return {"success": True, "output": "ok"}

    with patch.object(PolymorphicValidator, "run_compile_check",
                      _verification_gate_named("run_compile_check", generating_gate)):
        result = asyncio.run(engine.run_generation_workflow("Add sub to calc.py", str(ws)))
    assert result["quality_gates_passed"] is False
    assert result.get("failure_category") == "verification_tree_mutated"
    assert "def sub" not in (ws / "calc.py").read_text()  # nothing committed
    assert not (ws / "extra.properties").exists()


def test_ignored_build_output_created_by_a_gate_is_not_repository_content(tmp_path):
    root = _tree(tmp_path)  # .gitignore: target/
    binding = _binding(root)
    (root / "target" / "generated-sources").mkdir(parents=True)
    (root / "target" / "generated-sources" / "Gen.java").write_text("class Gen {}\n")
    binding.check("compile")  # Git ignores it: never verified-tree content


@pytest.mark.parametrize(("gate", "relpath"), [
    ("compile", "src/Generated.java"),        # compile creates an un-ignored source file
    ("tests", "src/test.properties"),         # a test writes an un-ignored config
    ("runtime_verification", "app.yaml"),     # the app under runtime verification writes YAML
])
def test_an_unignored_file_created_by_a_gate_is_a_typed_stop(tmp_path, gate, relpath):
    from kriya.workflow.file_integrity import VERIFICATION_GATE_CREATED_UNAUTHORIZED_FILE, VerificationGateCreatedFiles

    root = _tree(tmp_path)
    binding = _binding(root)
    (root / relpath).write_text("x\n")
    with pytest.raises(VerificationGateCreatedFiles) as stopped:
        binding.check(gate)
    failure = stopped.value.failure
    assert failure.type == "verification_tree_mutated"
    assert failure.diagnostics["reason_code"] == VERIFICATION_GATE_CREATED_UNAUTHORIZED_FILE
    assert failure.diagnostics["gate"] == gate
    assert failure.diagnostics["created"] == [{"path": relpath, "tracked": False, "ignored": False}]
    assert failure.message.startswith(f"{VERIFICATION_GATE_CREATED_UNAUTHORIZED_FILE}:")


def test_candidate_preexisting_and_authorized_files_are_never_attributed_to_a_gate(tmp_path):
    root = _tree(tmp_path)  # src/New.java: the untracked candidate
    (root / "notes.txt").write_text("the user's own untracked file\n")  # before the binding
    binding = _binding(root)
    (root / "src" / "Kriya.java").write_text("class Kriya {}\n")
    binding.authorize("src/Kriya.java")  # Kriya's own authorized write during verification
    (root / ".kriya").mkdir(exist_ok=True)
    (root / ".kriya" / "state.json").write_text("{}")  # Kriya's own state
    (root / "transient.tmp").write_text("x")
    (root / "transient.tmp").unlink()  # created and removed by the gate: nothing left behind
    binding.check("compile")
    assert binding.created() == []


def test_a_file_created_between_gates_is_caught_before_the_next_gate(tmp_path):
    from kriya.workflow.file_integrity import VerificationGateCreatedFiles

    root = _tree(tmp_path)
    binding = _binding(root)
    (root / "between.yml").write_text("x: 1\n")
    with pytest.raises(VerificationGateCreatedFiles) as stopped:
        binding.check("tests", "before")
    assert stopped.value.failure.diagnostics["phase"] == "before"


def test_the_untracked_listing_uses_gits_own_ignore_rules(tmp_path):
    from kriya.workflow.file_integrity import untracked_repository_paths

    root = _tree(tmp_path)
    (root / ".git" / "info").mkdir(exist_ok=True)
    (root / ".git" / "info" / "exclude").write_text("*.cache\n")  # not a .gitignore: still Git's semantics
    (root / "a.cache").write_text("x")
    (root / "target").mkdir()
    (root / "target" / "out.class").write_text("x")
    (root / "kept.txt").write_text("x")
    assert untracked_repository_paths(str(root)) == frozenset({"src/New.java", "kept.txt"})
    assert untracked_repository_paths(str(tmp_path)) == frozenset()  # not a work tree
    subprocess.run(["git", "status"], cwd=root, check=True, capture_output=True)


# --- gate-owned output: only the gate that runs a toolchain may create its output ---------------------------

def _gate_run(validator, gate, cmd, effect):
    """One real validator gate (the _verification_gate wrapper) that runs
    ``cmd`` through the validator's own runner (process stubbed: ``effect``
    plays the tool's file output) and then checks the bound tree."""
    from kriya.tools.validate import PolymorphicValidator, _verification_gate

    def body(self):
        with patch("kriya.tools.validate.ProcessController.run") as run:
            run.return_value.to_dict.return_value = {"returncode": 0, "stdout": "", "stderr": "", "timeout": False}
            effect()
            self._run_cmd_with_timeout(cmd, cwd=self.workspace_path)
        return {"success": True}

    with patch.object(PolymorphicValidator, "build_containment_profile_and_backend", return_value=(None, None)):
        return _verification_gate(gate)(body)(validator)


def _maven_tree(tmp_path):
    from kriya.tools.validate import PolymorphicValidator

    root = _tree(tmp_path)  # .gitignore: target/ - removed below: the greenfield case ignores nothing
    (root / ".gitignore").unlink()
    _git(root, "rm", "-q", "--cached", ".gitignore")
    _git(root, "commit", "-qm", "no ignore rules")
    (root / "pom.xml").write_text("<project/>\n")
    (root / "core").mkdir()
    (root / "core" / "pom.xml").write_text("<project/>\n")  # a module
    validator = PolymorphicValidator(str(root))
    validator.tree_binding = _binding(root)
    validator.tree_binding.authorize("pom.xml")
    validator.tree_binding.authorize("core/pom.xml")
    return root, validator


MVN = ["mvn", "-q", "compile"]


def test_a_maven_gates_own_target_output_is_accepted_without_any_ignore_rule(tmp_path):
    root, validator = _maven_tree(tmp_path)

    def maven_output():
        for module in (root, root / "core"):
            (module / "target" / "classes").mkdir(parents=True)
            (module / "target" / "classes" / "App.class").write_bytes(b"\xca\xfe")

    _gate_run(validator, "compile", MVN, maven_output)  # passes: target/ of each module is Maven's own


@pytest.mark.parametrize("stray", ["extra.properties", "src/main/java/Injected.java", "docs/target/notes.txt"])
def test_a_maven_gate_creating_anything_else_is_still_a_typed_stop(tmp_path, stray):
    from kriya.workflow.file_integrity import VerificationGateCreatedFiles

    root, validator = _maven_tree(tmp_path)

    def maven_output_plus_stray():
        (root / "target").mkdir()
        (root / "target" / "App.class").write_bytes(b"\xca\xfe")
        (root / stray).parent.mkdir(parents=True, exist_ok=True)
        (root / stray).write_text("x\n")

    with pytest.raises(VerificationGateCreatedFiles) as stopped:
        _gate_run(validator, "compile", MVN, maven_output_plus_stray)
    assert [entry["path"] for entry in stopped.value.failure.diagnostics["created"]] == [stray]


def test_a_target_directory_is_never_inferred_outside_the_gate_that_runs_maven(tmp_path):
    """The compile gate's Maven run owns target/; a later gate that does not
    run Maven never gains that ownership: the app under runtime verification
    writing there is runtime state (D6: recorded and discarded, never kept as
    output), and target/ output appearing between gates is caught."""
    from kriya.workflow.file_integrity import VerificationGateCreatedFiles

    root, validator = _maven_tree(tmp_path)
    _gate_run(validator, "compile", MVN, lambda: ((root / "target").mkdir(), (root / "target" / "A.class").write_bytes(b"x")))
    result = _gate_run(validator, "runtime_verification", ["java", "-cp", "target", "App"],
                       lambda: (root / "target" / "written-by-the-app.txt").write_text("x"))
    assert result["runtime_artifacts"] == ["target/written-by-the-app.txt"]
    assert not (root / "target" / "written-by-the-app.txt").exists() and (root / "target" / "A.class").exists()
    (root / "target" / "between.txt").write_text("x")
    with pytest.raises(VerificationGateCreatedFiles):
        validator.tree_binding.check("tests", "before")


def test_user_files_stay_protected_and_preexisting_ones_are_never_attributed(tmp_path):
    from kriya.workflow.file_integrity import VerificationGateCreatedFiles

    root = _tree(tmp_path)
    (root / "notes.txt").write_text("the user's own untracked file\n")  # before the binding: not the gate's
    binding = _binding(root)
    binding.check("compile")
    (root / "notes2.txt").write_text("appeared during verification\n")
    with pytest.raises(VerificationGateCreatedFiles):
        binding.check("tests")


@pytest.mark.parametrize(("cmd", "markers", "roots"), [
    (["mvn", "-q", "test"], ["pom.xml", "core/pom.xml", "docs/readme.md"], ["target", "core/target"]),
    (["./mvnw", "compile"], ["pom.xml"], ["target"]),
    (["gradle", "build"], ["build.gradle", "app/build.gradle.kts"], ["build", "app/build", ".gradle"]),
    (["./gradlew", "test"], ["settings.gradle"], [".gradle"]),
    (["javac", "-proc:none", "-d", "<root>/build", "A.java"], [], ["build"]),
    (["python3", "-m", "py_compile", "src/a.py"], ["src/a.py", "docs/readme.md"], ["src/__pycache__", ".pytest_cache"]),
    (["java", "-jar", "app.jar"], ["pom.xml"], []),  # running the app owns no output
])
def test_each_toolchain_command_designates_only_its_own_documented_output(tmp_path, cmd, markers, roots):
    from kriya.tools.validate import gate_output_roots

    for marker in markers:
        (tmp_path / marker).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / marker).write_text("")
    cmd = [part.replace("<root>", str(tmp_path)) for part in cmd]
    found = sorted(os.path.relpath(path, tmp_path) for path in gate_output_roots(cmd, str(tmp_path)))
    assert found == sorted(roots)


@pytest.mark.skipif(subprocess.run(["which", "mvn"], capture_output=True).returncode != 0, reason="mvn not on PATH")
def test_a_real_greenfield_maven_compile_passes_its_gate(tmp_path):
    """The regression this fixes (measured at 3d2bfd2): a greenfield Maven
    project has no .gitignore, so the real `mvn compile` gate's own target/
    stopped the run as VERIFICATION_GATE_CREATED_UNAUTHORIZED_FILE."""
    from kriya.tools.validate import PolymorphicValidator
    from kriya.workflow.file_integrity import VerificationTreeBinding
    from kriya.workflow.worktree import repository_content_paths

    for args in (["init", "-q"], ["config", "user.email", "t@x"], ["config", "user.name", "t"],
                 ["commit", "-q", "--allow-empty", "-m", "init"]):
        _git(tmp_path, *args)
    (tmp_path / "src/main/java/demo").mkdir(parents=True)
    (tmp_path / "pom.xml").write_text(
        '<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion>'
        "<groupId>demo</groupId><artifactId>demo</artifactId><version>1</version><properties>"
        "<maven.compiler.release>17</maven.compiler.release>"
        "<project.build.sourceEncoding>UTF-8</project.build.sourceEncoding></properties></project>\n")
    (tmp_path / "src/main/java/demo/App.java").write_text(
        'package demo; public class App { public static void main(String[] a) { System.out.println("hi"); } }\n')
    validator = PolymorphicValidator(str(tmp_path))
    validator.tree_binding = VerificationTreeBinding(
        str(tmp_path), repository_content_paths(str(tmp_path)), ["pom.xml", "src/main/java/demo/App.java"])
    result = validator.run_compile_check(["src/main/java/demo/App.java"])
    assert result["success"], result.get("output")
    assert (tmp_path / "target" / "classes" / "demo" / "App.class").exists()
