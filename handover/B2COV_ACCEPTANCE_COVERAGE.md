# B2-COV: acceptance coverage semantics - evidence closes a claim only at the strength it demonstrates

**Branch:** `feature/lr-r1-b2a`. **Implementation:** `d7a376e`, tests strengthened `74d745f` (certified revision).
**Trigger:** the post-B2-a A2 live run (`handover/evidence/b2a/a2-b2-live/A2-run/A2_RESULT.md`): three passing
operator cases closed "accepts a string offset of the form "+HH:MM" or "-HH:MM" ... raising ValueError for any other
string"; the candidate accepts "+5:30". No live model run in this change. Nothing pushed or merged.
**Labels:** MEASURED, TRACED, INFERRED.

## Rule

A trusted counterexample can disprove a general rule; finite passing examples cannot prove one.

## Implementation (TRACED)

- `requirements.behavior_strength(text, regression_covered=)` - deterministic, from the user's words only:
  - **EXACT** (enumerated): the statement states concrete example calls (`f(literal, ...)`) and no behaviour clause has a
    universal word (`any every all each only never always whatever whichever arbitrary regardless otherwise none
    nothing`) or phrase (`of the form`, `no other`, `for any/all/every`, `in any/all`, `match`, `pattern`, `range`,
    `between`, `at least/most`, `up to`), a `<placeholder>`, or a formula over a declared parameter
    (`double(x) ... returns 2 * x`).
  - **GENERAL**: anything else, including no concrete example (uncertain = GENERAL).
  - Quoted text, code spans, example calls and signatures are content, never quantifiers. A preservation clause ("keeps
    working exactly as before", "keeping T passing") belongs to the regression claim; when no regression claim covers
    it, the statement is GENERAL.
- Producer (`acceptance_oracle.close_requirements_with_acceptance`): VIOLATED stays VIOLATED for any strength (one
  counterexample suffices). PASSED closes BEHAVIOR only when EXACT; on GENERAL it is `ACCEPTANCE_GENERAL_RULE_UNPROVEN`:
  BEHAVIOR recorded INDETERMINATE, the passing cases recorded as the supporting `BEHAVIOR_EXAMPLES` claim (never a
  closure claim). Every record carries `strength` and `strength_reasons`.
- Read time (`_effective_closure` -> `_finite_evidence_may_close`): any finite-evidence (`acceptance_oracle`) BEHAVIOR
  claim or whole closure is re-judged from the requirement text, so a resumed pre-B2-COV record cannot close a general
  rule. FS-1C1 (claims, not sentences) unchanged; B3 (human acceptance authority, `human_bound_tests`) is not finite
  evidence and is not implemented.

A1 vs A2 (MEASURED, `test_a1_is_enumerated_and_a2_is_a_general_rule`): A1 = EXACT (examples `freeze_time(0)`,
`freeze_time(86400.5)`; its "every input it already accepts keeps working" is a preservation clause covered by C0);
A2 = GENERAL ("of the form", "any other string").

## Original symptom (MEASURED, `evidence/b2cov/a2_replay_pin.py.txt` + output)

The preserved A2 candidate (vendored, digest = live applied `b41e90a5...`), its three operator cases (real runner),
live C0 judgment, mutation scope:
- `b8809cd` (pre-fix): `ACCEPTANCE_PASSED`; REQ-1 closed_by_evidence; nothing blocks -> the live false success.
- B2-COV: `ACCEPTANCE_GENERAL_RULE_UNPROVEN`; REQ-1 unverified; REQ-2 closed; production blocking [REQ-1]
  -> `REQUIREMENTS_UNRESOLVED`, no commit/apply.

## Tests (`tests/test_b2cov_claim_strength.py`, 35; real acceptance runs)

1 exact + all cases pass -> closed · 2 exact + one case fails -> VIOLATED · 3 general + finite passes -> UNVERIFIED
(supporting examples recorded) · 4 general + one counterexample ("+5:30" on the live A2 candidate) -> VIOLATED ·
5 compound: examples proven, preservation proven, rule open · 6 uncertain -> GENERAL, cannot close · 7 model SATISFIED
cannot upgrade · 8 candidate/named-test/model producers cannot record behaviour or examples; examples never stand in
for behaviour · 9 C0 cannot upgrade (A2 regression closed, REQ-1 open) · 10 resumed pre-B2-COV claim or whole closure
closes nothing; a record without the regression claim cannot cover a preservation clause; the same record still closes
an enumerated statement · 11 A1 still closes (acceptance + C0 + scope) · 12 preserved A2 candidate blocked · classifier
table (14 statements). Plus a direct-workflow E2E: a `<name>` goal with passing cases is not applied.

Fixture changes (semantic, recorded): B2-a/FS-1C1 closure fixtures restated as enumerated statements
(`double(5) returns 10`, `greet('Ann') returns 'Hello, Ann'`, `scale(3) returns 6`); four PRD-020 mechanics tests use the
reserved B3 method as their generic closure stand-in; the milestone fixture's "m1.py defines VALUE" is GENERAL now
(passing cases are supporting only; a wrong value is still VIOLATED).

## Certification on `74d745f` (MEASURED)

| Gate | Result |
|---|---|
| B2-COV mutation run 1 (`d7a376e`) | 14 run, 12 killed, 2 survived (`placeholder-ignored`, `regression-coverage-assumed`): test gaps, fixed in `74d745f` |
| B2-COV mutation run 2 (`74d745f`) | **14 run, 14 killed** |
| B2-a mutation run 3 (`74d745f`) | 25 exercised, 24 killed, 1 equivalent/non-semantic survivor (`--noconftest`), 0 meaningful survivors |
| ruff / pylint | 0 / exit 0 |
| full suite (`full_suite_74d745f.txt`) | **9024 passed, 0 failed, 0 errors** (covers FS-1C1, B2-a, C0, FS-1A/B, requirement/terminal, M1, P1/P4/P5, P2, P3-A/B/C, I-2) |

## Residuals and judgment calls

- The classifier is lexical and conservative: an implicit universal without any cue word, placeholder, parameter
  formula or missing example (e.g. "the parser handles timestamps, so that parse(0) returns ...") is EXACT. The cue
  list errs broad (`match`, `range`, `between` make a statement GENERAL even when used loosely) - the safe direction.
- A statement GENERAL only because it has no concrete example (e.g. "m1.py defines VALUE") can no longer close from
  acceptance cases; it needs B3 or a goal restated with examples.
- Deferred (owner): acceptance PASS + model VIOLATED verdict is blocked (negative model authority).
