"""BACKEND-READINESS-004 (owner decision 2): the sealed documentation list-entries predicate.

"... and document them in the README's function list if there is one": when the referent and its named list exist at
baseline, the predicate is compiled and sealed BEFORE any model call from the goal's own words (the subjects are the
identifiers the goal's addition statements name) and the baseline README (which section, which subjects are already
listed). On the candidate it requires the sealed section to still exist and to carry an entry for every subject -
a whole-word match inside that section only; prose elsewhere in the file never counts. Absent referent: vacuous, as
before. Undeterminable subjects: a content claim, authority required, never a guess.
"""
from kriya.workflow import contract_closers as cc
from kriya.workflow import requirement_scopes as rs
from kriya.workflow.contract_baseline import BASELINE_MUTATION_REQUIRED, BASELINE_PASS, run_baseline_authorities
from kriya.workflow.contract_compilation import (
    CLOSER_DOCUMENTATION_LIST_ENTRIES,
    CLOSER_DOCUMENTATION_NOT_APPLICABLE,
    STATUS_AUTHORITY_REQUIRED,
    compile_verification_contract,
    documentation_entries_present,
    documentation_sections,
)
from kriya.workflow.obligations import ObligationLedger
from kriya.workflow.requirements import (
    DOCUMENTATION_CLAIM,
    RequirementOutcome,
    derive_requirements,
    record_requirement_verdicts,
    requirement_outcomes,
    seed_requirement_obligations,
    statement_origins,
)

T5_LIKE = ("Add three built-in string functions to the JMESPath implementation: lower, upper and trim.\n\n"
           "Document them in the README's function list if there is one.\n")
BASE_README = b"""# JMESPath

Some prose that mentions lower and upper casually.

## Function list

- `abs(number)` - absolute value
- `join(glue, array)` - joins strings

## Other

- trim is not here
"""
FIXED_README = BASE_README.replace(b"- `join(glue, array)` - joins strings\n",
                                   b"- `join(glue, array)` - joins strings\n- `lower(string)` - lower-case\n"
                                   b"- `upper(string)` - upper-case\n- `trim(string)` - strips whitespace\n")


def _reader(files):
    return lambda path: files.get(path)


def _compile(goal, files, language="python"):
    reqs = derive_requirements(goal)
    return reqs, compile_verification_contract(reqs, origins=statement_origins(goal), test_files=[],
                                               tracked_paths=list(files), tracked_file_reader=_reader(files),
                                               project_language=language)


def _ledger(reqs):
    ledger = ObligationLedger()
    seed_requirement_obligations(ledger, reqs)
    record_requirement_verdicts(ledger, reqs, {r.id: (RequirementOutcome.UNVERIFIED, "x") for r in reqs.requirements},
                                revision=1, evidence_fingerprint="cand", source="test")
    return ledger


def test_01_subjects_come_from_the_goals_own_addition_statement_or_the_clause_itself():
    reqs = derive_requirements(T5_LIKE)
    clause = rs.documentation_clause(reqs.get("REQ-2").text)
    assert clause["list_noun"] == "function" and clause["conditional"]
    subjects, sources = rs.documentation_subjects(reqs.get("REQ-2").text, clause, [(r.id, r.text) for r in reqs.requirements if r.id != "REQ-2"])
    assert subjects == ("lower", "upper", "trim") and sources == ("REQ-1",)
    # named in the clause itself: code spans win, nothing is read from other statements
    text = "Document `init` and `sync()` in the README's command list if there is one."
    assert rs.documentation_subjects(text, rs.documentation_clause(text), [("REQ-9", "Add commands: a, b and c.")]) == (("init", "sync"), ())
    # no addition statement of the list noun's family: no subjects (a content claim)
    assert rs.documentation_subjects(reqs.get("REQ-2").text, clause, [("REQ-1", "Add three new options: fast, slow and off.")]) == ((), ())
    assert rs.documentation_subjects(reqs.get("REQ-2").text, clause, [("REQ-1", "The functions lower, upper and trim are slow.")]) == ((), ())


def test_02_the_predicate_is_sealed_from_the_baseline_and_closes_only_inside_the_named_section():
    reqs, contract = _compile(T5_LIKE, {"README.md": BASE_README})
    entry = contract.entry("REQ-2")
    assert entry.closers == [CLOSER_DOCUMENTATION_LIST_ENTRIES] and entry.status != STATUS_AUTHORITY_REQUIRED
    [binding] = entry.bindings
    assert binding.detail["subjects"] == ["lower", "upper", "trim"] and binding.detail["headings"] == ["README.md: Function list"]
    assert binding.detail["baseline_present"] == {} and binding.detail["baseline_satisfied"] is False
    assert binding.detail["source_requirements"] == ["REQ-1"]
    # the baseline: a mutation is required (never NO_MUTATION_REQUIRED on an unmet documentation obligation)
    report = run_baseline_authorities(contract, reqs, base_revision="b", judge_suite=lambda: "PASS")
    assert report.claims["REQ-2"][DOCUMENTATION_CLAIM]["state"] == BASELINE_MUTATION_REQUIRED and not report.no_mutation_required
    # on the candidate: the entries present -> SATISFIED; the casual prose mention was never enough
    ledger = _ledger(reqs)
    [closure] = cc.close_documentation_requirements(ledger, reqs, contract, candidate_tracked_paths=["README.md"],
                                                    read_candidate=_reader({"README.md": FIXED_README}), source="t", revision=1)
    assert closure["closed"] is True and closure["reason_code"] == cc.DOCUMENTATION_ENTRIES_PRESENT
    assert closure["present"]["trim"] == ["README.md: Function list: - `trim(string)` - strips whitespace"]
    assert requirement_outcomes(ledger, reqs)["REQ-2"] is RequirementOutcome.CLOSED_BY_EVIDENCE
    # missing one entry -> open with the missing names; prose outside the section does not count
    partial = FIXED_README.replace(b"- `trim(string)` - strips whitespace\n", b"")
    other = _ledger(reqs)
    [closure] = cc.close_documentation_requirements(other, reqs, contract, candidate_tracked_paths=["README.md"],
                                                    read_candidate=_reader({"README.md": partial}), source="t", revision=1)
    assert closure["closed"] is False and closure["reason_code"] == cc.DOCUMENTATION_ENTRIES_MISSING and closure["missing"] == ["trim"]
    assert requirement_outcomes(other, reqs)["REQ-2"] is RequirementOutcome.UNVERIFIED
    # the candidate removed the section -> open, never satisfied by mentions elsewhere
    removed = FIXED_README.replace(b"## Function list\n", b"## Removed\n")
    third = _ledger(reqs)
    [closure] = cc.close_documentation_requirements(third, reqs, contract, candidate_tracked_paths=["README.md"],
                                                    read_candidate=_reader({"README.md": removed}), source="t", revision=1)
    assert closure["closed"] is False and closure["reason_code"] == cc.DOCUMENTATION_LIST_REMOVED


def test_03_already_documented_at_baseline_passes_and_a_subjectless_clause_stays_a_content_claim():
    reqs, contract = _compile(T5_LIKE, {"README.md": FIXED_README})
    [binding] = contract.entry("REQ-2").bindings
    assert binding.detail["baseline_satisfied"] is True and set(binding.detail["baseline_present"]) == {"lower", "upper", "trim"}
    report = run_baseline_authorities(contract, reqs, base_revision="b", judge_suite=lambda: "PASS")
    assert report.claims["REQ-2"][DOCUMENTATION_CLAIM]["state"] == BASELINE_PASS
    # no subjects derivable: the clause keeps its content claim (authority required), exactly as before this batch
    vague = "Make parsing faster.\n\nDocument them in the README's function list if there is one.\n"
    reqs2, contract2 = _compile(vague, {"README.md": BASE_README})
    assert contract2.entry("REQ-2").status == STATUS_AUTHORITY_REQUIRED
    assert [r.claim for r in contract2.entry("REQ-2").residual] == [DOCUMENTATION_CLAIM]
    # absent referent: vacuous, as before
    reqs3, contract3 = _compile(T5_LIKE, {"calc.py": b""})
    assert contract3.entry("REQ-2").closers == [CLOSER_DOCUMENTATION_NOT_APPLICABLE]


def test_04_section_and_entry_helpers_are_whole_word_and_section_scoped():
    sections = documentation_sections(BASE_README, "function")
    assert list(sections) == ["Function list"] and any("abs" in line for line in sections["Function list"])
    present = documentation_entries_present(sections, ["abs", "lower", "join", "ab"])
    assert present["abs"] and present["join"] and present["lower"] == [] and present["ab"] == []  # 'ab' is not 'abs'
    rst = b"Functions\n=========\n\n* ``lower()`` - x\n\nNotes\n-----\n\n* trim\n"
    rst_sections = documentation_sections(rst, "function")
    assert list(rst_sections) == ["Functions"] and documentation_entries_present(rst_sections, ["lower", "trim"]) == {
        "lower": ["Functions: * ``lower()`` - x"], "trim": []}
