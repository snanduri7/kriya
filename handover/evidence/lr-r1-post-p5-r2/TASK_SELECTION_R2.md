# LR-R1 post-P5 live success validation R2: task selection (recorded before any model run)

**Recorded:** 2026-10-05, after the baseline admission gate and before any model call of R2.
**Code under test:** `feature/lr-r1-p5` @ `6530138` (M1 + P1 + LV-2 + P4 + P5), the same `venv-p5` build as R1 (clean
clone; dirty false).
**Configuration:** the v5 production operator config; each workspace config changes only
`paths.skills/state/memory`. Owner-approved under SEC-009; every approval verified CURRENT.
**Rules:** 5 tasks, each run once in this order. No rerun, no goal rewrite, no gate change, no per-task config or
model change, no manual repair.

## Baseline admission gate (no model; `admission/*.json`, harness `admission/r2_admission.py`)

**How the gate ran.** Each candidate's full regression suite was run at its exact base, exactly as Kriya's own
brownfield baseline capture runs it: `PolymorphicValidator(workspace, original_workspace_path=workspace,
autonomy_cfg=cfg.autonomy).run_tests()` under the candidate's production config. That is the contained environment
the task is judged in. The workspace was then restored pristine. **Admitted only when failed = 0 and errors = 0.**

| Candidate | Repository @ commit | Command (contained) | Passed | Failed | Errors | Skipped / xfail | Duration | Gate |
|---|---|---|---|---|---|---|---|---|
| r2c-py-inflect | jaraco/inflect @ 262a247d2d | `.kriya/venv/bin/python -c "…pytest.main…"` (venv: pyproject deps + pytest) | 214 | 0 | 0 | 16 xfailed | 15.9 s | **GREEN** |
| r2c-py-cssselect2 | Kozea/cssselect2 @ dc2690c6b4 | same form | 446 | 0 | 0 | 9 xfailed | 13.3 s | **GREEN** |
| r2c-java-lang-a | apache/commons-lang @ 4ee346e59e | `mvn -B -o -Dmaven.repo.local=/kriya/cache/m2 test` | 22,023 | 0 | 0 | 19 | 327.7 s | **GREEN** |
| r2c-java-lang-b | apache/commons-lang @ 4ee346e59e | same | 22,023 | 0 | 0 | 19 | 329.4 s | **GREEN** |
| r2c-java-cli | apache/commons-cli @ d95484f57d | same | 933 | 0 | 0 | 61 | 37.7 s | GREEN (backup, not selected) |
| r2c-spring-xml | spring-petclinic/spring-framework-petclinic @ 09351b3ee0 | same | 75 | 0 | 0 | 0 | 15.0 s | **GREEN** |
| r2c-py-httpx | encode/httpx @ b5addb64f0 | venv (requirements.txt + pytest) | 1413 | **4** | 0 | 1 | 64.6 s | RED: rejected |
| r2c-py-moreit | more-itertools @ 1ea82a711c | `python3 -c "…import pytest…"` (no requirements.txt / no pyproject runtime deps, so no venv) | n/a | n/a | n/a | n/a | 1.3 s | RED: rejected (`ModuleNotFoundError: No module named 'pytest'`; the suite cannot run under Kriya) |
| r2c-py-tomli | hukkin/tomli @ 8479ed2902 | same as more-itertools | n/a | n/a | n/a | n/a | 0.7 s | RED: rejected (same) |

- **Not admitted, not repaired.** No candidate was modified to become eligible. tinycss2 was dropped before admission:
  its tests need a git submodule that is not part of the cloned tree.
- spring-petclinic @ `500158f` was not re-admitted: its contained baseline was measured red in R1 (1 error / 80).

## Selected tasks (all GREEN; none from R1, the CAGC matrix or any earlier LR-R1 run; each feature confirmed absent)

| ID | Language / framework | Workspace @ base | Goal |
|---|---|---|---|
| T1 | Python | r2c-py-inflect @ 262a247d2d | Add a method `count_noun(self, count, noun)` to the `inflect.engine` class in inflect/__init__.py that returns the count followed by a space and the noun inflected for that count with the engine's own `plural` method (for example `count_noun(1, "cat")` returns "1 cat" and `count_noun(3, "cat")` returns "3 cats"), and add tests for it in a new file tests/test_count_noun.py. |
| T2 | Python | r2c-py-cssselect2 @ dc2690c6b4 | Add a read-only property `depth` to `cssselect2.tree.ElementWrapper` in cssselect2/tree.py that returns the number of ancestors of the element (0 for the root element), and add tests for it in tests/test_cssselect2.py. |
| T3 | Java | r2c-java-lang-a @ 4ee346e59e | Add `public static boolean containsOnly(final String str, final String... set)` to org.apache.commons.lang3.CharSetUtils that returns true when every character of str is in the character set defined by the set strings (the same set syntax as the existing `CharSetUtils.containsAny`), returns true for a null or empty str, and returns false for a non-empty str when set is null or empty, and add unit tests for it in CharSetUtilsTest. |
| T4 | Java | r2c-java-lang-b @ 4ee346e59e | Add `public static int[] fill(final int[] a, final int fromIndex, final int toIndex, final int val)` to org.apache.commons.lang3.ArrayFill, mirroring the existing `fill(char[], int, int, char)` overload (fill the range with `Arrays.fill`, return the given array, and return null for a null array), and add unit tests for it in ArrayFillTest. |
| T5 | Spring XML | r2c-spring-xml @ 09351b3ee0 | Add a method `public boolean hasPet(String name)` to org.springframework.samples.petclinic.model.Owner that returns true when the owner has a pet with that name (matched the same way as the existing `getPet(String name)`) and false otherwise, and add a unit test for it in OwnerTests. |
