"""CAGC-0 facts (KRIYA_CAGC v0.7 §5, §14 test_cagc_facts)."""
import os
import subprocess

import pytest

from kriya.capabilities.guidance import Operation, RepoContext, Role, repository_facts, selection_facts
from kriya.capabilities.guidance.facts import (
    GREENFIELD_FILE_NAMES,
    GREENFIELD_FILE_PREFIXES,
    GREENFIELD_METADATA_DIRS,
    BuildRootFact,
    RepositoryFacts,
    build_roots,
    file_reader,
    nearest_build_root,
    spring_repository,
    workspace_inventory,
    workspace_is_greenfield,
)
from kriya.code_intel.config_parsing import PROPERTIES, SPRING_XML, YAML, config_language_for_path
from kriya.code_intel.model import ParseState
from kriya.code_intel.parsing import JAVA, PYTHON, language_for_path, parse_file
from kriya.code_intel.store import StructuralStore

SPRING_XML_BYTES = (b'<beans xmlns="http://www.springframework.org/schema/beans">'
                    b'<bean id="x" class="a.B"/></beans>')


def _write(root, path, text=""):
    full = os.path.join(root, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as handle:
        handle.write(text)


# -- spring_repository -------------------------------------------------------


def _index(tmp_path, files):
    db = str(tmp_path / "dependency_graph.db")
    store = StructuralStore(db)
    for path, data in files.items():
        store.publish(parse_file(path, data), data)
    store.close()
    return db


def test_spring_repository_from_the_analyzer_framework(tmp_path):
    assert spring_repository(["Spring Boot"], None)
    assert not spring_repository(["Maven (Java)"], None)


def test_spring_repository_from_a_parsed_spring_xml_file(tmp_path):
    db = _index(tmp_path, {"src/main/resources/ctx.xml": SPRING_XML_BYTES})
    assert spring_repository([], db)


def test_spring_repository_from_a_java_import(tmp_path):
    db = _index(tmp_path, {"A.java": b"import org.springframework.cache.annotation.Cacheable;\nclass A {}"})
    assert spring_repository([], db)


def test_spring_repository_false_otherwise(tmp_path):
    db = _index(tmp_path, {
        "A.java": b"import java.util.List;\nclass A {}",
        "pom.xml": b'<project xmlns="http://maven.apache.org/POM/4.0.0"/>',
        "a.py": b"import springframework\n",
    })
    assert not spring_repository(["Maven (Java)"], db)
    assert not spring_repository([], str(tmp_path / "absent.db"))
    assert not os.path.exists(tmp_path / "absent.db"), "an absent index is never created"


def test_store_import_prefix_is_a_prefix_not_a_substring(tmp_path):
    db = _index(tmp_path, {"A.java": b"import com.example.org.springframework.X;\nclass A {}"})
    store = StructuralStore(db)
    try:
        assert not store.has_import_prefix(JAVA, "org.springframework")
        assert store.has_import_prefix(JAVA, "com.example")
        assert not store.has_file(SPRING_XML, ParseState.PARSED)
    finally:
        store.close()


# -- greenfield --------------------------------------------------------------


def test_empty_workspace_is_greenfield(tmp_path):
    assert workspace_is_greenfield(str(tmp_path))


@pytest.mark.parametrize("name", sorted(GREENFIELD_FILE_NAMES) + [p.upper() + ".md" for p in GREENFIELD_FILE_PREFIXES])
def test_each_allowlisted_file_keeps_greenfield(tmp_path, name):
    _write(str(tmp_path), name, "x")
    assert workspace_is_greenfield(str(tmp_path))


@pytest.mark.parametrize("directory", sorted(GREENFIELD_METADATA_DIRS))
def test_each_metadata_directory_is_ignored(tmp_path, directory):
    _write(str(tmp_path), f"{directory}/settings.json", "{}")
    assert workspace_is_greenfield(str(tmp_path))


def test_a_git_worktree_pointer_file_is_metadata(tmp_path):
    _write(str(tmp_path), ".git", "gitdir: /elsewhere\n")
    assert workspace_is_greenfield(str(tmp_path))


@pytest.mark.parametrize("path", ["main.go", "src/app.rs", "notes.txt", "docs/guide.md", "pom.xml", "a.py"])
def test_any_other_file_is_existing(tmp_path, path):
    _write(str(tmp_path), path, "x")
    assert not workspace_is_greenfield(str(tmp_path))


def test_unreadable_directory_is_existing(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0)
    try:
        if os.access(locked, os.R_OK):
            pytest.skip("running with privileges that ignore directory permissions")
        assert not workspace_is_greenfield(str(tmp_path))
    finally:
        locked.chmod(0o700)


def test_walk_limit_is_existing(tmp_path):
    for index in range(5):
        _write(str(tmp_path), f"README{index}.md", "x")
    assert workspace_is_greenfield(str(tmp_path), limit=10)
    assert not workspace_is_greenfield(str(tmp_path), limit=4)


def test_a_non_allowlisted_symlink_is_existing(tmp_path):
    target = tmp_path.parent / f"{tmp_path.name}-target.txt"
    target.write_text("x")
    os.symlink(target, tmp_path / "data.txt")
    assert not workspace_is_greenfield(str(tmp_path))


def test_an_allowlisted_symlink_to_a_regular_file_keeps_greenfield(tmp_path):
    target = tmp_path.parent / f"{tmp_path.name}-readme"
    target.write_text("x")
    os.symlink(target, tmp_path / "README.md")
    assert workspace_is_greenfield(str(tmp_path))


def test_symlinked_directories_are_never_followed(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (outside / "README.md").write_text("x")
    os.symlink(outside, tmp_path / "docs")  # its contents would look greenfield; the link itself does not
    assert not workspace_is_greenfield(str(tmp_path))


def test_greenfield_is_computed_on_the_original_workspace_before_bootstrap(tmp_path):
    """repository_facts on a not-yet-Git workspace: bootstrap happens later
    (create_git_worktree) and a .git directory is metadata either way."""
    facts = repository_facts(str(tmp_path))
    assert facts.greenfield and facts.context is RepoContext.GREENFIELD
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    assert repository_facts(str(tmp_path)).greenfield


# -- build roots -------------------------------------------------------------


def test_build_roots_from_markers_confirmed_by_detects(tmp_path):
    root = str(tmp_path)
    _write(root, "pom.xml", "<project/>")
    _write(root, "svc/build.gradle", "")
    _write(root, "svc/pom.xml", "<project/>")
    _write(root, "tools/py/pyproject.toml", "")
    _write(root, "notes/build.gradle.kts", "")  # Kotlin DSL: not a Gradle marker (GRADLE-KOTLIN-DSL-001)
    roots = build_roots(root, workspace_inventory(root))
    assert roots == (
        BuildRootFact("tools/py", frozenset({"pip"})),
        BuildRootFact("svc", frozenset({"maven", "gradle"})),
        BuildRootFact("", frozenset({"maven"})),
    )


def test_build_roots_skip_kriya_state_and_git_ignored_files(tmp_path):
    root = str(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    _write(root, ".gitignore", "generated/\n")
    _write(root, "generated/pom.xml", "<project/>")
    _write(root, ".kriya/pom.xml", "<project/>")
    _write(root, "app/requirements.txt", "")
    assert build_roots(root, workspace_inventory(root)) == (BuildRootFact("app", frozenset({"pip"})),)


def test_nearest_root_lookup_is_per_path(tmp_path):
    roots = (BuildRootFact("svc/inner", frozenset({"gradle"})), BuildRootFact("svc", frozenset({"maven"})),
             BuildRootFact("", frozenset({"pip"})))
    assert nearest_build_root(roots, "svc/inner/src/A.java").build_systems == {"gradle"}
    assert nearest_build_root(roots, "svc/src/A.java").build_systems == {"maven"}
    assert nearest_build_root(roots, "svcx/A.java").build_systems == {"pip"}
    assert nearest_build_root(roots[:2], "other/A.java") is None


def test_multi_target_requests_union_build_systems_across_roots(tmp_path):
    repo = RepositoryFacts(False, False, (BuildRootFact("a", frozenset({"gradle"})),
                                          BuildRootFact("b", frozenset({"maven"}))))
    facts = selection_facts(Role.PLANNER, Operation.PLAN, repo, ["a/X.java", "b/Y.java", "c/Z.java"],
                            lambda _path: None)
    assert facts.build_systems == {"gradle", "maven"}
    single = selection_facts(Role.PLANNER, Operation.PLAN, repo, ["a/X.java"], lambda _path: None)
    assert single.build_systems == {"gradle"}
    assert repo.build_roots[0].build_systems == {"gradle"}, "lookup never mutates the facts"


def test_greenfield_without_targets_takes_the_goal_build_systems():
    repo = RepositoryFacts(False, True, (), frozenset({"maven"}))
    assert selection_facts(Role.PLANNER, Operation.PLAN, repo, [], lambda _p: None).build_systems == {"maven"}
    existing = RepositoryFacts(False, False, (), frozenset({"maven"}))
    assert selection_facts(Role.PLANNER, Operation.PLAN, existing, [], lambda _p: None).build_systems == set()


# -- languages ---------------------------------------------------------------

LANGUAGE_CASES = {
    JAVA: ("src/A.java", b"class A {}"),
    PYTHON: ("pkg/a.py", b"x = 1\n"),
    SPRING_XML: ("src/main/resources/ctx.xml", SPRING_XML_BYTES),
    PROPERTIES: ("src/main/resources/application.properties", b"server.port=8080\n"),
    YAML: ("src/main/resources/application.yml", b"server:\n  port: 8080\n"),
}


def test_every_language_value_is_exercised():
    returned = {language_for_path(path) or config_language_for_path(path) for path, _ in LANGUAGE_CASES.values()}
    assert returned == set(LANGUAGE_CASES) == {JAVA, PYTHON, SPRING_XML, PROPERTIES, YAML}


@pytest.mark.parametrize("language", sorted(LANGUAGE_CASES))
def test_existing_target_language_comes_from_its_bytes(language):
    path, data = LANGUAGE_CASES[language]
    repo = RepositoryFacts(True, False, ())
    facts = selection_facts(Role.DEVELOPER, Operation.EDIT, repo, [path], {path: data}.get)
    assert facts.target_languages == {language}


def test_a_non_spring_xml_file_has_no_language():
    repo = RepositoryFacts(True, False, ())
    pom = b'<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion></project>'
    facts = selection_facts(Role.DEVELOPER, Operation.EDIT, repo, ["pom.xml", "other.xml"],
                            {"pom.xml": pom, "other.xml": b"<config/>"}.get)
    assert facts.target_languages == frozenset()


def test_a_new_xml_file_is_spring_xml_only_in_a_spring_repository():
    new = lambda _path: None  # noqa: E731 - a reader for files that do not exist yet
    spring = RepositoryFacts(True, False, ())
    plain = RepositoryFacts(False, False, ())
    assert selection_facts(Role.DEVELOPER, Operation.CREATE, spring, ["ctx.xml"], new).target_languages == \
        {SPRING_XML}
    assert selection_facts(Role.DEVELOPER, Operation.CREATE, plain, ["ctx.xml"], new).target_languages == set()
    assert selection_facts(Role.DEVELOPER, Operation.CREATE, spring, ["pom.xml"], new).target_languages == set()


def test_structure_facts_annotations_kinds_and_spring_imports(tmp_path):
    root = str(tmp_path)
    _write(root, "A.java", "import org.springframework.stereotype.Service;\n@Service\nclass A { @Cacheable void f(){} }")
    facts = selection_facts(Role.DEVELOPER, Operation.EDIT, RepositoryFacts(False, False, ()), ["A.java", "B.java"],
                            file_reader(root))
    assert facts.target_annotations == {"Service", "Cacheable"}
    assert {"class", "method"} <= facts.target_symbol_kinds
    assert facts.target_imports_spring
    assert facts.target_paths == ("A.java", "B.java")
    assert facts.context is RepoContext.EXISTING
