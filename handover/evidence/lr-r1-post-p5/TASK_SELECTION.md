# LR-R1 post-P5 live success validation: task selection (recorded before any model run)

**Recorded:** 2026-10-05, before any model call of this validation.
**Code under test:** feature/lr-r1-p5 @ 6530138 (M1 + P1 + LV-2 + P4 + P5), installed from a clean clone.
**Rules:** 5 tasks, run once each in this order, no reruns, no task change after a result, no gate change, no manual
repair.

**How they were chosen.** Small additive features with tests on the benchmark task bases, whose build environments
already work locally:
- each feature was confirmed absent by grep, and its test file confirmed present;
- no task is from the frozen CAGC-v2 matrix or the LR-R1 Stage-1/P1 runs;
- none was chosen from knowledge of a previous Kriya outcome on that goal (none exists);
- none was designed to trigger P2/P3.

| ID | Language / framework | Repository @ base | Goal |
|---|---|---|---|
| T1 | Python | httpx @ 3de1915 | Add a method `Headers.count(key)` to httpx/_models.py that returns how many values are present for the given header name (case-insensitive), 0 when the header is absent, and add tests for it in tests/models/test_headers.py. |
| T2 | Python | more-itertools @ 0054e02 | Add a function `count_unique(iterable, key=None)` to more_itertools/more.py that returns the number of distinct items in the iterable (distinct by `key(item)` when a key function is given), add it to the module's `__all__`, and add tests for it in tests/test_more.py. |
| T3 | Java | commons-lang @ 5cba51c7e | Add `public static boolean isAsciiWhitespace(final char ch)` to org.apache.commons.lang3.CharUtils that returns true for ' ', '\t', '\n', '\u000B', '\f' and '\r' and false for every other character, and add unit tests for it in CharUtilsTest. |
| T4 | Java | commons-lang @ 5cba51c7e | Add `public static int countTrue(final boolean... array)` to org.apache.commons.lang3.BooleanUtils that returns the number of true values in the array and throws NullPointerException for a null array, and add unit tests for it in BooleanUtilsTest. |
| T5 | Spring Boot | spring-petclinic @ 500158f | Add a method `public int getPetCount()` to org.springframework.samples.petclinic.owner.Owner that returns the number of pets the owner has, and add a unit test for it in OwnerTests. |
