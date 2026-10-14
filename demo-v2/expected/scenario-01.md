# Scenario 01 - expected evidence

What `verify_demo.sh --scenario 01 --phase post` checks, and what it deliberately does not.

## Checked (deterministic, outcome-only)
| Check | Why |
|---|---|
| exactly one file changed, and it is `FIXTURE_TARGET_FILE` | the goal forbids touching any other file |
| `delete(Long)` body now contains a repository persistence call (`save` or `saveAndFlush`) | the bug is that nothing persists the deleted flag; the file's `create()` already calls `save`, so the check is scoped to the one method |
| the diff is narrow (at least one added line, at most one removed) | one-method fix |
| an independent offline `./mvnw -o clean test` reports `Failures: 0, Errors: 0` | a second confirmation outside Kriya's own gates |
| the scenario state directory is non-empty | the change came from a Kriya run, not from hand editing (a hand-applied fix passes every other check and fails this one - measured) |

## Baseline facts measured on `1064b9e` (no model)
- fixture commit `0d7a09136cfec085156308d706d53b091ecd3cde`, target file sha256 `a15b6ba2...944bb`;
- offline compile succeeds and `Tests run: 53, Failures: 0, Errors: 0, Skipped: 0` in about 13 s.

## NOT checked, on purpose
- the number of attempts, the model's wording, the exact statement chosen (`save` vs `saveAndFlush`), or run duration: these are model variance;
- that the run succeeds. A failed run is a result to classify and keep, not something the script can turn green.

## Expected in the GUI (observed by the presenter; not scripted)
Run Setup shows the routing; the run shows plan, unit and attempt records; the change review shows the diff and is decided by a person; Evidence shows
per-attempt records and verifies.
