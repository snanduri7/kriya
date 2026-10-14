"""SkillService (Demo V2): the read-only façade the GUI/KUP reads Skills through. Independent of workflow execution, no network."""
from __future__ import annotations

import hashlib
import json
import re
import socket
from pathlib import Path
from typing import Dict, List, Optional

import pytest

from kriya.skills import service as skill_service
from kriya.skills.service import SkillDescriptor, SkillService
from kriya.skills.skill import SkillEngine
from tests._strict_doubles import strict_config

SECRET_RULE = "RULE-TEXT-SENTINEL-must-never-be-returned"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def refuse(*_a, **_k):
        raise AssertionError("SkillService must not touch the network")
    monkeypatch.setattr(socket, "socket", refuse)


def _cfg(skills_dir: Path):
    return strict_config(paths={"skills": str(skills_dir), "memory": str(skills_dir.parent / "memory")}, skills={"load_global": False, "load_cwd": False})


def _write_skill(root: Path, folder: str, *, name: Optional[str] = None, rules: Optional[List[str]] = None, staged: Optional[List[str]] = None,
                 provenance: Optional[object] = None, extra_yaml: str = "", verified: bool = False, instructions: str = "") -> Path:
    directory = root / folder
    directory.mkdir(parents=True)
    (directory / "skill.yaml").write_text(
        f"name: {name or folder}\ndescription: Skill {folder}\ncategory: Messaging\ntags: [a, b]\nsupported_versions: '>=2.0 <3.0'\nverified: {str(verified).lower()}\n{extra_yaml}")
    if rules is not None:
        (directory / "rules.txt").write_text("\n".join(rules) + "\n")
    if staged is not None:
        (directory / "staged_rules.txt").write_text("\n".join(staged) + "\n")
    if instructions:
        (directory / "instructions.md").write_text(instructions)
    if provenance is not None:
        (directory / "rule_provenance.json").write_text(provenance if isinstance(provenance, str) else json.dumps(provenance))
    return directory


def _tree_state(root: Path) -> Dict[str, str]:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() + str(p.stat().st_mtime_ns) for p in sorted(root.rglob("*")) if p.is_file()}


def test_no_skills(tmp_path):
    (tmp_path / "skills").mkdir()
    items, total = SkillService(_cfg(tmp_path / "skills"), str(tmp_path)).list()
    assert items == [] and total == 0
    assert SkillService(_cfg(tmp_path / "skills"), str(tmp_path)).describe("anything") is None


def test_missing_skills_directory_is_an_empty_list_not_an_error(tmp_path):
    assert SkillService(_cfg(tmp_path / "nope"), str(tmp_path)).list() == ([], 0)


def test_one_skill_is_described_fully_and_unknowns_are_explicit(tmp_path):
    skills = tmp_path / "skills"
    _write_skill(skills, "artemis", name="Org.Apache:Artemis", rules=["r1", "r2"], staged=["s1"], instructions="# do it", verified=True,
                 extra_yaml="verified_at: '2026-10-01'\nverified_context: ran gate\n")
    (skills / "artemis" / "examples").mkdir()
    (skills / "artemis" / "examples" / "broker.xml").write_text("<x/>")
    service = SkillService(_cfg(skills), str(tmp_path))
    items, total = service.list()
    assert total == 1 and len(items) == 1
    d = items[0]
    assert (d.id, d.name) == ("org.apache:artemis", "Org.Apache:Artemis")
    assert (d.description, d.category, d.tags, d.supported_versions) == ("Skill artemis", "Messaging", ("a", "b"), ">=2.0 <3.0")
    assert (d.verified, d.verified_at, d.verified_context) == (True, "2026-10-01", "ran gate")
    assert (d.rule_count, d.staged_rule_count, d.example_count, d.has_instructions) == (2, 1, 1, True)
    assert d.skill_version is None and "skill_version" in d.unknown_fields
    assert d.source == "local_directory"
    assert d.scope == "workspace"  # the skills folder is inside the workspace we passed
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", d.digest) and "digest" not in d.unknown_fields
    assert service.describe("ORG.APACHE:ARTEMIS") == d  # lookup is case-insensitive, as the engine's is


def test_the_digest_is_the_engines_own_content_hash(tmp_path):
    skills = tmp_path / "skills"
    _write_skill(skills, "a", rules=["x"])
    cfg = _cfg(skills)
    engine = SkillEngine.from_config(cfg, workspace_path=str(tmp_path))
    engine.discover_and_load()
    expected = "sha256:" + engine.manifest_for(["a"])[0]["content_hash"]
    assert SkillService(cfg, str(tmp_path)).describe("a").digest == expected
    (skills / "a" / "rules.txt").write_text("x\ny\n")  # a content change changes the digest
    assert SkillService(cfg, str(tmp_path)).describe("a").digest != expected


def test_an_unhashable_skill_is_shown_with_an_explicit_unknown_digest(tmp_path, monkeypatch):
    skills = tmp_path / "skills"
    _write_skill(skills, "a", rules=["x"])
    monkeypatch.setattr(SkillEngine, "manifest_for", lambda self, names: (_ for _ in ()).throw(RuntimeError("boom")))
    d = SkillService(_cfg(skills), str(tmp_path)).describe("a")
    assert d is not None and d.digest is None and "digest" in d.unknown_fields and d.rule_count == 1


def test_multiple_skills_are_listed_in_id_order_with_a_true_total(tmp_path):
    skills = tmp_path / "skills"
    for folder in ("zeta", "alpha", "mid"):
        _write_skill(skills, folder, rules=["r"])
    items, total = SkillService(_cfg(skills), str(tmp_path)).list()
    assert [i.id for i in items] == ["alpha", "mid", "zeta"] and total == 3


def test_the_cap_keeps_the_true_total(tmp_path, monkeypatch):
    skills = tmp_path / "skills"
    for n in range(5):
        _write_skill(skills, f"s{n}", rules=["r"])
    monkeypatch.setattr(skill_service, "MAX_SKILLS", 3)
    items, total = SkillService(_cfg(skills), str(tmp_path)).list()
    assert len(items) == 3 and total == 5


def test_two_skills_sharing_a_name_list_once_and_describe_resolves_the_same_one(tmp_path):
    skills = tmp_path / "skills"
    _write_skill(skills, "first", name="Dup", rules=["one"])
    _write_skill(skills, "second", name="Dup", rules=["one", "two"])
    service = SkillService(_cfg(skills), str(tmp_path))
    items, total = service.list()
    assert [i.id for i in items] == ["dup"] and total == 1
    assert service.describe("dup") == items[0]


def test_verified_and_unverified_are_distinct(tmp_path):
    skills = tmp_path / "skills"
    _write_skill(skills, "good", verified=True, rules=["r"])
    _write_skill(skills, "raw", verified=False, rules=["r"])
    items, _ = SkillService(_cfg(skills), str(tmp_path)).list()
    assert {i.id: i.verified for i in items} == {"good": True, "raw": False}


def test_staged_rules_are_counted_not_returned(tmp_path):
    skills = tmp_path / "skills"
    _write_skill(skills, "a", rules=["r"], staged=["staged one", "", "staged two"])
    d = SkillService(_cfg(skills), str(tmp_path)).describe("a")
    assert d.staged_rule_count == 2 and d.rule_count == 1


def test_stored_provenance_is_summarised_by_source(tmp_path):
    skills = tmp_path / "skills"
    records = [
        {"text": "r1", "verified": True, "source": "https://example.org/a", "added_at": "2026-10-01"},
        {"text": "r2", "verified": False, "source": "https://example.org/a", "added_at": "2026-10-03"},
        {"text": "r3", "verified": False, "source": "https://example.org/b", "added_at": "2026-10-02"},
        {"text": "gone", "verified": True, "source": "https://example.org/stale", "added_at": "2026-09-01"},  # rule no longer in rules.txt
    ]
    _write_skill(skills, "a", rules=["r1", "r2", "r3", "r4"], provenance=records)
    p = SkillService(_cfg(skills), str(tmp_path)).describe("a").provenance
    assert (p.state, p.tracked_rule_count, p.untracked_rule_count, p.verified_tracked_count) == ("RECORDED", 3, 1, 1)
    assert [(s.source, s.rule_count, s.verified_count, s.first_added, s.last_added) for s in p.sources] == [
        ("https://example.org/a", 2, 1, "2026-10-01", "2026-10-03"), ("https://example.org/b", 1, 0, "2026-10-02", "2026-10-02")]
    assert "stale" not in json.dumps([s.source for s in p.sources])


def test_no_provenance_file_means_untracked_not_verified(tmp_path):
    skills = tmp_path / "skills"
    _write_skill(skills, "a", rules=["r1", "r2"])
    p = SkillService(_cfg(skills), str(tmp_path)).describe("a").provenance
    assert (p.state, p.tracked_rule_count, p.untracked_rule_count, p.verified_tracked_count, p.sources) == ("NONE_RECORDED", 0, 2, 0, ())


@pytest.mark.parametrize("content, state, sources", [
    ("{not json", "UNREADABLE_OR_EMPTY", []),                                          # the engine logs and treats it as empty
    ("{}", "UNREADABLE_OR_EMPTY", []),                                                 # valid JSON, wrong shape
    ('[1, "x", null, {"text": 5}, {"source": "no text"}]', "UNREADABLE_OR_EMPTY", []),  # no usable record for a current rule
    ('[{"text": "r1", "source": 7}]', "RECORDED", ["unspecified"]),                    # a non-string source is shown as unspecified, not coerced
])
def test_malformed_or_non_authoritative_provenance_never_breaks_the_listing(tmp_path, content, state, sources):
    skills = tmp_path / "skills"
    _write_skill(skills, "a", rules=["r1"], provenance=content)
    d = SkillService(_cfg(skills), str(tmp_path)).describe("a")
    assert d is not None and d.rule_count == 1
    assert d.provenance.state == state and [s.source for s in d.provenance.sources] == sources
    assert d.provenance.verified_tracked_count == 0


def test_a_verified_flag_that_is_not_literally_true_does_not_count_as_verified(tmp_path):
    skills = tmp_path / "skills"
    _write_skill(skills, "a", rules=["r1"], provenance=[{"text": "r1", "verified": "yes", "source": "s", "added_at": "2026-10-01"}])
    assert SkillService(_cfg(skills), str(tmp_path)).describe("a").provenance.verified_tracked_count == 0


def test_missing_skill_is_none(tmp_path):
    skills = tmp_path / "skills"
    _write_skill(skills, "a", rules=["r"])
    assert SkillService(_cfg(skills), str(tmp_path)).describe("not-there") is None


def test_a_malformed_skill_is_absent_and_does_not_hide_its_neighbours(tmp_path):
    skills = tmp_path / "skills"
    _write_skill(skills, "good", rules=["r"])
    bad = skills / "bad"
    bad.mkdir()
    (bad / "skill.yaml").write_text("name: [unterminated\n  : :")
    wrong_type = skills / "typed"
    wrong_type.mkdir()
    (wrong_type / "skill.yaml").write_text("name: typed\nsupported_versions: [1, 2]\n")
    service = SkillService(_cfg(skills), str(tmp_path))
    items, total = service.list()
    assert [i.id for i in items] == ["good"] and total == 1
    assert service.describe("bad") is None and service.describe("typed") is None


def test_unknown_optional_metadata_is_ignored(tmp_path):
    skills = tmp_path / "skills"
    _write_skill(skills, "a", rules=["r"], extra_yaml="future_field: {nested: [1, 2]}\nowner: someone\n")
    d = SkillService(_cfg(skills), str(tmp_path)).describe("a")
    assert d is not None and d.rule_count == 1


def test_rule_text_is_never_returned(tmp_path):
    skills = tmp_path / "skills"
    _write_skill(skills, "a", rules=[SECRET_RULE], staged=[SECRET_RULE + "-staged"],
                 provenance=[{"text": SECRET_RULE, "verified": True, "source": "https://example.org", "added_at": "2026-10-01"}], instructions=SECRET_RULE + "-instr")
    service = SkillService(_cfg(skills), str(tmp_path))
    blob = json.dumps([i.to_dict() for i in service.list()[0]] + [service.describe("a").to_dict()])
    assert "SENTINEL" not in blob


def test_reading_changes_nothing_on_disk(tmp_path):
    skills = tmp_path / "skills"
    _write_skill(skills, "a", rules=["r1"], staged=["s"], provenance=[{"text": "r1", "verified": False, "source": "s", "added_at": "2026-10-01"}])
    before = _tree_state(tmp_path)
    service = SkillService(_cfg(skills), str(tmp_path))
    service.list()
    service.describe("a")
    service.describe("missing")
    assert _tree_state(tmp_path) == before


@pytest.mark.parametrize("where, expected", [("install", "install"), ("workspace", "workspace"), ("elsewhere", "configured")])
def test_scope_follows_where_the_skill_lives(tmp_path, monkeypatch, where, expected):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    install = tmp_path / "install-skills"
    install.mkdir()
    monkeypatch.setattr(skill_service, "get_global_skills_dir", lambda: str(install))
    root = {"install": install, "workspace": workspace / "skills", "elsewhere": tmp_path / "other"}[where]
    _write_skill(root, "a", rules=["r"])
    d = SkillService(_cfg(root), str(workspace)).describe("a")
    assert d.scope == expected


def test_descriptor_serialises_to_plain_json(tmp_path):
    skills = tmp_path / "skills"
    _write_skill(skills, "a", rules=["r"])
    d: SkillDescriptor = SkillService(_cfg(skills), str(tmp_path)).describe("a")
    data = d.to_dict()
    assert json.loads(json.dumps(data)) == data
    assert isinstance(data["tags"], list) and isinstance(data["unknown_fields"], list) and isinstance(data["provenance"]["sources"], list)
    assert not any(isinstance(v, str) and v.startswith(str(tmp_path)) for v in data.values()), "no filesystem path is part of a descriptor"
    assert str(tmp_path) not in json.dumps(data)


def test_a_skill_in_the_install_directory_is_install_scoped_even_when_the_install_is_inside_the_workspace(tmp_path, monkeypatch):
    # Kriya working on its own checkout: the shared skills directory is inside the workspace. origin and scope must agree: install wins.
    workspace = tmp_path / "kriya-checkout"
    install = workspace / "skills"
    _write_skill(install, "a", rules=["r"])
    monkeypatch.setattr(skill_service, "get_global_skills_dir", lambda: str(install))
    d = SkillService(_cfg(install), str(workspace)).describe("a")
    assert (d.scope, d.origin) == ("install", "kriya_install")
