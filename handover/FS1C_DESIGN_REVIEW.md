# FS-1C design review (Track A)

**Reviews:** `handover/FS1C_REQUIREMENT_EVIDENCE_BINDING_DESIGN.md` (v1).
**Criterion:** a requirement may become deterministically VERIFIED only from evidence whose oracle is independent of
the candidate/model output being judged.
**Verdict:** **NEEDS CHANGE.** No production code changed.
**Labels:** MEASURED, TRACED, INFERRED, CONFIRMED.

## 1. New finding: the existing named-test closure trusts a candidate-controlled oracle (CONFIRMED)

- **Rule today (TRACED).** `close_unverified_requirements_with_named_tests` (`kriya/workflow/requirements.py:842`)
  refuses closure only when a named test **file** is in the candidate's changed set (`touched = named ∩ changed`).
- **Meaning is not guarded.** What decides whether that unchanged file passes is not guarded:
  - `conftest.py` hooks and fixtures;
  - test helpers;
  - runner configuration (`pyproject`/`pytest.ini`/`setup.cfg`/`tox.ini`, Surefire/Gradle test configuration,
    `junit-platform.properties`, service-loaded listeners/extensions);
  - test resources.
- **MEASURED (real pytest 9.1.1, scratch project).** A candidate `conftest.py` `pytest_runtest_makereport`
  hookwrapper that sets `outcome = "passed"` turned a failing test into **passed in the JUnit XML and exit code 0**.
- **CONFIRMED, end to end.** `tests/test_fs1c_named_test_oracle_dependency.py` uses the real `PolymorphicValidator`
  and real pytest.
  - The candidate breaks `app.value()`. The named `tests/test_legacy.py` is unchanged.
  - Without a candidate `conftest.py`, the requirement stays UNVERIFIED (control).
  - With one, it becomes **CLOSED_BY_EVIDENCE** (pinned).
  - Required behaviour: `handover/evidence/fs1c/test_named_test_oracle_required.py`, which **fails today**.
- **Classification.**
  - A current false-success path in a deterministic closure that FS-1B made central.
  - Pre-existing (PRD-020), not introduced by FS-1A/B.
  - Deliberate in this form; an accidental variant is plausible (a candidate "fixing" a shared fixture or helper the
    named test uses).
- **Status.** Not fixed (Track A forbids production code). **Recommended as the first FS-1C unit (C0).**
- **Correction to my own FS-1A note.** The FS-1A residual note said the exit code "stays authoritative". That is wrong
  against in-process hooks: report and exit code are forged together (measurement 2).

### Other measurements (pytest)

| Attempt by candidate-controlled code | Report | Exit | Effect |
|---|---|---|---|
| conftest hookwrapper flips outcome | passed | 0 | **forged** (both channels) |
| `addopts = --junitxml=elsewhere.xml` | Kriya's path kept | 1 | none: Kriya's CLI option wins |
| a test module overwrites the report file at import | real result | 1 | none: pytest writes at session end |
| conftest `pytest_collection_modifyitems` empties the run | no cases | 5 | visible: the identity is absent |

Java/JUnit (INFERRED from the documented mechanisms, not measured here). Equivalent in-process channels exist:
- JUnit 5 extensions: `@ExtendWith`, auto-detected via `junit.jupiter.extensions.autodetection.enabled` +
  `META-INF/services`, and `TestExecutionExceptionHandler` swallowing failures;
- `TestExecutionListener` service files;
- Surefire configuration in `pom.xml`: `testFailureIgnore`, `skipTests`, `includes`/`excludes`, and
  `reportsDirectory` (moving reports makes FS-1A INDETERMINATE, which fails closed).

## 2. Evidence-source classification

| Source | Class | Oracle author | Candidate can mutate oracle? | Candidate can influence report? | Bound to exact revision? | Remaining false-success path |
|---|---|---|---|---|---|---|
| Pre-existing repository tests (named, unchanged) | **MIXED** today → INDEPENDENT with C0 | repository / user | the file: no (guarded); its dependencies: **yes** (§1) | **yes** (hooks, config) | evidence_id yes; report gate id: no (v1) | §1 path; vacuous pass via fixtures |
| User-provided acceptance tests (operator input) | INDEPENDENT oracle; MIXED execution | user | no, if Kriya-owned and digest-bound | yes, through candidate hooks/config/production code in the same process | with B2/B3 binding | in-process forgery; weak user tests (user's own authority) |
| Hidden / golden tests (operator-held, unseen) | INDEPENDENT oracle; MIXED execution | operator | no | same as above | if digest-bound | same as above |
| Executable examples in the goal (B2) | INDEPENDENT oracle; MIXED execution | user | no (Kriya writes the file from the goal's bytes) | same as above | via requirement-set digest | same as above; examples prove only those examples |
| Runtime assertions fixed before generation | INDEPENDENT if user-authored; **MODEL_CLAIMED** if model-authored (even before generation) | user / model | no (if Kriya-owned) | same as above | yes | a model-authored oracle can encode the model's misreading |
| Static analysis | INDEPENDENT | rule packs | no (Kriya-owned packs, waivers outside the workspace) | no (scanner out of candidate process) | yes (PRD-031A binding) | proves only rule properties; closes a requirement only if it *is* a rule statement |
| Compiler evidence | INDEPENDENT | language spec / toolchain | build config only (PRD-011 authority) | no | yes | proves well-formedness/signatures, never behaviour |
| Mutation-scope check (existing) | INDEPENDENT | Kriya (git + commit evidence) | no | no | yes | none known |
| Migration gate (existing) | INDEPENDENT | Kriya (dependency analysis) | no | no | yes | none known |
| Candidate-written tests | **CANDIDATE_CONTROLLED** | candidate | yes | yes | yes | can be weak or vacuous; never closure (FS-1B) |
| Model-generated tests (B4) | **MODEL_CLAIMED** | model | n/a | yes | yes | encode model judgment; corroboration only |
| Model reviewer / spec-compliance judgments | **MODEL_CLAIMED** | model | n/a | n/a | yes | none: never closure (FS-1B) |

**Common residual for every executed oracle.** Executing an independent oracle still runs candidate code in the same
process. The oracle is independent, but its *verdict channel* is not, unless the runner's extension points are
Kriya-controlled. That is what §4 addresses.

## 3. Independent oracle rule (proposed, exact)

> A requirement outcome may become CLOSED_BY_EVIDENCE only from a deterministic check where:
> 1. **Authority.** The oracle's authority is the user, the operator, or a Kriya-owned deterministic analysis, never
>    the candidate or a model.
> 2. **Integrity.** The oracle and every file that can change its result (its *dependency set*: the oracle source,
>    test-side support files, runner and build verification configuration, test resources) are byte-identical to
>    their authorized revision. That is the base revision, or a digest-bound user/operator input.
> 3. **Fresh, complete evidence.** The check ran on the exact candidate the verdict judged (`evidence_id`). It produced
>    a fresh, COMPLETE `TestExecutionReport`, or an equivalent Kriya-owned record, in which every oracle identity
>    expected from the authorized revision executed and passed.
> 4. **Binding.** The closure record binds the requirement-set digest, `evidence_id`, the oracle and dependency-set
>    digests, and the report's gate id and sha256.
>
> Anything else is at most corroboration. A violation of (2) or (3) leaves the requirement UNVERIFIED; it never
> becomes VIOLATED by itself.

## 4. Report provenance: separating the exit result from identity provenance

- **Exit result.** Whether the runner process reported success. In-process code can forge it (MEASURED).
- **Identity provenance.** Which identities ran, and whether the runner's extension points were Kriya-controlled while
  they ran.

### Proposed provenance classes

| Class | Conditions | Closure eligible? |
|---|---|---|
| `KRIYA_CONTROLLED` | runner verification configuration and the oracle dependency set equal their authorized revision; Kriya's own runner arguments | yes (if C0–C3 also hold) |
| `CANDIDATE_INFLUENCED` | any of those changed by the candidate | no; Rule E still uses it as a necessary, not sufficient, check |
| `INDETERMINATE` | as in FS-1A | no |

### Mechanisms (no new containment technology needed)

1. **Protected verification-configuration set**, computed per runner from the base revision:
   - pytest: `conftest.py` on the oracle's path chain, `pytest.ini`, `pyproject.toml` `[tool.pytest*]`, `setup.cfg`
     `[tool:pytest]`, `tox.ini` `[pytest]`, and declared plugins;
   - JVM: the Surefire/Failsafe/Gradle test configuration (parsed as XML/structurally, not as text),
     `junit-platform.properties`, `src/test/resources/META-INF/services/*`, and test-scope extension classes.

   Its digest goes into the report (`verification_config_digest`). A change makes the report
   `CANDIDATE_INFLUENCED`.
2. **The oracle dependency set** of a named or provided test: its file plus the test-side files it imports
   (`code_intel` import edges, transitively within the test tree) plus (1). Byte-identical to base, or the closure is
   refused (`ORACLE_DEPENDENCY_CHANGED`). This is C0.
3. **Expected identities known before the run.** The oracle's identities come from the authorized revision: a
   baseline report, or for Kriya-written B2 files from the file itself. A missing expected identity is
   `ORACLE_NOT_EXECUTED`; a deselecting hook cannot pass silently.
4. **Consistency check.** The report's case counts must equal the runner's own aggregate summary (pytest final line;
   Surefire "Results:" block). A mismatch is INDETERMINATE. This is cheap, and it catches file-level tampering, not
   in-process hooks.
5. **Containment / FILE-INTEGRITY.**
   - **Already in place:**
     - the report path is Kriya-owned (`.kriya/**`, unreachable by candidate writes) and fresh per invocation;
     - the VerificationTreeBinding proves the tree did not change across the gate;
     - containment (when required) confines the run to the workspace mount with no network.
   - **Proposed:** run independent-oracle closure in a *separate* gate invocation, with Kriya's runner arguments
     (pytest `-p no:cacheprovider` and a `--confcutdir`/`-c` pointing at Kriya-owned config when the dependency set
     allows), so that a candidate test file cannot add hooks to it.
   - **Not claimed:** containment cannot stop *candidate production code* that the oracle legitimately executes from
     tampering in-process (e.g. monkey-patching the runner at import). That remains a disclosed residual of every
     executed oracle. It is adversarial, not accidental, and bounded by containment (no host or network effect).

**Report forgery risk: REQUIRES MITIGATION.**
- Mechanisms 1–3 are required before any executed oracle (C0, B2, B3) is closure-eligible.
- Mechanism 4 is cheap defence in depth.
- The in-process residual is accepted and disclosed.

## 5. R1: Python baseline inventory: REQUIRED

- **Gap (TRACED, FS-1A).** Rule E judges only the candidate report. A modified existing Python test that stops being
  collected is caught only if no other part of the delta ran. Python has no structural test marker.
- **Closing it needs baseline vs candidate inventory.** Expected = identities in the changed test files at base
  (baseline report). Every expected identity whose callable still exists in the candidate (`code_intel`; a deletion
  is a structural fact) must appear in the candidate report, else `TEST_NOT_EXECUTED`.
- **FS-1C needs the same inventory anyway.** It is how C0/B1 know which identities an unchanged named test must
  execute (mechanism 3).
- **Cost.** Bounded: one targeted run of only the affected test files at base, cached by base revision. The
  brownfield baseline gate already produces the report (FS-1A); it is just not stored. Aggregate counts are never
  used.

## 6. Required changes to the v1 design

1. **Add C0 (blocking, first).** The named-test closure must guard the oracle dependency set (§4.2) and expected
   identities (§4.3), using the report rather than `output_confirms_nonzero_test_execution`. v1's B1 covered only the
   identity part and missed the dependency set; the confirmed path in §1 is the case.
2. **Make the independent oracle rule (§3) the normative acceptance rule.** Every closure method must state how it
   meets clauses 1–4.
3. **Add provenance classes (§4) to `TestExecutionReport`** (`verification_config_digest`, `provenance`). Only
   `KRIYA_CONTROLLED` reports are closure-eligible.
4. **B2.** Run in a separate oracle gate with Kriya-controlled runner configuration; Python first (`--confcutdir`
   to the Kriya-owned directory). JVM B2 needs a design of its own (test classpath/compile).
5. **B3.** The approval also binds the verification-configuration and dependency-set digests.
6. **Runtime assertions authored by a model** are MODEL_CLAIMED even when written before generation (state it
   explicitly).
7. **R1.** The baseline inventory is part of C0 and is reused by Rule E.

## 7. Recommended closure sources (ordered)

1. **Existing independent analyses:** mutation scope and the migration gate (no change; already INDEPENDENT).
2. **C0: guarded named pre-existing tests.** Dependency set + expected identities + KRIYA_CONTROLLED provenance.
   Fixes the confirmed §1 path.
3. **B2: executable examples in the goal / operator acceptance file,** in a Kriya-controlled oracle gate.
4. **B3: human-bound acceptance tests.** Human authority, digest-bound to the oracle, its dependency set and the
   verification configuration.
5. **Static analysis / compiler evidence** only for requirements that are literally rule or signature statements
   (none of today's derived requirements qualify).
6. **Never closure:** candidate-written tests, model-generated tests, model judgments.

## 8. T5: how it could become deterministically closable

T5's single requirement is behavioural ("returns true when the owner has a pet with that name (matched the same way
as `getPet`) and false otherwise") plus "add a unit test".
- **B2.** The operator states examples, e.g. `owner with pet "Leo": hasPet("leo") is True; hasPet("Max") is False`.
  These close REQ-1 when they execute and pass in the Kriya-controlled gate (JVM B2 needed for T5).
- **B3.** A human approves the candidate's two added `OwnerTests` methods as acceptance for REQ-1, digest-bound,
  in human-in-the-loop mode.
- **Not available to T5:**
  - C0: no pre-existing test names `hasPet`; `OwnerTests` was changed;
  - static/compiler evidence: it proves only the signature;
  - candidate tests alone.

## 9. Status

```text
FS-1C DESIGN                NEEDS CHANGE (§6), core direction sound
NEW CONFIRMED FINDING       named-test closure accepts a candidate-controlled oracle dependency (§1) - reproducer
                            pinned, required-behaviour test failing; unfixed (Track A: no production code)
PRODUCTION CODE CHANGED     NO
```
